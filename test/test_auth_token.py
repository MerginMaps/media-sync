"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import base64
import datetime
import json
import os
import stat

import pytest
from mergin import ClientError, MerginClient

import media_sync
from config import config


def _create_test_token(label: str, validity_hours: float = 12) -> str:
    """Creates auth token in the format issued by the server (signature is not checked by the client),
    label makes tokens distinguishable in tests"""
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        hours=validity_hours
    )
    payload = json.dumps({"label": label, "expire": str(expire)})
    return f"Bearer {base64.urlsafe_b64encode(payload.encode()).decode().rstrip('=')}.signature"


# token stored before media sync starts
STORED_TOKEN = _create_test_token("stored")
# token issued by the (mocked) server on login
NEW_TOKEN = _create_test_token("new")


@pytest.fixture
def token_config(tmp_path):
    """Configures Mergin credentials and project working directory, the auth token is stored in its parent"""
    config.update(
        {
            "MERGIN__URL": "https://mergin.example.com",
            "MERGIN__USERNAME": "user",
            "MERGIN__PASSWORD": "pwd",
            "PROJECT_WORKING_DIR": str(tmp_path / "project"),
        }
    )
    return tmp_path / ".mergin_auth.json"


@pytest.fixture
def mergin_client(mocker):
    """Mocks MerginClient: login issues NEW_TOKEN, server response to token validation
    can be changed via `user_info` mock"""
    state = mocker.Mock(user_info=mocker.Mock())

    def create_client(
        url, auth_token=None, login=None, password=None, plugin_version=None
    ):
        mc = mocker.MagicMock()
        mc._auth_session = {"token": auth_token or NEW_TOKEN}
        mc.user_info = state.user_info
        return mc

    state.cls = mocker.patch("media_sync.MerginClient", side_effect=create_client)
    return state


def _store_token(
    path, url="https://mergin.example.com", username="user", token=STORED_TOKEN
):
    path.write_text(json.dumps({"url": url, "username": username, "token": token}))


def _logins(mergin_client):
    """Number of MerginClient instances created with login (i.e. without stored token)"""
    return sum(
        1 for c in mergin_client.cls.call_args_list if not c.kwargs.get("auth_token")
    )


def test_login_stores_token(token_config, mergin_client):
    """Without stored token, media sync logs in and stores the new token readable only by the owner"""
    mc = media_sync.create_mergin_client()

    assert mc._auth_session["token"] == NEW_TOKEN
    assert _logins(mergin_client) == 1
    assert json.loads(token_config.read_text()) == {
        "url": "https://mergin.example.com",
        "username": "user",
        "token": NEW_TOKEN,
    }
    if os.name == "posix":
        assert stat.S_IMODE(token_config.stat().st_mode) == 0o600


def test_stored_token_is_reused(token_config, mergin_client):
    """Valid stored token is used without new login, after checking the server still accepts it"""
    _store_token(token_config)

    mc = media_sync.create_mergin_client()

    assert mc._auth_session["token"] == STORED_TOKEN
    assert _logins(mergin_client) == 0
    mergin_client.user_info.assert_called_once()


def test_stored_token_is_shared_by_projects(token_config, mergin_client):
    """Token is stored outside of the project working directory, so it is kept when the project
    is downloaded again and it is reused for other projects in the same parent directory
    """
    media_sync.create_mergin_client()
    config.update({"PROJECT_WORKING_DIR": str(token_config.parent / "another_project")})

    mc = media_sync.create_mergin_client()

    assert mc._auth_session["token"] == NEW_TOKEN
    assert _logins(mergin_client) == 1
    assert not os.path.exists(config.project_working_dir)


@pytest.mark.parametrize(
    "stored, user_info_error",
    [
        (None, None),
        ({"url": "https://other.example.com"}, None),
        ({"username": "other"}, None),
        ("not a json", None),
        ({"token": "Bearer malformed"}, None),
        ({"token": _create_test_token("expiring", validity_hours=0.5)}, None),
        ({}, ClientError("Unauthorized", http_error=401)),
    ],
    ids=[
        "no-token",
        "other-server",
        "other-user",
        "corrupted-file",
        "malformed-token",
        "expires-soon",
        "rejected-by-server",
    ],
)
def test_stored_token_is_not_used(token_config, mergin_client, stored, user_info_error):
    """Stored token is not used when missing, issued for other server / user, unreadable, malformed,
    about to expire or rejected by the server - new login is done and its token is stored instead
    """
    if isinstance(stored, dict):
        _store_token(token_config, **stored)
    elif stored:
        token_config.write_text(stored)
    mergin_client.user_info.side_effect = user_info_error

    mc = media_sync.create_mergin_client()

    assert mc._auth_session["token"] == NEW_TOKEN
    assert _logins(mergin_client) == 1
    assert json.loads(token_config.read_text())["token"] == NEW_TOKEN


def test_login_works_when_token_can_not_be_stored(
    token_config, mergin_client, mocker, capsys
):
    """Failure to store the token (e.g. read-only directory) is only reported, login still succeeds"""
    mocker.patch(
        "media_sync.os.open", side_effect=PermissionError("Read-only file system")
    )

    mc = media_sync.create_mergin_client()

    assert mc._auth_session["token"] == NEW_TOKEN
    assert not token_config.exists()
    assert (
        "Unable to store Mergin Maps auth token: Read-only file system"
        in capsys.readouterr().out
    )


def test_stored_token_server_error(token_config, mergin_client):
    """Server error other than 401 when validating stored token does not cause new login,
    the stored token is used (and the sync itself fails and is retried later if the server is really unavailable)
    """
    _store_token(token_config)
    mergin_client.user_info.side_effect = ClientError(
        "Service unavailable", http_error=503
    )

    mc = media_sync.create_mergin_client()

    assert mc._auth_session["token"] == STORED_TOKEN
    assert _logins(mergin_client) == 0
    assert json.loads(token_config.read_text())["token"] == STORED_TOKEN


def test_stored_token_with_server(tmp_path, mocker):
    """Integration test with real server: stored token is reused without new login,
    tampered token is rejected by the server and new login is done"""
    config.update({"PROJECT_WORKING_DIR": str(tmp_path / "project")})
    token_path = tmp_path / ".mergin_auth.json"
    login = mocker.spy(MerginClient, "login")

    mc = media_sync.create_mergin_client()
    assert login.call_count == 1
    assert token_path.exists()

    mc = media_sync.create_mergin_client()
    assert login.call_count == 1
    # stored token is accepted by the server (configured login can be either username or email)
    user_info = mc.user_info()
    assert config.mergin.username in (user_info["username"], user_info["email"])

    stored = json.loads(token_path.read_text())
    tampered_token = stored["token"][:-4] + "abcd"
    token_path.write_text(json.dumps({**stored, "token": tampered_token}))

    mc = media_sync.create_mergin_client()
    assert login.call_count == 2
    assert json.loads(token_path.read_text())["token"] != tampered_token
