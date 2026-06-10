"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import pytest
import os
import shutil
import sqlite3

from drivers import MinioDriver, LocalDriver, GoogleDriveDriver
from media_sync import (
    main,
    mc_pull,
    media_sync_push,
    mc_download,
    MediaSyncError,
)
from config import config, validate_config, ConfigError

from .conftest import (
    API_USER,
    WORKSPACE,
    TMP_DIR,
    USER_PWD,
    SERVER_URL,
    MINIO_URL,
    MINIO_ACCESS_KEY,
    MINIO_SECRET_KEY,
    GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE,
    GOOGLE_DRIVE_FOLDER,
    cleanup,
    prepare_mergin_project,
)

from .utils import google_drive_delete_folder, google_drive_list_files_in_folder


def test_sync(mc):
    """Test media sync starting from fresh project and download and then following scenarios

    - simple full copy of all images without reference -> mergin server is not aware of any changes
    - copy only .jpg images -> .png file is ignored
    - copy + change reference table in gpkg -> geopackage is modified
    - move mode -> .jpg file is removed from mergin server
    - switch to .png images and enable lookup only in /images subdirectory -> nothing to do
    """
    project_name = "mediasync_test"
    full_project_name = WORKSPACE + "/" + project_name
    driver_dir = os.path.join(TMP_DIR, project_name + "_driver")

    # working dir is derived automatically: TMP_DIR / full_project_name
    work_project_dir = os.path.join(TMP_DIR, full_project_name)

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    # patch config to fit testing purposes
    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png", "jpg"],
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "PROJECT_WORKING_DIR": TMP_DIR,
            "DRIVER": "local",
            "OPERATION_MODE": "copy",
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "dest": driver_dir,
                    "references": [
                        {
                            "file": None,
                            "table": None,
                            "local_path_column": None,
                            "driver_path_column": None,
                        }
                    ],
                }
            ],
        }
    )

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
    config.update({"allowed_extensions": ["jpg"]})
    main()
    # check synced images
    assert not os.path.exists(os.path.join(driver_dir, "img1.png"))
    assert os.path.exists(os.path.join(driver_dir, "images", "img2.jpg"))
    shutil.rmtree(work_project_dir)
    shutil.rmtree(driver_dir)

    # enable references updates
    config.update(
        {
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "dest": driver_dir,
                    "references": [
                        {
                            "file": "survey.gpkg",
                            "table": "notes",
                            "local_path_column": "photo",
                            "driver_path_column": "ext_url",
                        }
                    ],
                }
            ]
        }
    )

    main()
    # check synced images
    copied_file = os.path.join(driver_dir, "images", "img2.jpg")
    assert os.path.exists(copied_file)

    # check references have been changed and pushed
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v2"
    ref = config.projects[0].references[0]
    gpkg_conn = sqlite3.connect(os.path.join(work_project_dir, ref.file))
    gpkg_cur = gpkg_conn.cursor()
    sql = f"SELECT count(*) FROM {ref.table} WHERE {ref.driver_path_column}='{copied_file}'"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 1
    shutil.rmtree(work_project_dir)
    shutil.rmtree(driver_dir)

    # change to 'move' mode
    config.update({"OPERATION_MODE": "move"})

    main()
    # check synced images
    copied_file = os.path.join(driver_dir, "images", "img2.jpg")
    assert os.path.exists(copied_file)
    # file in mergin project do not exist anymore
    assert not os.path.exists(os.path.join(work_project_dir, "images", "img2.jpg"))
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v3"
    moved_file = next(
        (f for f in project_info["files"] if f["path"] == "images/img2.jpg"), None
    )
    assert not moved_file
    ref = config.projects[0].references[0]
    gpkg_conn = sqlite3.connect(os.path.join(work_project_dir, ref.file))
    gpkg_cur = gpkg_conn.cursor()
    sql = f"SELECT count(*) FROM {ref.table} WHERE {ref.local_path_column} is Null AND {ref.driver_path_column}='{copied_file}'"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 1
    shutil.rmtree(work_project_dir)
    shutil.rmtree(driver_dir)

    # change mode to .png and also base project path to 'images' - nothing should be done
    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png"],
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "base_path": "images",
                    "dest": driver_dir,
                    "references": [
                        {
                            "file": None,
                            "table": None,
                            "local_path_column": None,
                            "driver_path_column": None,
                        }
                    ],
                }
            ]
        }
    )
    main()    # check synced images
    copied_file = os.path.join(driver_dir, "images", "img1.png")
    assert not os.path.exists(copied_file)
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v3"


def test_pull_and_sync(mc):
    """Test media sync if mergin project is ahead of locally downloaded one"""
    project_name = "mediasync_test_pull"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, full_project_name)
    driver_dir = os.path.join(TMP_DIR, project_name + "_driver")

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    config.update(
        {
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "PROJECT_WORKING_DIR": TMP_DIR,
            "DRIVER": "local",
            "OPERATION_MODE": "copy",
            "ALLOWED_EXTENSIONS": ["png", "jpg"],
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "dest": driver_dir,
                    "references": [
                        {
                            "file": None,
                            "table": None,
                            "local_path_column": None,
                            "driver_path_column": None,
                        }
                    ],
                }
            ],
        }
    )
    # initial run
    main()

    # let's update project on server - create new .png file and modify reference .gpkg file
    project_dir = os.path.join(TMP_DIR, project_name + "_create")
    if os.path.exists(project_dir):
        shutil.rmtree(project_dir)
    shutil.copytree(work_project_dir, project_dir)
    shutil.copyfile(
        os.path.join(project_dir, "img1.png"), os.path.join(project_dir, "img_new.png")
    )
    gpkg_conn = sqlite3.connect(os.path.join(project_dir, "survey.gpkg"))
    gpkg_conn.enable_load_extension(True)
    gpkg_cur = gpkg_conn.cursor()
    gpkg_cur.execute('SELECT load_extension("mod_spatialite")')
    gpkg_cur.execute("update notes set photo = 'img_new.png' where name = 'test'")
    gpkg_conn.commit()
    mc.push_project(project_dir)
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v2"

    project = config.projects[0]
    files_to_sync = mc_pull(mc, project)
    driver = LocalDriver(config, project)
    media_sync_push(mc, driver, project, files_to_sync)
    # check synced image
    assert os.path.exists(os.path.join(driver_dir, "img_new.png"))


def test_minio_backend(mc):
    """Test media sync connected to minio backend (needs local service running)"""
    project_name = "mediasync_test_minio"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, full_project_name)

    cleanup(mc, full_project_name, [work_project_dir])
    prepare_mergin_project(mc, full_project_name)

    # patch config to fit testing purposes
    config.update(
        {
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "PROJECT_WORKING_DIR": TMP_DIR,
            "OPERATION_MODE": "copy",
            "DRIVER": "minio",
            "MINIO__ENDPOINT": MINIO_URL,
            "MINIO__ACCESS_KEY": MINIO_ACCESS_KEY,
            "MINIO__SECRET_KEY": MINIO_SECRET_KEY,
            "MINIO__BUCKET": "test",
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "references": [
                        {
                            "file": None,
                            "table": None,
                            "local_path_column": None,
                            "driver_path_column": None,
                        }
                    ],
                }
            ],
        }
    )

    main()
    # check synced images
    project = config.projects[0]
    driver = MinioDriver(config, project)
    minio_objects = [
        o.object_name for o in driver.client.list_objects("test", recursive=True)
    ]
    assert "img1.png" in minio_objects
    assert "images/img2.jpg" in minio_objects

    # files in mergin project still exist (copy mode)
    assert os.path.exists(os.path.join(work_project_dir, "img1.png"))
    assert os.path.exists(os.path.join(work_project_dir, "images", "img2.jpg"))

    cleanup(mc, full_project_name, [work_project_dir])
    prepare_mergin_project(mc, full_project_name)

    config.update(
        {
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "PROJECT_WORKING_DIR": TMP_DIR,
            "OPERATION_MODE": "copy",
            "DRIVER": "minio",
            "MINIO__ENDPOINT": MINIO_URL,
            "MINIO__ACCESS_KEY": MINIO_ACCESS_KEY,
            "MINIO__SECRET_KEY": MINIO_SECRET_KEY,
            "MINIO__BUCKET": "test1",
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "bucket_subpath": "subPath",
                    "references": [
                        {
                            "file": None,
                            "table": None,
                            "local_path_column": None,
                            "driver_path_column": None,
                        }
                    ],
                }
            ],
        }
    )

    main()
    # check synced images
    project = config.projects[0]
    driver = MinioDriver(config, project)
    minio_objects = [
        o.object_name for o in driver.client.list_objects("test1", recursive=True)
    ]
    assert "subPath/img1.png" in minio_objects
    assert "subPath/images/img2.jpg" in minio_objects


def test_sync_failures(mc):
    """Test common sync failures"""
    project_name = "mediasync_fail"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, full_project_name)
    driver_dir = os.path.join(TMP_DIR, project_name + "_driver")

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png", "jpg"],
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "PROJECT_WORKING_DIR": TMP_DIR,
            "DRIVER": "local",
            "OPERATION_MODE": "copy",
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "dest": driver_dir,
                    "base_path": "",
                    "references": [
                        {
                            "file": None,
                            "table": None,
                            "local_path_column": None,
                            "driver_path_column": None,
                        }
                    ],
                }
            ],
        }
    )
    project = config.projects[0]
    driver = LocalDriver(config, project)
    files_to_sync = mc_download(mc, project)
    # "remove" .mergin hidden dir to mimic broken working directory
    os.rename(
        os.path.join(work_project_dir, ".mergin"),
        os.path.join(work_project_dir, ".hidden"),
    )

    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, project, files_to_sync)
    assert (
        "The project working directory does not seem to contain Mergin project"
        in exc.value.args[0]
    )
    os.rename(
        os.path.join(work_project_dir, ".hidden"),
        os.path.join(work_project_dir, ".mergin"),
    )

    # "remove" working project
    os.rename(work_project_dir, work_project_dir + "_renamed")
    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, project, files_to_sync)
    assert "The project working directory does not exist" in exc.value.args[0]
    os.rename(work_project_dir + "_renamed", work_project_dir)

    # incorrect gpkg details for reference table
    config.update(
        {
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "dest": driver_dir,
                    "references": [
                        {
                            "file": "survey.gpkg",
                            "table": "notes_error",
                            "local_path_column": "photo",
                            "driver_path_column": "ext_url",
                        }
                    ],
                }
            ],
        }
    )
    project = config.projects[0]
    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, project, files_to_sync)
    assert "SQLITE error" in exc.value.args[0]

    config.update(
        {
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "dest": driver_dir,
                    "references": [
                        {
                            "file": "survey.gpkg",
                            "table": "notes",
                            "local_path_column": "photo",
                            "driver_path_column": "ext_url",
                        }
                    ],
                }
            ],
        }
    )
    project = config.projects[0]

    # introduce some unknown local file
    shutil.copyfile(
        os.path.join(work_project_dir, "survey.gpkg"),
        os.path.join(work_project_dir, "new.gpkg"),
    )
    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, project, files_to_sync)
    assert "There are changes to be added - it should never happen" in exc.value.args[0]
    os.remove(os.path.join(work_project_dir, "new.gpkg"))

    # remove local file which should have been synced -> just skipped
    os.remove(os.path.join(work_project_dir, "img1.png"))
    shutil.rmtree(driver_dir)
    media_sync_push(mc, driver, project, files_to_sync)
    # check synced images
    assert not os.path.exists(os.path.join(driver_dir, "img1.png"))
    assert os.path.exists(os.path.join(driver_dir, "images", "img2.jpg"))


def test_multiple_tables(mc):
    project_name = "mediasync_test_multiple"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, full_project_name)
    driver_dir = os.path.join(TMP_DIR, project_name + "_driver")

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    # patch config to fit testing purposes
    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png"],
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "PROJECT_WORKING_DIR": TMP_DIR,
            "DRIVER": "local",
            "OPERATION_MODE": "copy",
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "dest": driver_dir,
                    "references": [
                        {
                            "file": "survey.gpkg",
                            "table": "notes",
                            "local_path_column": "photo",
                            "driver_path_column": "ext_url",
                        },
                        {
                            "file": "survey.gpkg",
                            "table": "photos",
                            "local_path_column": "123_photo",
                            "driver_path_column": "ext_url",
                        },
                    ],
                }
            ],
        }
    )

    main()
    # check synced images
    assert os.path.exists(os.path.join(driver_dir, "img1.png"))

    # check references have been changed and pushed
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v2"

    copied_file = os.path.join(driver_dir, "img1.png")
    refs = config.projects[0].references
    # check that both tables were updated
    gpkg_conn = sqlite3.connect(os.path.join(work_project_dir, refs[0].file))
    gpkg_cur = gpkg_conn.cursor()
    sql = f"SELECT count(*) FROM {refs[0].table} WHERE {refs[0].driver_path_column}='{copied_file}'"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 1

    gpkg_conn = sqlite3.connect(os.path.join(work_project_dir, refs[1].file))
    gpkg_cur = gpkg_conn.cursor()
    sql = f"SELECT count(*) FROM {refs[1].table} WHERE {refs[1].driver_path_column}='{copied_file}'"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 1


@pytest.mark.parametrize(
    "project_name,project_overrides",
    [
        (
            "mediasync_test_without_references",
            {},
        ),
        (
            "mediasync_test_with_references_none",
            {"references": None},
        ),
        (
            "mediasync_test_with_references_empty_list",
            {"references": []},
        ),
    ],
)
def test_sync_without_references(mc, project_name: str, project_overrides: dict):
    """
    Test media sync running sync without references. It should not fail and just copy files.
    The test just checks that main() runs without errors.
    """
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, full_project_name)
    driver_dir = os.path.join(TMP_DIR, project_name + "_driver")

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    project_config = {
        "project_name": full_project_name,
        "dest": driver_dir,
    }
    project_config.update(project_overrides)

    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png", "jpg"],
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "PROJECT_WORKING_DIR": TMP_DIR,
            "DRIVER": "local",
            "OPERATION_MODE": "copy",
            "PROJECTS": [project_config],
        }
    )

    main()

    gpkg_conn = sqlite3.connect(os.path.join(work_project_dir, "survey.gpkg"))
    gpkg_cur = gpkg_conn.cursor()
    sql = "SELECT count(*) FROM photos WHERE ext_url IS NULL"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 1

    sql = "SELECT count(*) FROM notes WHERE ext_url IS NULL"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 3


def test_google_drive_backend(mc):
    """Test media sync connected to google drive backend"""
    project_name = "mediasync_test_googledrive"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, full_project_name)

    cleanup(mc, full_project_name, [work_project_dir])
    prepare_mergin_project(mc, full_project_name)

    # invalid config – missing service_account_file
    config.update(
        {
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "PROJECT_WORKING_DIR": TMP_DIR,
            "OPERATION_MODE": "copy",
            "DRIVER": "google_drive",
            "GOOGLE_DRIVE__SHARE_WITH": "",
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "folder": GOOGLE_DRIVE_FOLDER,
                    "references": [
                        {
                            "file": None,
                            "table": None,
                            "local_path_column": None,
                            "driver_path_column": None,
                        }
                    ],
                }
            ],
        }
    )

    with pytest.raises(ConfigError):
        validate_config(config)

    # patch config to fit testing purposes
    config.update(
        {
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "PROJECT_WORKING_DIR": TMP_DIR,
            "OPERATION_MODE": "copy",
            "DRIVER": "google_drive",
            "GOOGLE_DRIVE__SERVICE_ACCOUNT_FILE": GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE,
            "GOOGLE_DRIVE__SHARE_WITH": "",
            "PROJECTS": [
                {
                    "project_name": full_project_name,
                    "folder": GOOGLE_DRIVE_FOLDER,
                    "references": [
                        {
                            "file": None,
                            "table": None,
                            "local_path_column": None,
                            "driver_path_column": None,
                        }
                    ],
                }
            ],
        }
    )

    project = config.projects[0]
    google_drive_delete_folder(GoogleDriveDriver(config, project), GOOGLE_DRIVE_FOLDER)

    main()

    google_drive_files = google_drive_list_files_in_folder(
        GoogleDriveDriver(config, project), GOOGLE_DRIVE_FOLDER
    )

    assert "img1.png" in google_drive_files
    assert "images/img2.jpg" in google_drive_files

    # files in mergin project still exist (copy mode)
    assert os.path.exists(os.path.join(work_project_dir, "img1.png"))
    assert os.path.exists(os.path.join(work_project_dir, "images", "img2.jpg"))
