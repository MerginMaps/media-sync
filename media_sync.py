"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import os
import sqlite3
import shutil
from mergin import MerginClient, MerginProject, LoginError, ClientError

from version import __version__
from drivers import DriverError, create_driver
from config import config, validate_config, ConfigError


class MediaSyncError(Exception):
    pass


class VersionConflictError(MediaSyncError):
    pass


def _quote_identifier(identifier):
    """Quote identifiers"""
    return '"' + identifier + '"'


def _get_project_version(workspace_path=None):
    """Returns the current version of the project"""
    path = workspace_path if workspace_path else config.project_working_dir
    mp = MerginProject(path)
    return mp.version()


def _get_server_version(mc, project_full_name):
    """Get current server version without modifying local state"""
    projects = mc.get_projects_by_names([project_full_name])
    return projects[project_full_name]["version"]


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


def _check_pending_changes(workspace_path=None):
    """Check working directory was not modified manually - this is probably uncommitted change from last attempt"""
    path = workspace_path if workspace_path else config.project_working_dir
    mp = MerginProject(path)
    status_push = mp.get_push_changes()
    if status_push["added"] or status_push["updated"] or status_push["removed"]:
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


def create_mergin_client():
    """Create instance of MerginClient"""
    try:
        return MerginClient(
            config.mergin.url,
            login=config.mergin.username,
            password=config.mergin.password,
            plugin_version=f"media-sync/{__version__}",
        )
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
    try:
        mc.download_project(config.mergin.project_name, config.project_working_dir)
    except ClientError as e:
        # this could be e.g. DNS error
        raise MediaSyncError("Mergin client error on download: " + str(e))
    mp = MerginProject(config.project_working_dir)
    print(f"Downloaded {_get_project_version()} from Mergin")
    files_to_upload = _get_media_sync_files(mp.inspect_files())
    return files_to_upload


def mc_pull(mc, workspace_path=None):
    """Pull latest version to synchronize with local dir
    :param mc: mergin client instance
    :param workspace_path: Optional workspace path, defaults to config.project_working_dir
    :return: list(dict) list of project files metadata
    """
    path = workspace_path if workspace_path else config.project_working_dir
    print("Pulling from mergin server ...")
    _check_pending_changes(path)

    mp = MerginProject(path)
    local_version = mp.version()

    try:
        project_info = mc.project_info(mp.project_full_name(), since=local_version)
        projects = mc.get_projects_by_names([mp.project_full_name()])
        server_version = projects[mp.project_full_name()]["version"]
    except ClientError as e:
        # this could be e.g. DNS error
        raise MediaSyncError("Mergin client error: " + str(e))

    _check_pending_changes(path)

    if server_version == local_version:
        print("No changes on Mergin.")
        return

    try:
        status_pull = mp.get_pull_changes(project_info["files"])
        mc.pull_project(path)
    except ClientError as e:
        raise MediaSyncError("Mergin client error on pull: " + str(e))

    print("Pulled new version from Mergin: " + _get_project_version(path))
    files_to_upload = _get_media_sync_files(
        status_pull["added"] + status_pull["updated"]
    )
    return files_to_upload


def _update_references(files, workspace_path=None):
    """Update references to media files in reference table"""
    path = workspace_path if workspace_path else config.project_working_dir
    for ref in config.references:
        reference_config = [
            ref.file,
            ref.table,
            ref.local_path_column,
            ref.driver_path_column,
        ]
        if not all(reference_config):
            return

        print("Updating references ...")
        try:
            gpkg_conn = sqlite3.connect(
                os.path.join(path, ref.file)
            )
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


def media_sync_push(mc, driver, files, workspace_path=None):
    if not files:
        return
    path = workspace_path if workspace_path else config.project_working_dir
    print("Synchronizing files with external drive...")
    if not os.path.exists(path):
        raise MediaSyncError(
            "The project working directory does not exist: " + path
        )
    if not os.path.exists(os.path.join(path, ".mergin")):
        raise MediaSyncError(
            "The project working directory does not seem to contain Mergin project: " + path
        )
    migrated_files = {}

    # TODO make async and parallel for better performance
    for file in files:
        src = os.path.join(path, file["path"])
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
    _update_references(migrated_files, workspace_path=path)

    # remove from local dir if move mode
    if config.operation_mode == "move":
        for file in migrated_files.keys():
            src = os.path.join(path, file)
            os.remove(src)

    # push changes to mergin back (with changed references and removed files) if applicable
    try:
        mp = MerginProject(path)
        status_push = mp.get_push_changes()
        if status_push["added"]:
            raise MediaSyncError(
                "There are changes to be added - it should never happen"
            )
        if status_push["updated"] or status_push["removed"]:
            mc.push_project(path)
            version = _get_project_version(path)
            print("Pushed new version to Mergin: " + version)
    except ClientError as e:
        # Check if it's a version conflict
        error_msg = str(e).lower()
        if any(keyword in error_msg for keyword in ["version", "conflict", "newer version", "update required"]):
            raise VersionConflictError("Version conflict on push: " + str(e))
        raise MediaSyncError("Mergin client error on push: " + str(e))
    except MediaSyncError:
        raise

    print("Sync finished")


def create_attempt_workspace(baseline_path, server_version):
    """Create attempt workspace with hardlinks, break non-media hardlinks"""
    attempt_path = f"{baseline_path}_attempt_v{server_version}"
    
    # Remove existing attempt if it exists
    if os.path.exists(attempt_path):
        print(f"Removing existing attempt workspace: {attempt_path}")
        shutil.rmtree(attempt_path)
    
    print(f"Creating attempt workspace: {attempt_path}")
    
    # Create hardlink clone
    def link_file(src, dst):
        try:
            os.link(src, dst)
        except OSError as e:
            if e.errno == 18:  # EXDEV - cross-device link
                raise MediaSyncError(
                    "Hardlinks not supported (cross-filesystem). "
                    "Ensure baseline and attempt are on same filesystem."
                )
            raise
    
    # Copy directory structure with hardlinks
    def copy_with_hardlinks(src, dst):
        os.makedirs(dst, exist_ok=True)
        for item in os.listdir(src):
            src_path = os.path.join(src, item)
            dst_path = os.path.join(dst, item)
            
            if os.path.isdir(src_path):
                if item == ".mergin":
                    # .mergin must be fully copied, not hardlinked
                    shutil.copytree(src_path, dst_path)
                else:
                    copy_with_hardlinks(src_path, dst_path)
            else:
                try:
                    link_file(src_path, dst_path)
                except OSError as e:
                    if e.errno == 2:  # ENOENT - parent directory doesn't exist
                        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                        link_file(src_path, dst_path)
                    else:
                        raise
    
    copy_with_hardlinks(baseline_path, attempt_path)
    
    # Break hardlinks for non-media files
    break_hardlinks_for_non_media(attempt_path)
    
    return attempt_path


def break_hardlinks_for_non_media(attempt_path):
    """Break hardlinks for .gpkg, .mergin, and other non-media files"""
    print("Breaking hardlinks for non-media files...")
    
    allowed_extensions = [ext.lower() for ext in config.allowed_extensions]
    gpkg_files = [ref.file for ref in config.references if ref.file]
    
    for root, dirs, files in os.walk(attempt_path):
        # Skip .mergin directory (already copied, not hardlinked)
        if ".mergin" in root:
            continue
            
        for file in files:
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, attempt_path)
            
            # Get file extension
            _, ext = os.path.splitext(file)
            ext = ext.lstrip(".").lower()
            
            # Check if this is a non-media file that needs hardlink breaking
            is_media = ext in allowed_extensions
            is_gpkg = file in gpkg_files or file.endswith(".gpkg")
            
            # Break hardlink if: not a media file OR is a gpkg file
            if not is_media or is_gpkg:
                # Check if file has hardlinks
                try:
                    stat_info = os.stat(file_path)
                    if stat_info.st_nlink > 1:
                        # Break hardlink: read, delete, write
                        print(f"Breaking hardlink: {rel_path}")
                        with open(file_path, "rb") as f:
                            content = f.read()
                        os.remove(file_path)
                        with open(file_path, "wb") as f:
                            f.write(content)
                except OSError as e:
                    print(f"Warning: Could not break hardlink for {rel_path}: {e}")


def promote_attempt_to_baseline(baseline_path, attempt_path):
    """Rename attempt workspace to become baseline"""
    print(f"Promoting attempt workspace to baseline...")
    if os.path.exists(baseline_path):
        os.rename(baseline_path, baseline_path + "_old")
    os.rename(attempt_path, baseline_path)
    print("Promotion complete")


def sync_with_attempt_workspace(mc, driver, baseline_path):
    """Run sync in attempt workspace, retry on version conflict"""
    while True:
        # Get current server version
        mp = MerginProject(baseline_path)
        project_full_name = mp.project_full_name()
        server_version = _get_server_version(mc, project_full_name)
        
        # Create attempt workspace
        attempt_path = create_attempt_workspace(baseline_path, server_version)
        
        try:
            # Sync in attempt workspace
            files_to_sync = mc_pull(mc, workspace_path=attempt_path)
            
            if not files_to_sync:
                print("No files to sync")
                # Still need to promote (attempt is up to date)
                promote_attempt_to_baseline(baseline_path, attempt_path)
                break
            
            media_sync_push(mc, driver, files_to_sync, workspace_path=attempt_path)
            
            # Success! Promote attempt to baseline
            promote_attempt_to_baseline(baseline_path, attempt_path)
            break
            
        except VersionConflictError:
            # Push failed - cleanup attempt
            print("Version conflict detected, cleaning up attempt workspace...")
            if os.path.exists(attempt_path):
                shutil.rmtree(attempt_path)
            # Baseline is still clean (unchanged)
            # Loop continues: get new server version, create new attempt, retry
            continue
        except Exception as e:
            # Other error - cleanup attempt and re-raise
            print(f"Error during sync: {e}")
            if os.path.exists(attempt_path):
                shutil.rmtree(attempt_path)
            raise


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
        
        # Initialize if needed
        if not os.path.exists(config.project_working_dir):
            files_to_sync = mc_download(mc)
            if files_to_sync:
                media_sync_push(mc, driver, files_to_sync)
            print("== Media sync done! ==")
            return
        
        # COPY mode: always use attempt workspace
        if config.operation_mode == "copy":
            sync_with_attempt_workspace(mc, driver, config.project_working_dir)
        else:
            # MOVE mode: normal sync in baseline (unchanged behavior)
            files_to_sync = mc_pull(mc)
            if files_to_sync:
                media_sync_push(mc, driver, files_to_sync)
        
        print("== Media sync done! ==")
    except MediaSyncError as err:
        print("Error: " + str(err))


if __name__ == "__main__":
    main()
