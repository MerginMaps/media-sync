"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import pytest
import os
import shutil
import sqlite3
import tempfile
from mergin import MerginClient, ClientError

from drivers import MinioDriver, LocalDriver
from media_sync import main, config, mc_pull, media_sync_push, mc_download, MediaSyncError

SERVER_URL = os.environ.get('TEST_MERGIN_URL')
API_USER = os.environ.get('TEST_API_USERNAME')
USER_PWD = os.environ.get('TEST_API_PASSWORD')
TMP_DIR = tempfile.gettempdir()
TEST_DATA_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'test_data')
MINIO_URL = os.environ.get('TEST_MINIO_URL')
MINIO_ACCESS_KEY = os.environ.get('TEST_MINIO_ACCESS_KEY')
MINIO_SECRET_KEY = os.environ.get('TEST_MINIO_SECRET_KEY')


@pytest.fixture(scope='function')
def mc():
    assert SERVER_URL and API_USER and USER_PWD
    return MerginClient(SERVER_URL, login=API_USER, password=USER_PWD)


def cleanup(mc, project, dirs):
    """ Cleanup leftovers from previous test if needed such as remote project and local directories """
    try:
        print("Deleting project on Mergin server: " + project)
        mc.delete_project(project)
    except ClientError as e:
        print("Deleting project error: " + str(e))
    for d in dirs:
        if os.path.exists(d):
            shutil.rmtree(d)


def prepare_mergin_project(mc, project_name):
    # copy test data to some temp dir, upload to mergin server and clean up working dir
    project_dir = os.path.join(TMP_DIR, project_name + '_create')
    cleanup(mc, API_USER + "/" + project_name, [project_dir])

    shutil.copytree(os.path.join(TEST_DATA_DIR), project_dir)
    mc.create_project_and_push(project_name, project_dir)


def test_sync(mc):
    """ Test media sync starting from fresh project and download and then following scenarios

    - simple full copy of all images without reference -> mergin server is not aware of any changes
    - copy only .jpg images -> .png file is ignored
    - copy + change reference table in gpkg -> geopackage is modified
    - move mode -> .jpg file is removed from mergin server
    - switch to .png images and enable lookup only in /images subdirectory -> nothing to do
    """
    project_name = "mediasync_test"
    full_project_name = API_USER + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, project_name + '_work')  # working dir for mediasync
    driver_dir = os.path.join(TMP_DIR, project_name + '_driver')  # destination dir for 'local' driver

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, project_name)

    # patch config to fit testing purposes
    config.update({
        'ALLOWED_EXTENSIONS': ".png,.jpg",
        'MERGIN__USERNAME': API_USER,
        'MERGIN__PASSWORD': USER_PWD,
        'MERGIN__URL': SERVER_URL,
        'MERGIN__PROJECT_NAME': full_project_name,
        'PROJECT_WORKING_DIR': work_project_dir,
        'DRIVER': "local",
        'LOCAL__DEST': driver_dir,
        'OPERATION_MODE_MOVE': False,
        'REFERENCE__ENABLED': False
    })

    main()
    # check synced images
    assert os.path.exists(os.path.join(driver_dir, "img1.png"))
    assert os.path.exists(os.path.join(driver_dir, "images", "img2.jpg"))

    # files in mergin project still exist (copy mode)
    assert os.path.exists(os.path.join(work_project_dir, "img1.png"))
    assert os.path.exists(os.path.join(work_project_dir, "images", "img2.jpg"))

    # no reference changes - no changes to mergin project at all
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v1"
    # remove artifacts from previous run
    shutil.rmtree(work_project_dir)
    shutil.rmtree(driver_dir)

    # limit sync to .jpg only
    config.update({"allowed_extensions": ".jpg"})
    main()
    # check synced images
    assert not os.path.exists(os.path.join(driver_dir, "img1.png"))
    assert os.path.exists(os.path.join(driver_dir, "images", "img2.jpg"))
    shutil.rmtree(work_project_dir)
    shutil.rmtree(driver_dir)

    # enable references updates
    config.update({
        'REFERENCE__ENABLED': True,
        'REFERENCE__FILE': "survey.gpkg",
        'REFERENCE__TABLE': "notes",
        'REFERENCE__LOCAL_PATH_FIELD': "photo",
        'REFERENCE__DRIVER_PATH_FIELD': "ext_url",
    })

    main()
    # check synced images
    copied_file = os.path.join(driver_dir, "images", "img2.jpg")
    assert os.path.exists(copied_file)

    # check references have been changed and pushed
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v2"
    gpkg_conn = sqlite3.connect(os.path.join(work_project_dir, config.reference.file))
    gpkg_cur = gpkg_conn.cursor()
    sql = f"SELECT count(*) FROM {config.reference.table} WHERE {config.reference.driver_path_field}='{copied_file}'"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 1
    shutil.rmtree(work_project_dir)
    shutil.rmtree(driver_dir)

    # change to 'move' mode
    config.update({
        'OPERATION_MODE_MOVE': True
    })

    main()
    # check synced images
    copied_file = os.path.join(driver_dir, "images", "img2.jpg")
    assert os.path.exists(copied_file)
    # file in mergin project do not exist anymore
    assert not os.path.exists(os.path.join(work_project_dir, "images", "img2.jpg"))
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v3"
    moved_file = next((f for f in project_info["files"] if f["path"] == "images/img2.jpg"), None)
    assert not moved_file
    gpkg_conn = sqlite3.connect(os.path.join(work_project_dir, config.reference.file))
    gpkg_cur = gpkg_conn.cursor()
    sql = f"SELECT count(*) FROM {config.reference.table} WHERE {config.reference.local_path_field} is Null AND {config.reference.driver_path_field}='{copied_file}'"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 1
    shutil.rmtree(work_project_dir)
    shutil.rmtree(driver_dir)

    # change mode to .png and also base project path to 'images' - nothing should be done
    config.update({"allowed_extensions": ".png", "base_path": "images"})
    main()
    # check synced images
    copied_file = os.path.join(driver_dir, "images", "img1.png")
    assert not os.path.exists(copied_file)
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v3"


def test_pull_and_sync(mc):
    """ Test media sync if mergin project is ahead of locally downloaded one """
    project_name = "mediasync_test_pull"
    full_project_name = API_USER + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, project_name + '_work')
    driver_dir = os.path.join(TMP_DIR, project_name + '_driver')

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, project_name)

    config.update({
        'MERGIN__USERNAME': API_USER,
        'MERGIN__PASSWORD': USER_PWD,
        'MERGIN__URL': SERVER_URL,
        'MERGIN__PROJECT_NAME': full_project_name,
        'PROJECT_WORKING_DIR': work_project_dir,
        'DRIVER': "local",
        'LOCAL__DEST': driver_dir,
        'OPERATION_MODE_MOVE': False,
        'REFERENCE__ENABLED': False,
        "ALLOWED_EXTENSIONS": ".png,.jpg",
        "BASE_PATH": None
    })
    # initial run
    main()

    # let's update project on server
    project_dir = os.path.join(TMP_DIR, project_name + '_create')
    shutil.copyfile(os.path.join(project_dir, 'img1.png'), os.path.join(project_dir, 'img_new.png'))
    mc.push_project(project_dir)
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v2"

    files_to_sync = mc_pull(mc)
    driver = LocalDriver(config)
    media_sync_push(mc, driver, files_to_sync)
    # check synced image
    assert os.path.exists(os.path.join(driver_dir, "img_new.png"))


def test_minio_backend(mc):
    """ Test media sync connected to minio backend (needs local service running) """
    project_name = "mediasync_test_minio"
    full_project_name = API_USER + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, project_name + '_work')

    cleanup(mc, full_project_name, [work_project_dir])
    prepare_mergin_project(mc, project_name)

    # patch config to fit testing purposes
    config.update({
        'MERGIN__USERNAME': API_USER,
        'MERGIN__PASSWORD': USER_PWD,
        'MERGIN__URL': SERVER_URL,
        'MERGIN__PROJECT_NAME': full_project_name,
        'PROJECT_WORKING_DIR': work_project_dir,
        'OPERATION_MODE_MOVE': False,
        'REFERENCE__ENABLED': False,
        'DRIVER': "minio",
        'MINIO__ENDPOINT': MINIO_URL,
        'MINIO__ACCESS_KEY': MINIO_ACCESS_KEY,
        'MINIO__SECRET_KEY': MINIO_SECRET_KEY,
        'MINIO__BUCKET': 'test'
    })

    main()
    # check synced images
    driver = MinioDriver(config)
    minio_objects = [o.object_name for o in driver.client.list_objects('test', recursive=True)]
    assert "img1.png" in minio_objects
    assert "images/img2.jpg" in minio_objects

    # files in mergin project still exist (copy mode)
    assert os.path.exists(os.path.join(work_project_dir, "img1.png"))
    assert os.path.exists(os.path.join(work_project_dir, "images", "img2.jpg"))


def test_sync_failures(mc):
    """ Test common sync failures """
    project_name = "mediasync_fail"
    full_project_name = API_USER + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, project_name + '_work')  # working dir for mediasync
    driver_dir = os.path.join(TMP_DIR, project_name + '_driver')  # destination dir for 'local' driver

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, project_name)
    config.update({
        'ALLOWED_EXTENSIONS': ".png,.jpg",
        'MERGIN__USERNAME': API_USER,
        'MERGIN__PASSWORD': USER_PWD,
        'MERGIN__URL': SERVER_URL,
        'MERGIN__PROJECT_NAME': full_project_name,
        'PROJECT_WORKING_DIR': work_project_dir,
        'DRIVER': "local",
        'LOCAL__DEST': driver_dir,
        'OPERATION_MODE_MOVE': False,
        'REFERENCE__ENABLED': False
    })
    driver = LocalDriver(config)
    files_to_sync = mc_download(mc)
    # "remove" .mergin hidden dir to mimic broken working directory
    os.rename(os.path.join(work_project_dir, '.mergin'), os.path.join(work_project_dir, '.hidden'))

    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, files_to_sync)
    assert "The project working directory does not seem to contain Mergin project" in exc.value.args[0]
    os.rename(os.path.join(work_project_dir, '.hidden'), os.path.join(work_project_dir, '.mergin'))

    # "remove" working project
    os.rename(work_project_dir, work_project_dir + "_renamed")
    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, files_to_sync)
    assert "The project working directory does not exist" in exc.value.args[0]
    os.rename(work_project_dir + "_renamed", work_project_dir)

    # incorrect gpkg details for reference table
    config.update({
        'REFERENCE__ENABLED': True,
        'REFERENCE__FILE': "survey.gpkg",
        'REFERENCE__TABLE': "notes_error",
        'REFERENCE__LOCAL_PATH_FIELD': "photo",
        'REFERENCE__DRIVER_PATH_FIELD': "ext_url"
    })
    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, files_to_sync)
    assert "SQLITE error" in exc.value.args[0]
    config.update({'REFERENCE__TABLE': "notes"})

    # introduce some unknown local file
    shutil.copyfile(os.path.join(work_project_dir, "survey.gpkg"), os.path.join(work_project_dir, "new.gpkg"))
    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, files_to_sync)
    assert "There are changes to be added - it should never happen" in exc.value.args[0]
    os.remove(os.path.join(work_project_dir, "new.gpkg"))

    # remove local file which should have been synced -> just skipped
    os.remove(os.path.join(work_project_dir, "img1.png"))
    shutil.rmtree(driver_dir)
    media_sync_push(mc, driver, files_to_sync)
    # check synced images
    assert not os.path.exists(os.path.join(driver_dir, "img1.png"))
    assert os.path.exists(os.path.join(driver_dir, "images", "img2.jpg"))
