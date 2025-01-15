"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import pathlib
import typing

from dynaconf import Dynaconf

config = Dynaconf(
    envvar_prefix=False,
    settings_files=["config.yaml"],
)


class ConfigError(Exception):
    pass


def validate_config(config):
    """Validate config - make sure values are consistent"""

    if not (
        config.mergin.username and config.mergin.password and config.mergin.project_name
    ):
        raise ConfigError("Config error: Incorrect mergin settings")

    if config.driver not in ["local", "minio", "google_drive"]:
        raise ConfigError("Config error: Unsupported driver")

    if config.operation_mode not in ["move", "copy"]:
        raise ConfigError("Config error: Unsupported operation mode")

    if config.driver == "local" and not config.local.dest:
        raise ConfigError("Config error: Incorrect Local driver settings")

    if config.driver == "minio" and not (
        config.minio.endpoint
        and config.minio.access_key
        and config.minio.secret_key
        and config.minio.bucket
    ):
        raise ConfigError("Config error: Incorrect MinIO driver settings")

    if not (config.allowed_extensions and len(config.allowed_extensions)):
        raise ConfigError("Config error: Allowed extensions can not be empty")

    if "references" not in config:
        config.update({"references": []})

    if config.references is None:
        config.update({"references": []})

    if not isinstance(config.references, list):
        raise ConfigError(
            "Config error: Incorrect reference settings. Needs to be list of references."
        )

    for ref in config.references:
        if not all(
            hasattr(ref, attr)
            for attr in ["file", "table", "local_path_column", "driver_path_column"]
        ):
            raise ConfigError("Config error: Incorrect media reference settings")

    if config.driver == "google_drive" and not (
        config.google_drive.service_account_file
        and config.google_drive.folder
        and config.google_drive.share_with
    ):
        raise ConfigError("Config error: Incorrect GoogleDrive driver settings")


def update_config_path(
    path_param: str,
) -> None:
    config_file_path = pathlib.Path(path_param)

    if config_file_path.exists():
        print(f"Using config file: {path_param}")
        user_file_config = Dynaconf(
            envvar_prefix=False,
            settings_files=[config_file_path],
        )
        config.update(user_file_config)
    else:
        raise IOError(f"Config file {config_file_path} does not exist.")


def get_share_with(google_driver_settings) -> typing.List[str]:
    """Returns shared_with setting as a list of emails."""

    if isinstance(google_driver_settings.share_with, str):
        return [google_driver_settings.share_with]
    elif isinstance(google_driver_settings.share_with, list):
        return google_driver_settings.share_with
    else:
        raise ConfigError("Config error: Incorrect GoogleDrive shared_with settings")
