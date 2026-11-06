"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import pathlib

from dynaconf import Dynaconf
from drivers import DriverType

config = Dynaconf(
    envvar_prefix=False,
    settings_files=["config.yaml"],
)


class ConfigError(Exception):
    pass


S3_SETTINGS = [
    "endpoint",
    "access_key",
    "secret_key",
    "bucket",
    "secure",
    "region",
    "bucket_subpath",
    "public_url",
    "addressing_style",
    "create_bucket",
]

S3_ADDRESSING_STYLES = ("auto", "path", "virtual")


def resolve_s3_config(config):
    """Fill `s3` settings missing in config from deprecated `minio` section (MINIO__* variables)"""
    legacy = config.driver == DriverType.MINIO
    if legacy:
        print("Warning: driver 'minio' is deprecated, use 's3' instead")

    # empty values in config are None or "", while explicit false or 0 is a valid setting
    legacy_settings = False
    for key in S3_SETTINGS:
        value = config.get(f"s3.{key}")
        legacy_value = config.get(f"minio.{key}")
        if value in (None, "") and legacy_value not in (None, ""):
            config.set(f"s3.{key}", legacy_value)
            legacy_settings = True
    if legacy_settings:
        print(
            "Warning: 'minio' settings (MINIO__* variables) are deprecated, use 's3' settings (S3__* variables) instead"
        )

    # legacy minio settings used plain http by default
    defaults = {"secure": not (legacy or legacy_settings), "create_bucket": True}
    for key, default in defaults.items():
        if config.get(f"s3.{key}") in (None, ""):
            config.set(f"s3.{key}", default)
        config.set(f"s3.{key}", config.as_bool(f"s3.{key}"))


def validate_config(config):
    """Validate config - make sure values are consistent"""

    if not (
        config.mergin.username and config.mergin.password and config.mergin.project_name
    ):
        raise ConfigError("Config error: Incorrect mergin settings")

    if not (
        config.driver == DriverType.LOCAL
        or config.driver == DriverType.S3
        or config.driver == DriverType.MINIO
        or config.driver == DriverType.GOOGLE_DRIVE
    ):
        raise ConfigError("Config error: Unsupported driver")

    if config.operation_mode not in ["move", "copy"]:
        raise ConfigError("Config error: Unsupported operation mode")

    if config.driver == "local" and not config.local.dest:
        raise ConfigError("Config error: Incorrect Local driver settings")

    if config.driver == DriverType.S3 or config.driver == DriverType.MINIO:
        resolve_s3_config(config)
        # endpoint is not needed for AWS S3, but then region is needed to construct URLs of files
        # access keys are optional, AWS default credentials chain (e.g. IAM role) is used if not set
        if not (
            config.get("s3.bucket")
            and (config.get("s3.endpoint") or config.get("s3.region"))
            and bool(config.get("s3.access_key")) == bool(config.get("s3.secret_key"))
        ):
            raise ConfigError("Config error: Incorrect S3 driver settings")
        if (config.get("s3.addressing_style") or "auto") not in S3_ADDRESSING_STYLES:
            raise ConfigError(
                f"Config error: Unsupported S3 addressing style, use one of {', '.join(S3_ADDRESSING_STYLES)}"
            )

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

    if "daemon" in config and "max_failed_retries" in config.daemon:
        max_failed_retries = config.daemon.max_failed_retries
        if not isinstance(max_failed_retries, int) or max_failed_retries < 0:
            raise ConfigError(
                "Config error: `max_failed_retries` in `daemon` must be a non-negative integer."
            )

    if config.driver == DriverType.GOOGLE_DRIVE and not (
        hasattr(config.google_drive, "service_account_file")
        and hasattr(config.google_drive, "folder")
        and hasattr(config.google_drive, "share_with")
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
