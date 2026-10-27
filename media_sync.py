"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import contextlib
import datetime
import glob
import json
import os
import pathlib
import shutil
import sqlite3
import tempfile
import time
import typing
from mergin import MerginClient, MerginProject, LoginError, ClientError
from mergin.client import decode_token_data, TokenError
from mergin.common import SYNC_ATTEMPTS, SYNC_ATTEMPT_WAIT

from version import __version__
from drivers import DriverError, create_driver
from config import config, validate_config, ConfigError

# auth token with less validity left (in seconds) is not reused and new login is done instead
TOKEN_MIN_VALIDITY = 3600


class MediaSyncError(Exception):
    pass


def _quote_identifier(identifier):
    """Quote identifiers"""
    return '"' + identifier + '"'


def _get_project_version():
    """Returns the current version of the project"""
    mp = MerginProject(config.project_working_dir)
    return mp.version()


def _check_has_working_dir():
    if not os.path.exists(config.project_working_dir):
        raise MediaSyncError(
            "The project working directory does not exist: "
            + config.project_working_dir
        )

    if not os.path.exists(os.path.join(config.project_working_dir, ".mergin")):
        raise MediaSyncError(
            "The project working directory does not seem to contain Mergin project: "
            + config.project_working_dir
        )


def _get_untracked_files(mp):
    """Returns files (relative paths) in the working directory which are not project files"""
    project_files = {f["path"] for f in mp.files()}
    untracked = set()
    for root, dirs, files in os.walk(config.project_working_dir):
        dirs[:] = [d for d in dirs if d != ".mergin"]
        for f in files:
            if mp.ignore_file(f):
                continue
            path = os.path.relpath(os.path.join(root, f), config.project_working_dir)
            path = pathlib.Path(path).as_posix()
            if path not in project_files:
                untracked.add(path)
    return untracked


def _check_pending_changes():
    """Check working directory contains only changes left by an interrupted sync (e.g. failed push),
    otherwise it was probably modified manually.
    """
    mp = MerginProject(config.project_working_dir)
    status_push = mp.get_push_changes()
    reference_files = {ref.file for ref in config.references if ref.file}
    unexpected_updated = [
        f for f in status_push["updated"] if f["path"] not in reference_files
    ]
    # media files are removed only in the move mode
    removed_media = (
        _get_media_sync_files(status_push["removed"])
        if config.operation_mode == "move"
        else []
    )
    if (
        status_push["added"]
        or unexpected_updated
        or len(removed_media) != len(status_push["removed"])
    ):
        raise MediaSyncError(
            "There are pending changes in the local directory - please review and push manually! "
            + str(status_push)
        )


def _get_media_sync_files(files):
    """Return files relevant to media sync from project files"""
    allowed_extensions = config.allowed_extensions
    files_to_upload = [
        f
        for f in files
        if os.path.splitext(f["path"])[1].lstrip(".") in allowed_extensions
    ]
    # filter out files which are not under particular directory in mergin project
    if "base_path" in config and config.base_path:
        filtered_files = [
            f for f in files_to_upload if f["path"].startswith(config.base_path)
        ]
        files_to_upload = filtered_files
    return files_to_upload


def _get_include_filter():
    """Returns sparse checkout include filter (glob patterns), so that only files relevant
    to media sync are downloaded - media files and geopackages with references"""
    base_path = glob.escape(config.base_path) if config.get("base_path") else ""
    include = [f"{base_path}*.{glob.escape(ext)}" for ext in config.allowed_extensions]
    include += [glob.escape(ref.file) for ref in config.references if ref.file]
    return sorted(set(include))


def _token_expires_soon(expire: typing.Optional[datetime.datetime]) -> bool:
    if expire is None:
        return True
    delta = expire - datetime.datetime.now(datetime.timezone.utc)
    return delta.total_seconds() < TOKEN_MIN_VALIDITY


class AuthTokenStore:
    """Stores Mergin Maps auth token, so it can be reused when media sync is restarted instead of new login.

    The token is stored in the parent directory of the project working directory, so it is kept when
    the project is downloaded again and it can be shared by multiple projects synced by the same user
    """

    def __init__(self, path: pathlib.Path, url: str, username: str):
        self.path = path
        self.url = url
        self.username = username

    @classmethod
    def from_config(cls) -> "AuthTokenStore":
        project_dir = pathlib.Path(config.project_working_dir).absolute()
        return cls(
            project_dir.parent / ".mergin_auth.json",
            config.mergin.url,
            config.mergin.username,
        )

    def load(self) -> typing.Optional[str]:
        """Returns stored token if it was issued for this server and user and it is not about to expire"""
        try:
            with open(self.path) as f:
                data = json.load(f)
            if data["url"] != self.url or data["username"] != self.username:
                return None
            token = data["token"]
            expire = datetime.datetime.fromisoformat(decode_token_data(token)["expire"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, TokenError) as e:
            print(f"Unable to read stored Mergin Maps auth token: {e}")
            return None

        if _token_expires_soon(expire):
            return None
        return token

    def save(self, mc: MerginClient) -> None:
        """Stores auth token of the client"""
        token = (mc._auth_session or {}).get("token")
        if not token:
            return
        tmp_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # write a new file readable only by the owner (on POSIX systems) and replace the stored one with it,
            # so permissions of the existing file (or symlink target) are not reused and the file is never partially written
            fd, tmp_path = tempfile.mkstemp(
                dir=self.path.parent, prefix=self.path.name, suffix=".tmp"
            )
            with open(fd, "w") as f:
                json.dump(
                    {"url": self.url, "username": self.username, "token": token}, f
                )
            os.replace(tmp_path, self.path)
        except OSError as e:
            if tmp_path:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)
            print(f"Unable to store Mergin Maps auth token: {e}")

    def remove(self) -> None:
        self.path.unlink(missing_ok=True)


def auth_token_expires_soon(mc: MerginClient) -> bool:
    return _token_expires_soon((mc._auth_session or {}).get("expire"))


def auth_token_rejected(mc: MerginClient) -> bool:
    """Checks whether the server rejects auth token of the client although it has not expired yet.
    Other errors (e.g. server unavailable) are ignored."""
    try:
        mc.user_info()
    except ClientError as e:
        return e.http_error == 401
    except Exception:
        pass
    return False


def _create_mergin_client_from_stored_token() -> typing.Optional[MerginClient]:
    """Creates MerginClient using stored auth token, returns None if there is no valid stored token"""
    token_store = AuthTokenStore.from_config()
    token = token_store.load()
    if not token:
        return None

    mc = MerginClient(
        config.mergin.url,
        auth_token=token,
        login=config.mergin.username,
        password=config.mergin.password,
        plugin_version=f"media-sync/{__version__}",
    )
    if auth_token_rejected(mc):
        print("Stored Mergin Maps auth token was rejected by the server")
        token_store.remove()
        return None

    print("Using stored Mergin Maps auth token")
    return mc


def create_mergin_client():
    """Create instance of MerginClient, reusing stored auth token if possible"""
    try:
        mc = _create_mergin_client_from_stored_token()
        if mc is None:
            mc = MerginClient(
                config.mergin.url,
                login=config.mergin.username,
                password=config.mergin.password,
                plugin_version=f"media-sync/{__version__}",
            )
            AuthTokenStore.from_config().save(mc)
        return mc
    except LoginError as e:
        # this could be auth failure, but could be also server problem (e.g. worker crash)
        raise MediaSyncError(
            f"Unable to log in to Mergin: {str(e)} \n\n"
            + "Have you specified correct credentials in configuration file?"
        )
    except ClientError as e:
        # this could be e.g. DNS error
        raise MediaSyncError("Mergin client error: " + str(e))


def mc_download(mc):
    """Clone mergin project to local dir
    :param mc: mergin client instance
    :return: list(dict) list of project files metadata
    """
    print("Downloading project from Mergin server ...")
    if os.path.exists(config.project_working_dir):
        raise MediaSyncError(
            "The project working directory already exists: "
            + config.project_working_dir
        )
    try:
        mc.download_project(
            config.mergin.project_name,
            config.project_working_dir,
            include=_get_include_filter(),
        )
    except Exception as e:
        # remove partially downloaded project, otherwise it would be pulled next time instead of new download
        shutil.rmtree(config.project_working_dir, ignore_errors=True)
        if isinstance(e, (ClientError, LoginError)):
            # this could be e.g. DNS error
            raise MediaSyncError("Mergin client error on download: " + str(e))
        raise
    mp = MerginProject(config.project_working_dir)
    print(f"Downloaded {_get_project_version()} from Mergin")
    files_to_upload = _get_media_sync_files(mp.inspect_files())
    return files_to_upload


def mc_pull(mc):
    """Pull latest version to synchronize with local dir
    :param mc: mergin client instance
    :return: list(dict) list of project files metadata
    """
    print("Pulling from mergin server ...")
    # changes left by interrupted sync (e.g. failed push) are rebased on server changes and pushed
    _check_pending_changes()
    if mc.has_unfinished_pull(config.project_working_dir):
        # previous pull failed in the final stage (e.g. locked file), resolving it would only move
        # local changes to conflicted copies, so it is checked above they are made by media sync
        print("Previous pull was not finished, downloading project again ...")
        return _download_again(mc)

    mp = MerginProject(config.project_working_dir)
    if mp.file_filter().get("include") != _get_include_filter():
        # configuration has changed (or project was downloaded without filter by older version)
        print(
            "Files to sync have changed in configuration, downloading project again ..."
        )
        return _download_again(mc)

    local_version = mp.version()
    files_to_upload = _pull_changes(mc)

    version = _get_project_version()
    # server version has not changed since last pull
    if version == local_version:
        print("No changes on Mergin.")
    else:
        print("Pulled new version from Mergin: " + version)

    return files_to_upload + _push_changes(mc)


def _download_again(mc):
    """Remove working directory and download project again. Local changes are made only by media sync
    (see _check_pending_changes) and they are recreated - all media files are synced again.
    :param mc: mergin client instance
    :return: list(dict) list of project files metadata
    """
    shutil.rmtree(config.project_working_dir)
    return mc_download(mc)


def _pull_changes(mc):
    """Pull latest version, local changes are rebased on top of server changes.
    If rebase fails, project is downloaded again.
    :param mc: mergin client instance
    :return: list(dict) media files to be synced
    """
    untracked_files = _get_untracked_files(MerginProject(config.project_working_dir))
    try:
        # pull changes are limited to files in sparse checkout filter
        status_pull, _, _ = mc.project_status(config.project_working_dir)
        # pull even if there are no relevant changes, to keep local version up to date for push
        conflicts = mc.pull_project(config.project_working_dir)
    except (ClientError, LoginError) as e:
        raise MediaSyncError("Mergin client error on pull: " + str(e))
    if conflicts:
        print(
            "Warning: unable to rebase local changes on server changes (conflicts: "
            + str(conflicts)
            + "), downloading project again ..."
        )
        return _download_again(mc)
    # other new files than conflicted copies are edit conflict files - rebase was successful,
    # but the same feature was modified also on server and local changes won
    new_edit_conflicts = sorted(
        _get_untracked_files(MerginProject(config.project_working_dir))
        - untracked_files
    )
    # in the move mode, local removal of a reference wins over the reference to a new file set on server,
    # the new file is synced, but no feature references it
    if new_edit_conflicts and config.operation_mode == "move":
        print(
            "Warning: features modified also on server were overwritten by media sync, "
            "please check references to media files manually: "
            + str(new_edit_conflicts)
        )
    return _get_media_sync_files(status_pull["added"] + status_pull["updated"])


def _push_changes(mc):
    """Push changes to mergin back (with changed references and removed files) if applicable.
    If server has a newer version, pull it (rebase) and try again.
    :param mc: mergin client instance
    :return: list(dict) media files pulled from server before successful push
    """
    files_to_upload = []
    for attempt in range(1, SYNC_ATTEMPTS + 1):
        status_push = MerginProject(config.project_working_dir).get_push_changes()
        if status_push["added"]:
            raise MediaSyncError(
                "Mergin client error on push: There are changes to be added - it should never happen"
            )
        # nothing to push (also when project was downloaded again on pull)
        if not (status_push["updated"] or status_push["removed"]):
            return files_to_upload
        try:
            mc.push_project(config.project_working_dir)
            print("Pushed new version to Mergin: " + _get_project_version())
            return files_to_upload
        except (ClientError, LoginError) as e:
            # this could be either because of some temporal error (network, server lock)
            # or permanent one that needs to be resolved by user
            if (
                not isinstance(e, ClientError)
                or not e.is_retryable_sync()
                or attempt == SYNC_ATTEMPTS
            ):
                raise MediaSyncError("Mergin client error on push: " + str(e))
            print(f"Unable to push ({e}), going to pull and try again ...")
        time.sleep(SYNC_ATTEMPT_WAIT)
        files_to_upload += _pull_changes(mc)


def _update_references(files):
    """Update references to media files in reference table"""
    for ref in config.references:
        reference_config = [
            ref.file,
            ref.table,
            ref.local_path_column,
            ref.driver_path_column,
        ]
        if not all(reference_config):
            return

        gpkg_path = os.path.join(config.project_working_dir, ref.file)
        if not os.path.exists(gpkg_path):
            raise MediaSyncError(f"Reference file not found in project: {ref.file}")

        print("Updating references ...")
        try:
            gpkg_conn = sqlite3.connect(gpkg_path)
            gpkg_conn.enable_load_extension(True)
            gpkg_cur = gpkg_conn.cursor()
            gpkg_cur.execute('SELECT load_extension("mod_spatialite")')
            for file_path, dest in files.items():
                # remove reference to the local path only in the move mode
                if config.operation_mode == "move":
                    sql = (
                        f"UPDATE {_quote_identifier(ref.table)} "
                        f"SET {_quote_identifier(ref.driver_path_column)}=:dest_column, {_quote_identifier(ref.local_path_column)}=Null "
                        f"WHERE {_quote_identifier(ref.local_path_column)}=:file_path"
                    )
                elif config.operation_mode == "copy":
                    sql = (
                        f"UPDATE {_quote_identifier(ref.table)} "
                        f"SET {_quote_identifier(ref.driver_path_column)}=:dest_column "
                        f"WHERE {_quote_identifier(ref.local_path_column)}=:file_path"
                    )
                gpkg_cur.execute(sql, {"dest_column": dest, "file_path": file_path})
            gpkg_conn.commit()
            gpkg_conn.close()
        except sqlite3.OperationalError as e:
            raise MediaSyncError("SQLITE error: " + str(e))


def media_sync_push(mc, driver, files):
    if not files:
        return
    _check_has_working_dir()
    # media files pulled from server when push is retried need to be synced too
    while files:
        _sync_files(driver, files)
        files = _push_changes(mc)

    print("Sync finished")


def _sync_files(driver, files):
    """Upload media files to external drive and update references to them"""
    print("Synchronizing files with external drive...")
    migrated_files = {}

    # TODO make async and parallel for better performance
    for file in files:
        src = os.path.join(config.project_working_dir, file["path"])
        if not os.path.exists(src):
            print("Missing local file: " + str(file["path"]))
            continue

        try:
            size = os.path.getsize(src) / 1024 / 1024  # file size in MB
            print(f"Uploading {file['path']} of size {size:.2f} MB")
            dest = driver.upload_file(src, file["path"])
            migrated_files[file["path"]] = dest
        except DriverError as e:
            print(f"Failed to upload {file['path']}: " + str(e))
            continue

    # update reference table (if applicable)
    _update_references(migrated_files)

    # remove from local dir if move mode
    if config.operation_mode == "move":
        for file in migrated_files.keys():
            src = os.path.join(config.project_working_dir, file)
            os.remove(src)


def main():
    print(f"== Starting Mergin Media Sync version {__version__} ==")
    try:
        validate_config(config)
    except ConfigError as e:
        print("Error: " + str(e))
        return

    try:
        driver = create_driver(config)
    except DriverError as e:
        print("Error: " + str(e))
        return

    try:
        print("Logging in to Mergin...")
        mc = create_mergin_client()
        # initialize or pull changes to sync with latest project version
        if os.path.exists(config.project_working_dir):
            files_to_sync = mc_pull(mc)
        else:
            files_to_sync = mc_download(mc)

        if not files_to_sync:
            print("No files to sync")
            return

        # sync media files with external driver
        media_sync_push(mc, driver, files_to_sync)
        print("== Media sync done! ==")
    except MediaSyncError as err:
        print("Error: " + str(err))


if __name__ == "__main__":
    main()
