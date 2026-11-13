"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import pytest

from config import config, ConfigError, validate_config, S3_SETTINGS

from .conftest import (
    SERVER_URL,
    API_USER,
    USER_PWD,
    S3_ENDPOINT,
    S3_SECRET_KEY,
    S3_ACCESS_KEY,
)


def _reset_config():
    """helper to reset config settings to ensure valid config"""
    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png"],
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "MERGIN__PROJECT_NAME": "test/mediasync",
            "WORKING_DIR": "/tmp/working_dir",
            "OPERATION_MODE": "copy",
            **{f"S3__{key.upper()}": "" for key in S3_SETTINGS},
            **{f"MINIO__{key.upper()}": "" for key in S3_SETTINGS},
            "DRIVER": "s3",
            "S3__ENDPOINT": S3_ENDPOINT,
            "S3__ACCESS_KEY": S3_ACCESS_KEY,
            "S3__SECRET_KEY": S3_SECRET_KEY,
            "S3__BUCKET": "test",
            "BASE_PATH": "",
        }
    )


def test_config():
    # valid config
    _reset_config()
    validate_config(config)

    _reset_config()
    config.update({"REFERENCES": None})
    validate_config(config)

    _reset_config()
    config.update(
        {
            "REFERENCES": [
                {
                    "file": "survey.gpkg",
                    "table": "table",
                    "local_path_column": "local_path_column",
                    "driver_path_column": "driver_path_column",
                }
            ]
        }
    )
    validate_config(config)

    with pytest.raises(ConfigError, match="Config error: Incorrect mergin settings"):
        config.update({"MERGIN__USERNAME": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Unsupported driver"):
        config.update({"DRIVER": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(
        ConfigError, match="Config error: Incorrect Local driver settings"
    ):
        config.update({"DRIVER": "local", "LOCAL__DEST": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Incorrect S3 driver settings"):
        config.update({"S3__BUCKET": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Incorrect S3 driver settings"):
        config.update({"S3__ENDPOINT": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Incorrect S3 driver settings"):
        config.update({"S3__SECRET_KEY": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Unsupported S3 addressing style"):
        config.update({"S3__ADDRESSING_STYLE": "foo"})
        validate_config(config)

    _reset_config()
    with pytest.raises(
        ConfigError, match="Config error: Allowed extensions can not be empty"
    ):
        config.update({"ALLOWED_EXTENSIONS": []})
        validate_config(config)

    _reset_config()
    with pytest.raises(
        ConfigError, match="Config error: Incorrect media reference settings"
    ):
        config.update({"REFERENCES": [{"file": "survey.gpkg"}]})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Unsupported operation mode"):
        config.update({"OPERATION_MODE": ""})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Incorrect reference settings"):
        config.update({"REFERENCES": "text"})
        validate_config(config)


def test_config_s3():
    """S3 driver settings defaults, AWS S3 without endpoint and keys"""
    _reset_config()
    validate_config(config)
    assert config.s3.secure is True
    assert config.s3.create_bucket is True

    _reset_config()
    config.update({"S3__SECURE": "false", "S3__CREATE_BUCKET": 0})
    validate_config(config)
    assert config.s3.secure is False
    assert config.s3.create_bucket is False

    # AWS S3 with default credentials chain
    _reset_config()
    config.update(
        {
            "S3__ENDPOINT": "",
            "S3__ACCESS_KEY": "",
            "S3__SECRET_KEY": "",
            "S3__REGION": "eu-west-1",
        }
    )
    validate_config(config)


def test_config_s3_minio_compatibility():
    """Deprecated `minio` driver and settings are used for S3 driver"""
    _reset_config()
    config.update(
        {
            "DRIVER": "minio",
            "S3__ENDPOINT": "",
            "S3__ACCESS_KEY": "",
            "S3__SECRET_KEY": "",
            "S3__BUCKET": "",
            "MINIO__ENDPOINT": "localhost:9000",
            "MINIO__ACCESS_KEY": "access",
            "MINIO__SECRET_KEY": "secret",
            "MINIO__BUCKET": "legacy",
            "MINIO__BUCKET_SUBPATH": "sub",
        }
    )
    validate_config(config)
    assert config.s3.endpoint == "localhost:9000"
    assert config.s3.access_key == "access"
    assert config.s3.secret_key == "secret"
    assert config.s3.bucket == "legacy"
    assert config.s3.bucket_subpath == "sub"
    # legacy settings defaulted to plain http
    assert config.s3.secure is False

    # s3 settings take precedence over minio settings
    _reset_config()
    config.update(
        {
            "MINIO__ENDPOINT": "legacy:9000",
            "MINIO__BUCKET": "legacy",
            "MINIO__SECURE": True,
        }
    )
    validate_config(config)
    assert config.s3.endpoint == S3_ENDPOINT
    assert config.s3.bucket == "test"
    assert config.s3.secure is True

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Incorrect S3 driver settings"):
        config.update({"DRIVER": "minio", "S3__BUCKET": "", "MINIO__BUCKET": ""})
        validate_config(config)


def test_config_daemon_max_failed_retries():
    """`max_failed_retries` in `daemon` section must be a non-negative integer"""
    _reset_config()
    config.update({"DAEMON__MAX_FAILED_RETRIES": 5})
    validate_config(config)

    config.update({"DAEMON__MAX_FAILED_RETRIES": 0})
    validate_config(config)

    for value in [-1, "abc", 1.5]:
        config.update({"DAEMON__MAX_FAILED_RETRIES": value})
        with pytest.raises(
            ConfigError,
            match="`max_failed_retries` in `daemon` must be a non-negative integer",
        ):
            validate_config(config)
