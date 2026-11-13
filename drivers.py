"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import os
from pathlib import Path
import shutil
import typing
import re
import enum
import mimetypes
from urllib.parse import urlparse, urlunparse

# IMPORTANT: mergin must be imported before boto3/botocore - keep this import first.
# On import, mergin.client calls truststore.inject_into_ssl(), which replaces ssl.SSLContext.
# botocore binds SSLContext at its import time - if that happens before the injection, it keeps
# the original class and boto3 client creation fails with RecursionError.
# Doing it here (the only module importing boto3) makes every entry point safe regardless of its import order.
import mergin  # noqa: F401

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from google.oauth2 import service_account
from googleapiclient.discovery import build, Resource
from googleapiclient.http import MediaFileUpload

from log_functions import logger

# standard AWS S3 endpoints - s3.amazonaws.com, s3.<region>.amazonaws.com or legacy s3-<region>.amazonaws.com
AWS_S3_ENDPOINT_RE = re.compile(
    r"^s3(?:[.-](?P<region>[a-z]{2}(?:-[a-z]+)+-\d+))?\.amazonaws\.com$",
    re.IGNORECASE,
)


class DriverType(enum.Enum):
    LOCAL = "local"
    S3 = "s3"
    # deprecated alias of S3
    MINIO = "minio"
    GOOGLE_DRIVE = "google_drive"

    def __eq__(self, value):
        if isinstance(value, str):
            return self.value == value

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.value


class DriverError(Exception):
    pass


def guess_content_type(path):
    """Guess media type of file from its extension, defaults to generic binary type"""
    content_type, _ = mimetypes.guess_type(path)
    return content_type or "application/octet-stream"


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


class S3Driver(Driver):
    """Driver to handle connection to AWS S3 or S3-compatible server"""

    def __init__(self, config):
        super(S3Driver, self).__init__(config)
        s3 = config.s3

        self.bucket = s3.bucket
        self.bucket_subpath = s3.get("bucket_subpath") or None
        addressing_style = s3.get("addressing_style") or "auto"

        endpoint_url = None
        if s3.get("endpoint"):
            endpoint_url = s3.endpoint
            if "://" not in endpoint_url:
                scheme = "https" if s3.get("secure", True) else "http"
                endpoint_url = f"{scheme}://{endpoint_url}"
            endpoint_url = endpoint_url.rstrip("/")

        # AWS S3 endpoint (e.g. s3.amazonaws.com from legacy minio settings) is not passed to boto3,
        # so it uses regional endpoints and follows redirects to bucket region if region is not set
        client_endpoint_url = endpoint_url
        region = s3.get("region") or None
        host = urlparse(endpoint_url).hostname if endpoint_url else None
        if aws_endpoint := AWS_S3_ENDPOINT_RE.match(host or ""):
            client_endpoint_url = None
            region = region or aws_endpoint.group("region")

        try:
            self.client = boto3.client(
                "s3",
                endpoint_url=client_endpoint_url,
                region_name=region,
                # if keys are not set, default AWS credentials chain is used (env variables, IAM role, ...)
                aws_access_key_id=s3.get("access_key") or None,
                aws_secret_access_key=s3.get("secret_key") or None,
                config=BotoConfig(
                    signature_version="s3v4",
                    s3={"addressing_style": addressing_style},
                    # checksums added by default in recent boto3 are not supported by all S3-compatible servers
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
            )
            self._ensure_bucket(s3.get("create_bucket", True))
        except (ClientError, BotoCoreError, ValueError) as e:
            raise DriverError("S3 driver init error: " + str(e))

        # construct base url for bucket
        if s3.get("public_url"):
            self.base_url = s3.public_url.rstrip("/")
        elif endpoint_url is None:
            self.base_url = f"https://{self.bucket}.s3.{s3.region}.amazonaws.com"
        elif s3.get("region") and "amazonaws" in endpoint_url.lower():
            scheme = urlparse(endpoint_url).scheme
            self.base_url = f"{scheme}://{self.bucket}.s3.{s3.region}.amazonaws.com"
        elif addressing_style == "virtual":
            url = urlparse(endpoint_url)
            self.base_url = urlunparse(
                url._replace(netloc=f"{self.bucket}.{url.netloc}")
            )
        else:
            self.base_url = endpoint_url + "/" + self.bucket

    def _ensure_bucket(self, create_bucket):
        """Check that bucket exists, optionally create it"""
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code not in ("404", "NoSuchBucket", "NotFound"):
                raise
            if not create_bucket:
                raise DriverError(
                    f"S3 driver init error: bucket {self.bucket} does not exist"
                )

        # us-east-1 is the default location and it can not be specified
        region = self.client.meta.region_name
        location = {}
        if region and region != "us-east-1":
            location["CreateBucketConfiguration"] = {"LocationConstraint": region}
        self.client.create_bucket(Bucket=self.bucket, **location)

    def upload_file(self, src, obj_path):
        if self.bucket_subpath:
            obj_path = f"{self.bucket_subpath}/{obj_path}"
        try:
            self.client.upload_file(
                src,
                self.bucket,
                obj_path,
                ExtraArgs={"ContentType": guess_content_type(src)},
            )
        except (ClientError, BotoCoreError, S3UploadFailedError) as e:
            raise DriverError("S3 driver error: " + str(e))
        return self.base_url + "/" + obj_path


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
            self._folder_id = self._folder_exists(self._folder)

            if not self._folder_id:
                self._folder_id = self._create_folder(self._folder)

            for email in self._get_share_with(config.google_drive):
                if email:
                    self._share_with(email)

        except Exception as e:
            raise DriverError("GoogleDrive driver init error: " + str(e))

    def upload_file(self, src: str, obj_path: str) -> str:
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

    def _folder_exists(self, folder_name: str) -> typing.Optional[str]:
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
            logger.warning(
                f"Multiple folders with name '{folder_name}' found. Using the first one found."
            )

        if items:
            return items[0]["id"]
        else:
            return None

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

    def _file_link(self, file_id: str) -> str:
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

            return any(
                permission.get("emailAddress", "").lower() == email.lower()
                for permission in permissions.get("permissions", [])
            )

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

    def _get_share_with(self, config_google_drive) -> typing.List[str]:
        email_regex = re.compile(r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)")

        emails_to_share_with = []
        if isinstance(config_google_drive.share_with, str):
            if email_regex.match(config_google_drive.share_with):
                emails_to_share_with.append(config_google_drive.share_with)
        elif isinstance(config_google_drive.share_with, list):
            for email in config_google_drive.share_with:
                if email_regex.match(email):
                    emails_to_share_with.append(email)
        else:
            raise DriverError(
                "Google Drive sharing: Incorrect GoogleDrive shared_with settings"
            )

        if not emails_to_share_with:
            logger.warning("Google Drive sharing: Not shared with any user")

        return emails_to_share_with


def create_driver(config):
    """Create driver object based on type defined in config"""
    driver = None
    if config.driver == DriverType.LOCAL:
        driver = LocalDriver(config)
    elif config.driver in (DriverType.S3, DriverType.MINIO):
        driver = S3Driver(config)
    elif config.driver == DriverType.GOOGLE_DRIVE:
        driver = GoogleDriveDriver(config)
    return driver
