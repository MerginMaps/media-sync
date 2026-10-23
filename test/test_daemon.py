"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import datetime
import logging
import os

import pytest
from mergin import ClientError, MerginClient

import media_sync_daemon
from config import config
from drivers import create_driver
from media_sync import MediaSyncError

from .conftest import WORKSPACE, cleanup, prepare_mergin_project


class StopDaemon(Exception):
    """Raised from mocked sleep to break out of the infinite daemon loop"""


@pytest.fixture
def run_daemon(mocker):
    """Returns function running the daemon loop until it exits or sleeps `iterations` times.

    Sleeping is mocked, the function returns list of the requested sleep times and whether the daemon exited.
    """

    def run(
        driver=None,
        iterations=100,
        sleep_time=10,
        max_failed_retries=10,
        on_sleep=None,
    ):
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if on_sleep:
                on_sleep(len(sleeps))
            if len(sleeps) >= iterations:
                raise StopDaemon()

        mocker.patch("media_sync_daemon.time.sleep", side_effect=fake_sleep)
        logger = logging.getLogger("media-sync-test")
        try:
            media_sync_daemon.run_daemon(driver, sleep_time, max_failed_retries, logger)
        except StopDaemon:
            return sleeps, False
        except SystemExit:
            return sleeps, True

    return run


@pytest.fixture
def sync_mocks(mocker, tmp_path):
    """Mocks Mergin client creation and sync steps. Download creates the project working directory
    unless it fails with an error from `download_errors` list"""
    config.update({"PROJECT_WORKING_DIR": str(tmp_path / "project")})
    mc = mocker.MagicMock()
    mc._auth_session = {
        "expire": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=12)
    }
    download_errors = []

    def download(mc):
        if download_errors:
            raise download_errors.pop(0)
        os.makedirs(config.project_working_dir)

    return mocker.Mock(
        mc=mc,
        download_errors=download_errors,
        create_client=mocker.patch(
            "media_sync_daemon.create_mergin_client", return_value=mc
        ),
        download=mocker.patch("media_sync_daemon.mc_download", side_effect=download),
        pull=mocker.patch("media_sync_daemon.mc_pull"),
        push=mocker.patch("media_sync_daemon.media_sync_push"),
        token_store=mocker.patch("media_sync_daemon.AuthTokenStore"),
    )


def test_retry_wait_time():
    """Wait time doubles with each consecutive failure, starting at sleep time and capped at max wait
    (MAX_RETRY_WAIT by default) or sleep time if longer"""
    assert [media_sync_daemon.retry_wait_time(10, f) for f in range(1, 9)] == [
        10,
        20,
        40,
        80,
        160,
        320,
        600,
        600,
    ]
    # sleep time longer than the max retry wait is respected
    assert media_sync_daemon.retry_wait_time(3600, 5) == 3600
    # no huge numbers with many failures
    assert (
        media_sync_daemon.retry_wait_time(10, 10**6) == media_sync_daemon.MAX_RETRY_WAIT
    )
    # longer limit for failed logins
    assert [media_sync_daemon.retry_wait_time(10, f, 3600) for f in range(7, 11)] == [
        640,
        1280,
        2560,
        3600,
    ]


def test_daemon_startup_is_retried(run_daemon, sync_mocks):
    """Failed login and initial download are retried in the same process with backoff: login is repeated
    only until it succeeds and regular sleep time is used again once the sync succeeds
    """
    sync_mocks.create_client.side_effect = [MediaSyncError("login failed")] * 2 + [
        sync_mocks.mc
    ]
    sync_mocks.download_errors.extend([MediaSyncError("download failed")] * 2)

    sleeps, exited = run_daemon(iterations=6)

    assert not exited
    assert sleeps == [10, 20, 40, 80, 10, 10]
    assert sync_mocks.create_client.call_count == 3
    assert sync_mocks.download.call_count == 3
    assert sync_mocks.pull.call_count == 1
    assert sync_mocks.push.call_count == 2


def test_daemon_keeps_running_after_start(run_daemon, sync_mocks):
    """Sync errors after a successful start (e.g. server unavailable) never make the daemon exit, only back off.
    A single unexpected error does not stop it either. No new login is done in any case.
    """
    sync_mocks.pull.side_effect = (
        [MediaSyncError("server unavailable")] * 8 + [RuntimeError("boom")] + [None]
    )

    sleeps, exited = run_daemon(iterations=11, max_failed_retries=2)

    assert not exited
    assert sleeps == [10, 10, 20, 40, 80, 160, 320, 600, 600, 600, 10]
    sync_mocks.create_client.assert_called_once()
    sync_mocks.download.assert_called_once()
    assert sync_mocks.push.call_count == 2


@pytest.mark.parametrize(
    "failing_step, errors, max_failed_retries, expected_sleeps, expected_exit",
    [
        ("download", [MediaSyncError("download failed")] * 4, 3, [10, 20, 40], True),
        ("pull", [RuntimeError("boom")] * 3, 2, [10, 10, 20], True),
        (
            "create_client",
            MediaSyncError("login failed"),
            0,
            [10, 20, 40, 80, 160, 320, 640, 1280, 2560, 3600],
            False,
        ),
    ],
    ids=["startup-failures", "unexpected-errors", "never-exit"],
)
def test_daemon_gives_up_after_max_failed_retries(
    run_daemon,
    sync_mocks,
    failing_step,
    errors,
    max_failed_retries,
    expected_sleeps,
    expected_exit,
):
    """Daemon exits after `max_failed_retries` consecutive failed retries of the start or of unexpected errors
    (never with max_failed_retries 0)"""
    if failing_step == "download":
        sync_mocks.download_errors.extend(errors)
    else:
        getattr(sync_mocks, failing_step).side_effect = errors

    # daemon that should not exit is stopped after the expected number of sleeps
    iterations = 100 if expected_exit else len(expected_sleeps)
    sleeps, exited = run_daemon(
        iterations=iterations, max_failed_retries=max_failed_retries
    )

    assert exited == expected_exit
    assert sleeps == expected_sleeps


def test_daemon_logs_in_again_when_token_rejected(run_daemon, sync_mocks):
    """When the server rejects the auth token during sync, stored token is removed and new login is done
    on the next attempt. Other failures do not cause new login."""
    sync_mocks.pull.side_effect = [
        MediaSyncError("server unavailable"),
        MediaSyncError("401"),
        None,
    ]
    sync_mocks.mc.user_info.side_effect = [
        None,
        ClientError("Unauthorized", http_error=401),
    ]

    sleeps, exited = run_daemon(iterations=4)

    assert not exited
    assert sleeps == [10, 10, 20, 10]
    assert sync_mocks.create_client.call_count == 2
    sync_mocks.token_store.from_config.return_value.remove.assert_called_once()


def test_daemon_credentials_rejected_after_start(run_daemon, sync_mocks):
    """When the token is rejected after a successful start and the new login fails too (e.g. user was deactivated),
    the daemon keeps running but retries the login with longer wait times (up to MAX_LOGIN_RETRY_WAIT)
    """
    sync_mocks.create_client.side_effect = [sync_mocks.mc] + [
        MediaSyncError("login failed")
    ] * 20
    sync_mocks.pull.side_effect = [MediaSyncError("401")]
    sync_mocks.mc.user_info.side_effect = ClientError("Unauthorized", http_error=401)

    sleeps, exited = run_daemon(iterations=13, max_failed_retries=2)

    assert not exited
    assert sleeps == [10, 10, 20, 40, 80, 160, 320, 640, 1280, 2560, 3600, 3600, 3600]
    # first login + login retried after each sleep since the token was rejected
    assert sync_mocks.create_client.call_count == 12


def test_daemon_refreshes_expiring_token(run_daemon, sync_mocks, mocker):
    """Auth token which expires soon is refreshed after the sync"""
    sync_mocks.mc._auth_session["expire"] = datetime.datetime.now(
        datetime.timezone.utc
    ) + datetime.timedelta(minutes=30)
    fresh_mc = mocker.MagicMock()
    fresh_mc._auth_session = {
        "expire": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=12)
    }
    sync_mocks.create_client.side_effect = [sync_mocks.mc, fresh_mc]

    sleeps, exited = run_daemon(iterations=3)

    assert not exited
    assert sleeps == [10, 10, 10]
    assert sync_mocks.create_client.call_count == 2
    assert sync_mocks.pull.call_args.args[0] is fresh_mc


def _setup_daemon_sync(tmp_path, project_name):
    full_project_name = WORKSPACE + "/" + project_name
    config.update(
        {
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": str(tmp_path / "project"),
            "DRIVER": "local",
            "LOCAL__DEST": str(tmp_path / "driver"),
            "ALLOWED_EXTENSIONS": ["jpg"],
        }
    )
    return full_project_name, create_driver(config)


def test_daemon_recovers_from_missing_project(
    mc: MerginClient, run_daemon, mocker, tmp_path
):
    """Integration test with real server: project does not exist when the daemon starts,
    the download is retried without logging in again and files are synced once the project is created
    """
    full_project_name, driver = _setup_daemon_sync(tmp_path, "mediasync_test_daemon")
    cleanup(mc, full_project_name, [])

    def create_project(sleep_count):
        if sleep_count == 2:
            prepare_mergin_project(mc, full_project_name)

    login = mocker.spy(MerginClient, "login")

    sleeps, exited = run_daemon(
        driver, iterations=4, max_failed_retries=5, on_sleep=create_project
    )

    assert not exited
    assert sleeps == [10, 20, 10, 10]
    assert login.call_count == 1
    assert os.path.exists(tmp_path / "driver" / "images" / "img2.jpg")


def test_daemon_recovers_from_rejected_token(
    mc: MerginClient, run_daemon, mocker, tmp_path
):
    """Integration test with real server: the server starts rejecting the auth token
    of the running daemon, the daemon logs in again and continues syncing."""
    full_project_name, driver = _setup_daemon_sync(tmp_path, "mediasync_test_token")
    prepare_mergin_project(mc, full_project_name)
    login = mocker.spy(MerginClient, "login")
    create_client = mocker.spy(media_sync_daemon, "create_mergin_client")
    pull = mocker.spy(media_sync_daemon, "mc_pull")

    def invalidate_token(sleep_count):
        if sleep_count == 1:
            daemon_mc = create_client.spy_return
            daemon_mc._auth_session["token"] = (
                daemon_mc._auth_session["token"][:-4] + "abcd"
            )

    sleeps, exited = run_daemon(driver, iterations=3, on_sleep=invalidate_token)

    assert not exited
    assert sleeps == [10, 10, 10]
    assert login.call_count == 2
    assert pull.call_count == 2
