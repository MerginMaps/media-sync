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
    return "\"" + identifier + "\""


def _get_project_version():
    """ Returns the current version of the project """
    mp = MerginProject(config.project_working_dir)
    return mp.metadata["version"]


def _check_has_working_dir():
    if not os.path.exists(config.project_working_dir):
        raise MediaSyncError("The project working directory does not exist: " + config.project_working_dir)

    if not os.path.exists(os.path.join(config.project_working_dir, '.mergin')):
        raise MediaSyncError("The project working directory does not seem to contain Mergin project: " + config.project_working_dir)


def _check_pending_changes():
    """ Check working directory was not modified manually - this is probably uncommitted change from last attempt"""
    mp = MerginProject(config.project_working_dir)
    status_push = mp.get_push_changes()
    if status_push['added'] or status_push['updated'] or status_push['removed']:
        raise MediaSyncError(
            "There are pending changes in the local directory - please review and push manually! " + str(status_push))


def _get_media_sync_files(files):
    """ Return files relevant to media sync from project files """
    allowed_extensions = config.allowed_extensions
    files_to_upload = [f for f in files if os.path.splitext(f["path"])[1].lstrip('.') in allowed_extensions]
    # filter out files which are not under particular directory in mergin project
    if config.base_path:
        filtered_files = [f for f in files_to_upload if f["path"].startswith(config.base_path)]
        files_to_upload = filtered_files
    return files_to_upload


def create_mergin_client():
    """ Create instance of MerginClient"""
    try:
        return MerginClient(config.mergin.url, login=config.mergin.username, password=config.mergin.password, plugin_version=f"media-sync/{__version__}")
    except LoginError as e:
        # this could be auth failure, but could be also server problem (e.g. worker crash)
        raise MediaSyncError(f"Unable to log in to Mergin: {str(e)} \n\n" +
                          "Have you specified correct credentials in configuration file?")
    except ClientError as e:
        # this could be e.g. DNS error
        raise MediaSyncError("Mergin client error: " + str(e))


def mc_download(mc):
    """ Clone mergin project to local dir
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


def mc_pull(mc):
    """ Pull latest version to synchronize with local dir
    :param mc: mergin client instance
    :return: list(dict) list of project files metadata
    """
    print("Pulling from mergin server ...")
    _check_pending_changes()

    mp = MerginProject(config.project_working_dir)
    project_path = mp.metadata["name"]
    local_version = mp.metadata["version"]

    try:
        project_info = mc.project_info(project_path, since=local_version)
        projects = mc.get_projects_by_names([project_path])
        server_version = projects[project_path]["version"]
    except ClientError as e:
        # this could be e.g. DNS error
        raise MediaSyncError("Mergin client error: " + str(e))

    _check_pending_changes()

    if server_version == local_version:
        print("No changes on Mergin.")
        return

    try:
        status_pull = mp.get_pull_changes(project_info["files"])
        mc.pull_project(config.project_working_dir)
    except ClientError as e:
        raise MediaSyncError("Mergin client error on pull: " + str(e))

    print("Pulled new version from Mergin: " + _get_project_version())
    files_to_upload = _get_media_sync_files(status_pull["added"]+status_pull["updated"])
    return files_to_upload


def _update_references(files):
    """ Update references to media files in reference table """
    for ref in config.references:
        reference_config = [ref.file, ref.table, ref.local_path_column, ref.driver_path_column]
        if not all(reference_config):
            return

        print("Updating references ...")
        try:
            gpkg_conn = sqlite3.connect(os.path.join(config.project_working_dir, ref.file))
            gpkg_conn.enable_load_extension(True)
            gpkg_cur = gpkg_conn.cursor()
            gpkg_cur.execute('SELECT load_extension("mod_spatialite")')
            for file, dest in files.items():
                # remove reference to the local path only in the move mode
                if config.operation_mode == "move":
                    sql = f"UPDATE {_quote_identifier(ref.table)} " \
                          f"SET {_quote_identifier(ref.driver_path_column)}='{dest}', {_quote_identifier(ref.local_path_column)}=Null " \
                          f"WHERE {_quote_identifier(ref.local_path_column)}='{file}'"
                elif config.operation_mode == "copy":
                    sql = f"UPDATE {_quote_identifier(ref.table)} " \
                          f"SET {_quote_identifier(ref.driver_path_column)}='{dest}' " \
                          f"WHERE {_quote_identifier(ref.local_path_column)}='{file}'"
                gpkg_cur.execute(sql)
            gpkg_conn.commit()
            gpkg_conn.close()
        except sqlite3.OperationalError as e:
            raise MediaSyncError("SQLITE error: " + str(e))


def media_sync_push(mc, driver, files):
    if not files:
        return
    print("Synchronizing files with external drive...")
    _check_has_working_dir()
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
            migrated_files[file['path']] = dest
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

    # push changes to mergin back (with changed references and removed files) if applicable
    try:
        mp = MerginProject(config.project_working_dir)
        status_push = mp.get_push_changes()
        if status_push["added"]:
            raise MediaSyncError("There are changes to be added - it should never happen")
        if status_push["updated"] or status_push["removed"]:
            mc.push_project(config.project_working_dir)
            version = _get_project_version()
            print("Pushed new version to Mergin: " + version)
    except (ClientError, MediaSyncError) as e:
        # this could be either because of some temporal error (network, server lock)
        # or permanent one that needs to be resolved by user
        raise MediaSyncError("Mergin client error on push: " + str(e))

    print("Sync finished")


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


if __name__ == '__main__':
    main()
