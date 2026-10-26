"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import argparse
import datetime
import gc
import logging
import os
import sys
import time

from config import config, validate_config, ConfigError, update_config_path
from drivers import DriverError, create_driver
from media_sync import (
    auth_token_expires_soon,
    auth_token_rejected,
    create_mergin_client,
    mc_download,
    media_sync_push,
    mc_pull,
    AuthTokenStore,
    MediaSyncError,
)
from version import __version__

# upper limit (in seconds) of the wait time between retries after repeated failures
MAX_RETRY_WAIT = 600
# upper limit (in seconds) of the wait time for failed logins - credentials rejected
# by the server can not be fixed by retrying and too frequent failed logins may lock the account
MAX_LOGIN_RETRY_WAIT = 3600
# default number of consecutive failed retries of startup (login / initial download) or unexpected errors before the daemon exits
DEFAULT_MAX_FAILED_RETRIES = 10


def setup_logger():
    logger = logging.getLogger("media-sync")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def retry_wait_time(
    sleep_time: int, failures: int, max_wait: int = MAX_RETRY_WAIT
) -> int:
    """Seconds to wait before the next attempt after `failures` consecutive failed attempts.

    Doubles with each failure up to max_wait, but never less than sleep_time."""
    # limit the exponent to avoid huge numbers with many failures
    wait_time = sleep_time * 2 ** min(failures - 1, 32)
    return min(wait_time, max(sleep_time, max_wait))


def run_sync_cycle(mc, driver, logger):
    """Download the project if it is not present yet or pull changes, then sync media files"""
    if not os.path.exists(config.project_working_dir):
        logger.info(
            "Project directory not found. Downloading from Mergin maps server..."
        )
        files_to_sync = mc_download(mc)
    else:
        logger.info("Pulling changes from Mergin maps server...")
        files_to_sync = mc_pull(mc)
    media_sync_push(mc, driver, files_to_sync)
    logger.info("Sync complete.")


def run_daemon(driver, sleep_time: int, max_failed_retries: int, logger) -> None:
    """Keep syncing until killed. Failures (including login and initial download) are retried within
    this process with exponential backoff instead of exiting, so the daemon does not
    log in again on every restart by the container / service manager.

    Sync errors after a successful start are retried indefinitely. Startup failures (login, initial download)
    and unexpected errors make the daemon exit after `max_failed_retries` consecutive failed retries (0 = never exit).
    """
    mc = None
    started = False
    failures = 0
    fatal_failures = 0

    while True:
        logger.info(
            f"Heartbeat: {datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()} UTC"
        )
        login_failed = False

        try:
            if mc is None:
                logger.info("Logging in to Mergin maps server...")
                mc = create_mergin_client()

            run_sync_cycle(mc, driver, logger)
            started = True

            if auth_token_expires_soon(mc):
                logger.info("Refreshing Mergin maps server auth token...")
                mc = create_mergin_client()

            failures = 0
            fatal_failures = 0

        except Exception as e:
            failures += 1
            # client is created by login as the first step, so no client means the login has failed
            login_failed = mc is None
            if not started or not isinstance(e, MediaSyncError):
                fatal_failures += 1
            if isinstance(e, MediaSyncError):
                logger.error(f"Media sync error: {e}")
            else:
                logger.exception(f"Unexpected error: {e!r}")

            if max_failed_retries and fatal_failures > max_failed_retries:
                logger.error(
                    f"Giving up after {max_failed_retries} retries, the daemon will exit"
                )
                sys.exit(1)
            # server may reject the token before it expires, log in again on the next attempt in such case
            if mc is not None and auth_token_rejected(mc):
                logger.warning(
                    "Mergin maps server auth token was rejected, going to log in again"
                )
                AuthTokenStore.from_config().remove()
                mc = None

        if failures:
            wait_time = retry_wait_time(
                sleep_time,
                failures,
                MAX_LOGIN_RETRY_WAIT if login_failed else MAX_RETRY_WAIT,
            )
            logger.info(
                f"Failed attempt #{failures}, going to sleep for {wait_time} seconds before retrying"
            )
        else:
            wait_time = sleep_time
            logger.info(f"Sleeping for {wait_time} seconds...")
        time.sleep(wait_time)


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
        help="Path to file with configuration. Default is ./config.yaml",
    )

    args = parser.parse_args()
    logger = setup_logger()
    logger.info(f"== Starting Mergin Media Sync daemon v{__version__} ==")

    try:
        update_config_path(args.config_file)
        validate_config(config)
    except (IOError, ConfigError) as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    sleep_time = config.as_int("daemon.sleep_time")
    max_failed_retries = config.get(
        "daemon.max_failed_retries", default=DEFAULT_MAX_FAILED_RETRIES, cast="@int"
    )

    try:
        driver = create_driver(config)
    except DriverError as e:
        logger.error(f"Driver error: {e}")
        sys.exit(1)

    logger.info("Entering sync loop...")
    run_daemon(driver, sleep_time, max_failed_retries, logger)


if __name__ == "__main__":
    main()
