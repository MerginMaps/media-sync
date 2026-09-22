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


def _validate_project(project, driver, index):
    """Validate a single project entry inside the projects list."""
    if not hasattr(project, "project_name") or not project.project_name:
        raise ConfigError(
            f"Config error: Project #{index} is missing 'project_name'"
        )

    if driver == DriverType.LOCAL and not (
        hasattr(project, "dest") and project.dest
    ):
        raise ConfigError(
            f"Config error: Project '{project.project_name}' is missing 'dest' for local driver"
        )

    if driver == DriverType.MINIO:
        # bucket_subpath is optional for minio – no mandatory check needed
        pass

    if driver == DriverType.GOOGLE_DRIVE and not (
        hasattr(project, "folder") and project.folder
    ):
        raise ConfigError(
            f"Config error: Project '{project.project_name}' is missing 'folder' for google_drive driver"
        )

    if not hasattr(project, "base_path") or project.base_path is None:
        project.update({"base_path": ""})

    if not hasattr(project, "references") or project.references is None:
        project.update({"references": []})

    if not isinstance(project.references, list):
        raise ConfigError(
            f"Config error: Project '{project.project_name}': 'references' must be a list"
        )

    for ref in project.references:
        if not all(
            hasattr(ref, attr)
            for attr in ["file", "table", "local_path_column", "driver_path_column"]
        ):
            raise ConfigError(
                f"Config error: Project '{project.project_name}': incorrect media reference settings"
            )


def validate_config(config):
    """Validate config - make sure values are consistent"""

    if not (
        config.mergin.username and config.mergin.password and config.mergin.url
    ):
        raise ConfigError("Config error: Incorrect mergin settings")

    if not (
        config.driver == DriverType.LOCAL
        or config.driver == DriverType.MINIO
        or config.driver == DriverType.GOOGLE_DRIVE
    ):
        raise ConfigError("Config error: Unsupported driver")

    if config.operation_mode not in ["move", "copy"]:
        raise ConfigError("Config error: Unsupported operation mode")

    if config.driver == DriverType.MINIO and not (
        config.minio.endpoint
        and config.minio.access_key
        and config.minio.secret_key
        and config.minio.bucket
    ):
        raise ConfigError("Config error: Incorrect MinIO driver settings")

    if not (config.allowed_extensions and len(config.allowed_extensions)):
        raise ConfigError("Config error: Allowed extensions can not be empty")

    if config.driver == DriverType.GOOGLE_DRIVE and not (
        hasattr(config.google_drive, "service_account_file")
        and hasattr(config.google_drive, "share_with")
    ):
        raise ConfigError("Config error: Incorrect GoogleDrive driver settings")

    # Validate projects list
    if "projects" not in config or not config.projects:
        raise ConfigError("Config error: 'projects' list is missing or empty")

    if not isinstance(config.projects, list):
        raise ConfigError("Config error: 'projects' must be a list")

    projects = config.projects

    for i, project in enumerate(projects):
        _validate_project(project, config.driver, i)

    config.update({"projects": projects})


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
