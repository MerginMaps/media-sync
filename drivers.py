"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import os
from pathlib import Path
import shutil
import typing
import warnings

from minio import Minio
from minio.error import S3Error
from urllib.parse import urlparse, urlunparse

from google.oauth2 import service_account
from googleapiclient.discovery import build, Resource
from googleapiclient.http import MediaFileUpload

from config import get_share_with


class DriverError(Exception):
    pass


class Driver:
    def __init__(self, config):
        self.config = config

    def upload_file(self, src, obj_path):
        """Copy object to destination and return path"""
        raise NotImplementedError


class LocalDriver(Driver):
    """Driver to work with local drive, for testing purpose mainly"""

    def __init__(self, config):
        super(LocalDriver, self).__init__(config)
        self.dest = config.local.dest

        try:
            if not os.path.exists(self.dest):
                os.makedirs(self.dest)
        except OSError as e:
            raise DriverError("Local driver init error: " + str(e))

    def upload_file(self, src, obj_path):
        dest = os.path.join(self.dest, obj_path)
        dest_dir = os.path.dirname(dest)
        try:
            if not os.path.exists(dest_dir):
                os.makedirs(dest_dir)
            shutil.copyfile(src, dest)
        except (shutil.SameFileError, OSError) as e:
            raise DriverError("Local driver error: " + str(e))
        return dest


class MinioDriver(Driver):
    """Driver to handle connection to minio-like server"""

    def __init__(self, config):
        super(MinioDriver, self).__init__(config)

        try:
            self.client = Minio(
                endpoint=config.minio.endpoint,
                access_key=config.minio.access_key,
                secret_key=config.minio.secret_key,
                secure=config.as_bool("minio.secure"),
                region=config.minio.region,
            )
            self.bucket = config.minio.bucket
            bucket_found = self.client.bucket_exists(self.bucket)
            if not bucket_found:
                self.client.make_bucket(self.bucket)

            self.bucket_subpath = None
            if hasattr(config.minio, "bucket_subpath"):
                if config.minio.bucket_subpath:
                    self.bucket_subpath = config.minio.bucket_subpath

            # construct base url for bucket
            scheme = "https://" if config.as_bool("minio.secure") else "http://"
            self.base_url = scheme + config.minio.endpoint + "/" + self.bucket
        except S3Error as e:
            raise DriverError("MinIO driver init error: " + str(e))

    def upload_file(self, src, obj_path):
        if self.bucket_subpath:
            obj_path = f"{self.bucket_subpath}/{obj_path}"
        try:
            res = self.client.fput_object(self.bucket, obj_path, src)
            dest = self.base_url + "/" + res.object_name
        except S3Error as e:
            raise DriverError("MinIO driver error: " + str(e))
        return dest


class GoogleDriveDriver(Driver):
    """Driver to handle connection to Google Drive"""

    def __init__(self, config):
        super(GoogleDriveDriver, self).__init__(config)

        try:
            self._credentials = service_account.Credentials.from_service_account_file(
                Path(config.google_drive.service_account_file),
                scopes=["https://www.googleapis.com/auth/drive.file"],
            )

            self._service: Resource = build(
                "drive", "v3", credentials=self._credentials
            )

            self._folder = config.google_drive.folder
            folder_exists, self._folder_id = self._folder_exists(self._folder)

            if not folder_exists:
                self._folder_id = self._create_folder(self._folder)

            for email in get_share_with(config.google_drive):
                if email:
                    self._share_with(email)

        except Exception as e:
            raise DriverError("GoogleDrive driver init error: " + str(e))

    def upload_file(self, src: str, obj_path: str):
        try:
            file_metadata = {
                "name": obj_path,
                "parents": [self._folder_id],
            }
            media = MediaFileUpload(src)

            file = (
                self._service.files()
                .create(body=file_metadata, media_body=media, fields="id")
                .execute()
            )

            file_id = file.get("id")

        except Exception as e:
            raise DriverError("GoogleDrive driver error: " + str(e))

        return self._file_link(file_id)

    def _folder_exists(self, folder_name: str) -> typing.Tuple[bool, str]:
        """Check if a folder with the specified name exists. Return boolean and folder ID if exists."""

        # Query to check if a folder with the specified name exists
        try:
            query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder'"
            results = (
                self._service.files().list(q=query, fields="files(id, name)").execute()
            )
            items = results.get("files", [])
        except Exception as e:
            raise DriverError("Google Drive folder exists error: " + str(e))

        if len(items) > 1:
            warnings.warn(
                f"Multiple folders with name '{folder_name}' found. Using the first one found."
            )

        if items:
            return True, items[0]["id"]
        else:
            return False, ""

    def _create_folder(self, folder_name: str) -> str:
        file_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }

        try:
            folder = (
                self._service.files().create(body=file_metadata, fields="id").execute()
            )
            return folder.get("id")
        except Exception as e:
            raise DriverError("Google Drive create folder error: " + str(e))

    def _file_link(self, file_id: str):
        """Get a link to the file in Google Drive."""
        try:
            file = (
                self._service.files()
                .get(fileId=file_id, fields="webViewLink")
                .execute()
            )
            return file.get("webViewLink")
        except Exception as e:
            raise DriverError("Google Drive file link error: " + str(e))

    def _has_already_permission(self, email: str) -> bool:
        """Check if email already has permission to the folder."""
        try:
            # List all permissions for the file
            permissions = (
                self._service.permissions()
                .list(
                    fileId=self._folder_id,
                    fields="permissions(id, emailAddress, role, type)",
                )
                .execute()
            )

            # Check if email exists in permissions
            for permission in permissions.get("permissions", []):
                if permission.get("emailAddress", "").lower() == email.lower():
                    return True

        except Exception as e:
            raise DriverError("Google Drive has permission error: " + str(e))

        return False

    def _share_with(self, email: str) -> None:
        """Share the folder with the specified email."""
        if not self._has_already_permission(email):
            try:
                permission = {
                    "type": "user",
                    "role": "writer",
                    "emailAddress": email,
                }
                self._service.permissions().create(
                    fileId=self._folder_id, body=permission
                ).execute()
            except Exception as e:
                raise DriverError("Google Drive sharing folder error: " + str(e))


def create_driver(config):
    """Create driver object based on type defined in config"""
    driver = None
    if config.driver == "local":
        driver = LocalDriver(config)
    elif config.driver == "minio":
        driver = MinioDriver(config)
    elif config.driver == "google_drive":
        driver = GoogleDriveDriver(config)
    return driver
