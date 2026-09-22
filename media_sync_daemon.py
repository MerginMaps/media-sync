"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import argparse
import sys
import datetime
import os
import time
from drivers import DriverError, create_driver
from media_sync import (
    create_mergin_client,
    mc_download,
    media_sync_push,
    _sync_project,
    _project_working_dir,
    MediaSyncError,
)
from config import config, validate_config, ConfigError, update_config_path
from version import __version__


def main():
    parser = argparse.ArgumentParser(
        prog="media_sync_daemon.py",
        description="Synchronization tool for media files in Mergin Maps project to other backends.",
        epilog="www.merginmaps.com",
    )

    parser.add_argument(
        "config_file",
        nargs="?",
        default="config.yaml",
        help="Path to file with configuration. Default value is config.yaml in current working directory.",
    )

    args = parser.parse_args()

    print(f"== Starting Mergin Media Sync daemon version {__version__} ==")

    try:
        update_config_path(args.config_file)
    except IOError as e:
        print("Error:" + str(e))
        sys.exit(1)

    sleep_time = config.as_int("daemon.sleep_time")

    try:
        validate_config(config)
    except ConfigError as e:
        print("Error: " + str(e))
        return

    print("Logging in to Mergin...")
    try:
        mc = create_mergin_client()

        # Initial download for projects that have not been downloaded yet
        for project in config.projects:
            working_dir = _project_working_dir(project)
            if not os.path.exists(working_dir):
                try:
                    driver = create_driver(config, project)
                    files_to_sync = mc_download(mc, project)
                    media_sync_push(mc, driver, project, files_to_sync)
                except (DriverError, MediaSyncError) as e:
                    print(f"Error initialising project '{project.project_name}': " + str(e))

    except MediaSyncError as e:
        print("Error: " + str(e))
        return

    # keep running until killed by ctrl+c:
    # - sleep N seconds
    # - pull + push for every project
    while True:
        print(datetime.datetime.now())
        try:
            for project in config.projects:
                _sync_project(mc, project)

            # check mergin client token expiration
            delta = mc._auth_session["expire"] - datetime.datetime.now(
                datetime.timezone.utc
            )
            if delta.total_seconds() < 3600:
                mc = create_mergin_client()

        except MediaSyncError as e:
            print("Error: " + str(e))

        print("Going to sleep")
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
