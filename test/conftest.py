import os
import tempfile
import shutil

import pytest

from mergin import MerginClient, ClientError
from config import config

SERVER_URL = os.environ.get("TEST_MERGIN_URL")
API_USER = os.environ.get("TEST_API_USERNAME")
USER_PWD = os.environ.get("TEST_API_PASSWORD")
TMP_DIR = tempfile.gettempdir()
TEST_DATA_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "test_data")
WORKSPACE = os.environ.get("TEST_API_WORKSPACE")
MINIO_URL = os.environ.get("TEST_MINIO_URL")
MINIO_ACCESS_KEY = os.environ.get("TEST_MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = os.environ.get("TEST_MINIO_SECRET_KEY")


@pytest.fixture(scope="function")
def mc() -> MerginClient:
    assert SERVER_URL and API_USER and USER_PWD
    return MerginClient(SERVER_URL, login=API_USER, password=USER_PWD)


@pytest.fixture(scope="function", autouse=True)
def setup_config():
    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png", "jpg"],
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "MERGIN__PROJECT_NAME": "",
            "PROJECT_WORKING_DIR": "",
            "OPERATION_MODE": "copy",
            "DRIVER": "",
            "REFERENCES": [],
            "BASE_PATH": "",
            "MINIO__ENDPOINT": "",
            "MINIO__ACCESS_KEY": "",
            "MINIO__SECRET_KEY": "",
            "MINIO__BUCKET": "",
            "MINIO__BUCKET_SUBPATH": "",
            "MINIO__SECURE": False,
            "MINIO__REGION": "",
        }
    )


def cleanup(mc, project, dirs):
    """Cleanup leftovers from previous test if needed such as remote project and local directories"""
    try:
        print("Deleting project on Mergin server: " + project)
        mc.delete_project_now(project)
    except ClientError as e:
        print("Deleting project error: " + str(e))
    for d in dirs:
        if os.path.exists(d):
            shutil.rmtree(d)


def prepare_mergin_project(mc, project_name):
    # copy test data to some temp dir, upload to mergin server and clean up working dir
    project_dir = os.path.join(TMP_DIR, project_name + "_create")
    cleanup(mc, project_name, [project_dir])

    shutil.copytree(os.path.join(TEST_DATA_DIR), project_dir)
    mc.create_project_and_push(project_name, project_dir)
