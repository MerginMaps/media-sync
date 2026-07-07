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
            "PROJECT_WORKING_DIR": "/tmp/working_project",
            "OPERATION_MODE": "copy",
            "DRIVER": "minio",
            "MINIO__ENDPOINT": MINIO_URL,
            "MINIO__ACCESS_KEY": MINIO_ACCESS_KEY,
            "MINIO__SECRET_KEY": MINIO_SECRET_KEY,
            "MINIO__BUCKET": "test",
            "PROJECTS": [
                {
                    "project_name": "test/mediasync",
                    "bucket_subpath": "",
                    "references": [],
                }
            ],
        }
    )


def test_config():
    # valid config
    _reset_config()
    validate_config(config)

    # references None is normalised to []
    _reset_config()
    config.update(
        {
            "PROJECTS": [
                {
                    "project_name": "test/mediasync",
                    "bucket_subpath": "",
                    "references": None,
                }
            ]
        }
    )
    validate_config(config)

    # valid references list
    _reset_config()
    config.update(
        {
            "PROJECTS": [
                {
                    "project_name": "test/mediasync",
                    "bucket_subpath": "",
                    "references": [
                        {
                            "file": "survey.gpkg",
                            "table": "table",
                            "local_path_column": "local_path_column",
                            "driver_path_column": "driver_path_column",
                        }
                    ],
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
        ConfigError,
        match="Config error: Project 'test/mediasync' is missing 'dest' for local driver",
    ):
        config.update(
            {
                "DRIVER": "local",
                "PROJECTS": [
                    {
                        "project_name": "test/mediasync",
                        "dest": None,
                        "references": [],
                    }
                ],
            }
        )
        validate_config(config)

    _reset_config()
    with pytest.raises(
        ConfigError, match="Config error: Incorrect MinIO driver settings"
    ):
        config.update({"DRIVER": "minio", "MINIO__ENDPOINT": None})
        validate_config(config)

    _reset_config()
    with pytest.raises(
        ConfigError, match="Config error: Allowed extensions can not be empty"
    ):
        config.update({"ALLOWED_EXTENSIONS": []})
        validate_config(config)

    _reset_config()
    with pytest.raises(
        ConfigError, match="incorrect media reference settings"
    ):
        config.update(
            {
                "PROJECTS": [
                    {
                        "project_name": "test/mediasync",
                        "bucket_subpath": "",
                        "references": [{"file": "survey.gpkg"}],
                    }
                ]
            }
        )
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="Config error: Unsupported operation mode"):
        config.update({"OPERATION_MODE": ""})
        validate_config(config)

    _reset_config()
    with pytest.raises(ConfigError, match="'projects' list is missing or empty"):
        config.update({"PROJECTS": []})
        validate_config(config)
