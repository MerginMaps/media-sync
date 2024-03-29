"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import os
import shutil
from minio import Minio
from minio.error import S3Error
from urllib.parse import urlparse, urlunparse


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


def create_driver(config):
    """Create driver object based on type defined in config"""
    driver = None
    if config.driver == "local":
        driver = LocalDriver(config)
    elif config.driver == "minio":
        driver = MinioDriver(config)
    return driver
