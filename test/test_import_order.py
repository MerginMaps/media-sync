"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Runs in a fresh interpreter - in pytest, conftest.py imports mergin first and would hide the issue.
# The module under test is imported first, mergin (truststore injection) afterwards, as any entry point eventually does.
# Creating a boto3 client needs no network, but builds the SSL context that fails with RecursionError
# when botocore was imported before mergin injected truststore.
CHECK_SCRIPT = """
import {module}
import mergin
import boto3

boto3.client(
    "s3",
    endpoint_url="http://localhost:9000",
    region_name="us-east-1",
    aws_access_key_id="key",
    aws_secret_access_key="secret",
)
"""


@pytest.mark.parametrize("module", ["drivers", "config", "media_sync", "media_sync_daemon"])
def test_import_order_boto3_client(module):
    """Importing any module first must not break boto3 - mergin (truststore injection) has to be imported before boto3, see drivers.py"""
    result = subprocess.run(
        [sys.executable, "-c", CHECK_SCRIPT.format(module=module)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
