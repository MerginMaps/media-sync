"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT

Tests of S3 driver with S3-compatible server, no Mergin project is needed.
Start the server with test/s3_servers/start.sh and export printed TEST_S3_* variables.
"""

import os
import uuid

import pytest

from config import config, resolve_s3_config
from drivers import S3Driver, DriverError

from .conftest import (
    TEST_DATA_DIR,
    S3_ENDPOINT,
    S3_ACCESS_KEY,
    S3_SECRET_KEY,
    S3_REGION,
    S3_CREATE_BUCKET,
)

pytestmark = pytest.mark.skipif(
    not S3_ENDPOINT, reason="S3-compatible server for testing is not configured"
)


def _configure(bucket, **settings):
    config.update(
        {
            "DRIVER": "s3",
            "S3__ENDPOINT": S3_ENDPOINT,
            "S3__SECURE": False,
            "S3__ACCESS_KEY": S3_ACCESS_KEY,
            "S3__SECRET_KEY": S3_SECRET_KEY,
            "S3__REGION": S3_REGION,
            "S3__CREATE_BUCKET": S3_CREATE_BUCKET,
            "S3__BUCKET": bucket,
            **{f"S3__{key.upper()}": value for key, value in settings.items()},
        }
    )
    resolve_s3_config(config)


def test_s3_driver_server():
    """Upload files with correct content type to bucket, which is created if server allows it"""
    # new bucket tests its creation, otherwise bucket prepared by start.sh is used
    can_create_bucket = S3_CREATE_BUCKET.lower() != "false"
    bucket = f"test-{uuid.uuid4().hex[:8]}" if can_create_bucket else "test"
    subpath = f"test-{uuid.uuid4().hex[:8]}"
    _configure(bucket, bucket_subpath=subpath)

    driver = S3Driver(config)
    dest = driver.upload_file(os.path.join(TEST_DATA_DIR, "img1.png"), "img1.png")
    dest_jpg = driver.upload_file(
        os.path.join(TEST_DATA_DIR, "images", "img2.jpg"), "images/img2.jpg"
    )

    assert dest == f"http://{S3_ENDPOINT}/{bucket}/{subpath}/img1.png"
    assert dest_jpg == f"http://{S3_ENDPOINT}/{bucket}/{subpath}/images/img2.jpg"
    head = driver.client.head_object(Bucket=bucket, Key=f"{subpath}/img1.png")
    assert head["ContentType"] == "image/png"
    head = driver.client.head_object(Bucket=bucket, Key=f"{subpath}/images/img2.jpg")
    assert head["ContentType"] == "image/jpeg"


def test_s3_driver_server_missing_bucket():
    """Missing bucket is reported when it should not be created"""
    _configure(f"missing-{uuid.uuid4().hex[:8]}", create_bucket=False)
    with pytest.raises(DriverError, match="does not exist"):
        S3Driver(config)
