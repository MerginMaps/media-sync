"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import os
import sqlite3
from mergin import MerginClient, MerginProject, LoginError, ClientError

from version import __version__
from drivers import DriverError, create_driver
from config import config, validate_config, ConfigError


class MediaSyncError(Exception):
    pass


def _quote_identifier(identifier):
    """Quote identifiers"""
    return '"' + identifier + '"'


def _project_working_dir(project):
    """Return the working directory for a specific project.

    The base directory from config is combined with the project name so that
    each project gets its own isolated sub-directory, e.g.:
        /tmp/mediasync/workspace/my_project
    """
    return os.path.join(config.project_working_dir, project.project_name)


def _get_project_version(project):
    """Returns the current version of the project"""
    mp = MerginProject(_project_working_dir(project))
    return mp.version()


def _check_has_working_dir(project):
    working_dir = _project_working_dir(project)
    if not os.path.exists(working_dir):
        raise MediaSyncError(
            "The project working directory does not exist: " + working_dir
        )

    if not os.path.exists(os.path.join(working_dir, ".mergin")):
        raise MediaSyncError(
            "The project working directory does not seem to contain Mergin project: "
            + working_dir
        )


def _check_pending_changes(project):
    """Check working directory was not modified manually - this is probably uncommitted change from last attempt"""
    mp = MerginProject(_project_working_dir(project))
    status_push = mp.get_push_changes()
    if status_push["added"] or status_push["updated"] or status_push["removed"]:
        raise MediaSyncError(
            "There are pending changes in the local directory - please review and push manually! "
            + str(status_push)
        )


def _get_media_sync_files(files, project):
    """Return files relevant to media sync from project files"""
    allowed_extensions = config.allowed_extensions
    files_to_upload = [
        f
        for f in files
        if os.path.splitext(f["path"])[1].lstrip(".") in allowed_extensions
    ]
    # filter out files which are not under particular directory in mergin project
    if project.base_path:
        files_to_upload = [
            f for f in files_to_upload if f["path"].startswith(project.base_path)
        ]
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


def mc_download(mc, project):
    """Clone mergin project to local dir
    :param mc: mergin client instance
    :param project: project config object
    :return: list(dict) list of project files metadata
    """
    working_dir = _project_working_dir(project)
    print(f"Downloading project '{project.project_name}' from Mergin server ...")
    try:
        mc.download_project(project.project_name, working_dir)
    except ClientError as e:
        raise MediaSyncError("Mergin client error on download: " + str(e))
    mp = MerginProject(working_dir)
    print(f"Downloaded {_get_project_version(project)} from Mergin")
    files_to_upload = _get_media_sync_files(mp.inspect_files(), project)
    return files_to_upload


def mc_pull(mc, project):
    """Pull latest version to synchronize with local dir
    :param mc: mergin client instance
    :param project: project config object
    :return: list(dict) list of project files metadata
    """
    working_dir = _project_working_dir(project)
    print(f"Pulling project '{project.project_name}' from mergin server ...")
    _check_pending_changes(project)

    mp = MerginProject(working_dir)
    local_version = mp.version()

    try:
        project_info = mc.project_info(mp.project_full_name(), since=local_version)
        projects = mc.get_projects_by_names([mp.project_full_name()])
        server_version = projects[mp.project_full_name()]["version"]
    except ClientError as e:
        raise MediaSyncError("Mergin client error: " + str(e))

    _check_pending_changes(project)

    if server_version == local_version:
        print(f"No changes on Mergin for '{project.project_name}'.")
        return

    try:
        status_pull = mp.get_pull_changes(project_info["files"])
        mc.pull_project(working_dir)
    except ClientError as e:
        raise MediaSyncError("Mergin client error on pull: " + str(e))

    print("Pulled new version from Mergin: " + _get_project_version(project))
    files_to_upload = _get_media_sync_files(
        status_pull["added"] + status_pull["updated"], project
    )
    return files_to_upload


def _update_references(project, files):
    """Update references to media files in reference table"""
    working_dir = _project_working_dir(project)
    for ref in project.references:
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
                os.path.join(working_dir, ref.file)
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


def media_sync_push(mc, driver, project, files):
    if not files:
        return
    working_dir = _project_working_dir(project)
    print(f"Synchronizing files for project '{project.project_name}' with external drive...")
    _check_has_working_dir(project)
    migrated_files = {}

    # TODO make async and parallel for better performance
    for file in files:
        src = os.path.join(working_dir, file["path"])
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
    _update_references(project, migrated_files)

    # remove from local dir if move mode
    if config.operation_mode == "move":
        for file in migrated_files.keys():
            src = os.path.join(working_dir, file)
            os.remove(src)

    # push changes to mergin back (with changed references and removed files) if applicable
    try:
        mp = MerginProject(working_dir)
        status_push = mp.get_push_changes()
        if status_push["added"]:
            raise MediaSyncError(
                "There are changes to be added - it should never happen"
            )
        if status_push["updated"] or status_push["removed"]:
            mc.push_project(working_dir)
            version = _get_project_version(project)
            print("Pushed new version to Mergin: " + version)
    except (ClientError, MediaSyncError) as e:
        # this could be either because of some temporal error (network, server lock)
        # or permanent one that needs to be resolved by user
        raise MediaSyncError("Mergin client error on push: " + str(e))

    print("Sync finished")


def _sync_project(mc, project):
    """Run the full download-or-pull + push cycle for a single project."""
    working_dir = _project_working_dir(project)
    try:
        driver = create_driver(config, project)
    except DriverError as e:
        print(f"Error initialising driver for '{project.project_name}': " + str(e))
        return

    try:
        if os.path.exists(working_dir):
            files_to_sync = mc_pull(mc, project)
        else:
            files_to_sync = mc_download(mc, project)

        if not files_to_sync:
            print(f"No files to sync for '{project.project_name}'")
            return

        media_sync_push(mc, driver, project, files_to_sync)
    except MediaSyncError as err:
        print(f"Error syncing '{project.project_name}': " + str(err))


def main():
    print(f"== Starting Mergin Media Sync version {__version__} ==")
    try:
        validate_config(config)
    except ConfigError as e:
        print("Error: " + str(e))
        return

    try:
        print("Logging in to Mergin...")
        mc = create_mergin_client()
    except MediaSyncError as err:
        print("Error: " + str(err))
        return

    for project in config.projects:
        _sync_project(mc, project)

    print("== Media sync done! ==")


if __name__ == "__main__":
    main()
