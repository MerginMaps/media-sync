"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

from dynaconf import Dynaconf

config = Dynaconf(
    envvar_prefix=False,
    settings_files=['config.ini'],
)


class ConfigError(Exception):
    pass


def validate_config(config):
    """ Validate config - make sure values are consistent """

    if not (config.mergin.username and config.mergin.password and config.mergin.project_name):
        raise ConfigError("Config error: Incorrect mergin settings")

    if config.driver not in ["local", "minio"]:
        raise ConfigError("Config error: Unsupported driver")

    if config.operation_mode not in ["move", "copy"]:
        raise ConfigError("Config error: Unsupported operation mode")

    if config.driver == 'local' and not config.local.dest:
        raise ConfigError("Config error: Incorrect Local driver settings")

    if config.driver == 'minio' and not (config.minio.endpoint and config.minio.access_key and config.minio.secret_key and config.minio.bucket):
        raise ConfigError("Config error: Incorrect MinIO driver settings")

    if not (config.allowed_extensions and len(config.allowed_extensions.split(","))):
        raise ConfigError("Config error: Allowed extensions can not be empty")

    reference_config = [config.reference.file, config.reference.table, config.reference.local_path_column, config.reference.driver_path_column]
    if any(reference_config) and not all(reference_config):
        raise ConfigError("Config error: Incorrect media reference settings")
