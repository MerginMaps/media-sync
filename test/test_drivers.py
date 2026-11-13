"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import pytest
from botocore.exceptions import ClientError

from config import config, resolve_s3_config
import drivers
from drivers import S3Driver, DriverError, guess_content_type


def _client_error(code):
    return ClientError({"Error": {"Code": code}}, "HeadBucket")


@pytest.fixture
def boto_client(mocker):
    """Mocked boto3 S3 client"""
    client = mocker.MagicMock()
    client.meta.region_name = None
    mocker.patch("drivers.boto3.client", return_value=client)
    return client


def _s3_driver(**settings):
    config.update({"DRIVER": "s3", "S3__BUCKET": "bucket"})
    config.update({f"S3__{key.upper()}": value for key, value in settings.items()})
    resolve_s3_config(config)
    return S3Driver(config)


@pytest.mark.parametrize(
    "path,content_type",
    [
        ("photo.jpg", "image/jpeg"),
        ("photo.JPEG", "image/jpeg"),
        ("img.png", "image/png"),
        ("img.heic", "image/heic"),
        ("doc.pdf", "application/pdf"),
        ("video.mp4", "video/mp4"),
        ("unknown.xyz123", "application/octet-stream"),
        ("no_extension", "application/octet-stream"),
    ],
)
def test_guess_content_type(path, content_type):
    assert guess_content_type(path) == content_type


@pytest.mark.parametrize(
    "settings,base_url",
    [
        # AWS S3
        (
            {"region": "eu-west-1"},
            "https://bucket.s3.eu-west-1.amazonaws.com",
        ),
        (
            {"region": "eu-west-1", "endpoint": "s3.eu-west-1.amazonaws.com"},
            "https://bucket.s3.eu-west-1.amazonaws.com",
        ),
        # S3-compatible server
        ({"endpoint": "localhost:9000"}, "https://localhost:9000/bucket"),
        (
            {"endpoint": "localhost:9000", "secure": False},
            "http://localhost:9000/bucket",
        ),
        ({"endpoint": "http://localhost:9000/"}, "http://localhost:9000/bucket"),
        (
            {"endpoint": "https://s3.example.com", "addressing_style": "virtual"},
            "https://bucket.s3.example.com",
        ),
        (
            {"endpoint": "localhost:9000", "public_url": "https://files.example.com/"},
            "https://files.example.com",
        ),
    ],
)
def test_s3_driver_base_url(boto_client, settings, base_url):
    driver = _s3_driver(**settings)
    assert driver.base_url == base_url


def test_s3_driver_upload(boto_client):
    driver = _s3_driver(endpoint="localhost:9000", bucket_subpath="sub")
    dest = driver.upload_file("/tmp/images/photo.jpg", "images/photo.jpg")

    assert dest == "https://localhost:9000/bucket/sub/images/photo.jpg"
    boto_client.upload_file.assert_called_once_with(
        "/tmp/images/photo.jpg",
        "bucket",
        "sub/images/photo.jpg",
        ExtraArgs={"ContentType": "image/jpeg"},
    )

    boto_client.upload_file.side_effect = _client_error("AccessDenied")
    with pytest.raises(DriverError, match="S3 driver error"):
        driver.upload_file("/tmp/photo.jpg", "photo.jpg")


def test_s3_driver_bucket(boto_client):
    # existing bucket is not created
    _s3_driver(endpoint="localhost:9000")
    boto_client.create_bucket.assert_not_called()

    # missing bucket is created
    boto_client.head_bucket.side_effect = _client_error("404")
    _s3_driver(endpoint="localhost:9000")
    boto_client.create_bucket.assert_called_once_with(Bucket="bucket")

    # outside of us-east-1 region location has to be specified
    boto_client.create_bucket.reset_mock()
    boto_client.meta.region_name = "eu-west-1"
    _s3_driver(region="eu-west-1")
    boto_client.create_bucket.assert_called_once_with(
        Bucket="bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )

    # missing bucket is not created when disabled
    boto_client.create_bucket.reset_mock()
    with pytest.raises(DriverError, match="bucket bucket does not exist"):
        _s3_driver(region="eu-west-1", create_bucket=False)
    boto_client.create_bucket.assert_not_called()

    # other errors are reported
    boto_client.head_bucket.side_effect = _client_error("403")
    with pytest.raises(DriverError, match="S3 driver init error"):
        _s3_driver(region="eu-west-1")


@pytest.mark.parametrize(
    "settings,endpoint_url,region,base_url",
    [
        # AWS S3 configured with legacy minio driver, minio-py detected region of bucket by itself
        (
            {"endpoint": "s3.amazonaws.com", "secure": 1},
            None,
            None,
            "https://s3.amazonaws.com/bucket",
        ),
        (
            {"endpoint": "s3.amazonaws.com", "secure": 1, "region": "eu-west-1"},
            None,
            "eu-west-1",
            "https://bucket.s3.eu-west-1.amazonaws.com",
        ),
        (
            {"endpoint": "s3.eu-central-1.amazonaws.com", "secure": 1},
            None,
            "eu-central-1",
            "https://s3.eu-central-1.amazonaws.com/bucket",
        ),
        (
            {"endpoint": "s3-us-gov-west-1.amazonaws.com", "secure": 1},
            None,
            "us-gov-west-1",
            "https://s3-us-gov-west-1.amazonaws.com/bucket",
        ),
        (
            {"endpoint": "s3.amazonaws.com", "region": "eu-west-1"},
            None,
            "eu-west-1",
            "http://bucket.s3.eu-west-1.amazonaws.com",
        ),
        # other endpoints are passed to boto3
        (
            {"endpoint": "s3.dualstack.eu-west-1.amazonaws.com", "secure": 1},
            "https://s3.dualstack.eu-west-1.amazonaws.com",
            None,
            "https://s3.dualstack.eu-west-1.amazonaws.com/bucket",
        ),
        (
            {"endpoint": "minio.example.com", "region": "us-east-1"},
            "http://minio.example.com",
            "us-east-1",
            "http://minio.example.com/bucket",
        ),
    ],
)
def test_s3_driver_legacy_minio_settings(
    boto_client, settings, endpoint_url, region, base_url
):
    """Existing deployments with minio driver keep working with the same URLs"""
    config.update({"DRIVER": "minio", "MINIO__BUCKET": "bucket"})
    config.update({f"MINIO__{key.upper()}": value for key, value in settings.items()})
    resolve_s3_config(config)
    driver = S3Driver(config)

    client_kwargs = drivers.boto3.client.call_args.kwargs
    assert client_kwargs["endpoint_url"] == endpoint_url
    assert client_kwargs["region_name"] == region
    assert driver.base_url == base_url
