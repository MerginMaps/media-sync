"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import pytest

from config import config, ConfigError, validate_config

from .conftest import (
    SERVER_URL,
    API_USER,
    USER_PWD,
    MINIO_URL,
    MINIO_SECRET_KEY,
    MINIO_ACCESS_KEY,
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
            "PROJECT_WORKING_DIR": "/tmp/working_project",
            "OPERATION_MODE": "copy",
            "DRIVER": "minio",
            "MINIO__ENDPOINT": MINIO_URL,
            "MINIO__ACCESS_KEY": MINIO_ACCESS_KEY,
            "MINIO__SECRET_KEY": MINIO_SECRET_KEY,
            "MINIO__BUCKET": "test",
            "REFERENCES": [
                {
                    "file": "survey.gpkg",
                    "table": "table",
                    "local_path_column": "local_path_column",
                    "driver_path_column": "driver_path_column",
                }
            ],
            "BASE_PATH": "",
        }
    )


def test_config():
    # valid config
    _reset_config()
    validate_config(config)

    with pytest.raises(ConfigError, match="Config error: Incorrect mergin settings"):
        config.update({"MERGIN__USERNAME": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Unsupported driver"):
        config.update({"DRIVER": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Incorrect Local driver settings"):
        config.update({"DRIVER": "local", "LOCAL__DEST": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Incorrect MinIO driver settings"):
        config.update({"DRIVER": "minio", "MINIO__ENDPOINT": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Allowed extensions can not be empty"):
        config.update({"ALLOWED_EXTENSIONS": []})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: References list can not be empty"):
        config.update({"REFERENCES": []})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Incorrect media reference settings"):
        config.update({"REFERENCES": [{"file": "survey.gpkg"}]})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Unsupported operation mode"):
        config.update({"OPERATION_MODE": ""})
        validate_config(config)
