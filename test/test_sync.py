"""
Mergin Media Sync - a tool to sync media files from Mergin projects to other storage backends

Copyright (C) 2021 Lutra Consulting

License: MIT
"""

import pytest
import os
import shutil
import sqlite3

from mergin import MerginClient, MerginProject, ClientError

from drivers import MinioDriver, LocalDriver, GoogleDriveDriver
from media_sync import (
    main,
    config,
    mc_pull,
    media_sync_push,
    mc_download,
    MediaSyncError,
)
from config import validate_config, ConfigError

from .conftest import (
    API_USER,
    WORKSPACE,
    TMP_DIR,
    TEST_DATA_DIR,
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
    work_project_dir = os.path.join(
        TMP_DIR, project_name + "_work"
    )  # working dir for mediasync
    driver_dir = os.path.join(
        TMP_DIR, project_name + "_driver"
    )  # destination dir for 'local' driver

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    # patch config to fit testing purposes
    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png", "jpg"],
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "DRIVER": "local",
            "LOCAL__DEST": driver_dir,
            "OPERATION_MODE": "copy",
            "REFERENCES": [
                {
                    "file": None,
                    "table": None,
                    "local_path_column": None,
                    "driver_path_column": None,
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
            "REFERENCES": [
                {
                    "file": "survey.gpkg",
                    "table": "notes",
                    "local_path_column": "photo",
                    "driver_path_column": "ext_url",
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
    gpkg_conn = sqlite3.connect(
        os.path.join(work_project_dir, config.references[0].file)
    )
    gpkg_cur = gpkg_conn.cursor()
    sql = f"SELECT count(*) FROM {config.references[0].table} WHERE {config.references[0].driver_path_column}='{copied_file}'"
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
    gpkg_conn = sqlite3.connect(
        os.path.join(work_project_dir, config.references[0].file)
    )
    gpkg_cur = gpkg_conn.cursor()
    sql = f"SELECT count(*) FROM {config.references[0].table} WHERE {config.references[0].local_path_column} is Null AND {config.references[0].driver_path_column}='{copied_file}'"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 1
    shutil.rmtree(work_project_dir)
    shutil.rmtree(driver_dir)

    # change mode to .png and also base project path to 'images' - nothing should be done
    config.update({"allowed_extensions": ["png"], "base_path": "images"})
    main()
    # check synced images
    copied_file = os.path.join(driver_dir, "images", "img1.png")
    assert not os.path.exists(copied_file)
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v3"


def test_pull_and_sync(mc):
    """Test media sync if mergin project is ahead of locally downloaded one"""
    project_name = "mediasync_test_pull"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, project_name + "_work")
    driver_dir = os.path.join(TMP_DIR, project_name + "_driver")

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    config.update(
        {
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "DRIVER": "local",
            "LOCAL__DEST": driver_dir,
            "OPERATION_MODE": "copy",
            "ALLOWED_EXTENSIONS": ["png", "jpg"],
            "BASE_PATH": None,
            "REFERENCES": [
                {
                    "file": None,
                    "table": None,
                    "local_path_column": None,
                    "driver_path_column": None,
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
    # media sync working dir contains only media files (sparse checkout), use full project copy instead
    mc.download_project(full_project_name, project_dir)
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

    files_to_sync = mc_pull(mc)
    driver = LocalDriver(config)
    media_sync_push(mc, driver, files_to_sync)
    # check synced image
    assert os.path.exists(os.path.join(driver_dir, "img_new.png"))


def test_sparse_checkout(mc):
    """Test only media files and referenced geopackages are downloaded from mergin project
    and project is downloaded again when the configuration of synced files changes"""
    project_name = "mediasync_test_sparse"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, project_name + "_work")
    driver_dir = os.path.join(TMP_DIR, project_name + "_driver")

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    config.update(
        {
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "DRIVER": "local",
            "LOCAL__DEST": driver_dir,
            "OPERATION_MODE": "copy",
            "ALLOWED_EXTENSIONS": ["jpg"],
            "BASE_PATH": None,
            "REFERENCES": [],
        }
    )
    main()
    assert os.path.exists(os.path.join(driver_dir, "images", "img2.jpg"))
    # only .jpg files are downloaded, other project files are not
    assert os.path.exists(os.path.join(work_project_dir, "images", "img2.jpg"))
    assert not os.path.exists(os.path.join(work_project_dir, "img1.png"))
    assert not os.path.exists(os.path.join(work_project_dir, "survey.gpkg"))
    assert not os.path.exists(os.path.join(work_project_dir, "project.qgz"))

    # nothing changed on server
    assert mc_pull(mc) == []

    # project is updated on server - out of sync scope file only, local version is updated anyway
    project_dir = os.path.join(TMP_DIR, project_name + "_create")
    if os.path.exists(project_dir):
        shutil.rmtree(project_dir)
    mc.download_project(full_project_name, project_dir)
    shutil.copyfile(
        os.path.join(project_dir, "img1.png"), os.path.join(project_dir, "img_new.png")
    )
    mc.push_project(project_dir)
    assert not mc_pull(mc)
    assert MerginProject(work_project_dir).version() == "v2"
    assert not os.path.exists(os.path.join(work_project_dir, "img_new.png"))

    # change of synced files leads to new download of the project with new filter
    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png"],
            "REFERENCES": [
                {
                    "file": "survey.gpkg",
                    "table": "notes",
                    "local_path_column": "photo",
                    "driver_path_column": "ext_url",
                }
            ],
        }
    )
    main()
    assert os.path.exists(os.path.join(driver_dir, "img1.png"))
    assert os.path.exists(os.path.join(driver_dir, "img_new.png"))
    assert os.path.exists(os.path.join(work_project_dir, "img1.png"))
    assert os.path.exists(os.path.join(work_project_dir, "survey.gpkg"))
    assert not os.path.exists(os.path.join(work_project_dir, "images", "img2.jpg"))
    assert not os.path.exists(os.path.join(work_project_dir, "project.qgz"))
    # references were updated and pushed
    assert mc.project_info(full_project_name)["version"] == "v3"


def test_legacy_full_checkout(mc):
    """Test project downloaded by older version of media sync (full checkout without filter)
    is downloaded again with sparse checkout filter only once"""
    project_name = "mediasync_test_legacy"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, project_name + "_work")
    driver_dir = os.path.join(TMP_DIR, project_name + "_driver")

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    config.update(
        {
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "DRIVER": "local",
            "LOCAL__DEST": driver_dir,
            "OPERATION_MODE": "copy",
            "ALLOWED_EXTENSIONS": ["jpg"],
            "BASE_PATH": None,
            "REFERENCES": [
                {
                    "file": "survey.gpkg",
                    "table": "notes",
                    "local_path_column": "photo",
                    "driver_path_column": "ext_url",
                }
            ],
        }
    )
    # full checkout as done by older versions of media sync
    mc.download_project(full_project_name, work_project_dir)
    assert os.path.exists(os.path.join(work_project_dir, "project.qgz"))
    assert MerginProject(work_project_dir).file_filter()["include"] is None

    # first pull downloads project again with filter
    files_to_sync = mc_pull(mc)
    assert [f["path"] for f in files_to_sync] == ["images/img2.jpg"]
    assert MerginProject(work_project_dir).file_filter()["include"] == [
        "*.jpg",
        "survey.gpkg",
    ]
    assert not os.path.exists(os.path.join(work_project_dir, "project.qgz"))
    assert not os.path.exists(os.path.join(work_project_dir, "img1.png"))
    media_sync_push(mc, LocalDriver(config), files_to_sync)
    assert os.path.exists(os.path.join(driver_dir, "images", "img2.jpg"))
    assert mc.project_info(full_project_name)["version"] == "v2"

    # following pull does not download project again (marker file would be removed)
    marker_file = os.path.join(work_project_dir, "marker.txt")
    with open(marker_file, "w") as f:
        f.write("not part of sparse checkout")
    assert mc_pull(mc) == []
    assert os.path.exists(marker_file)


def test_minio_backend(mc):
    """Test media sync connected to minio backend (needs local service running)"""
    project_name = "mediasync_test_minio"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, project_name + "_work")

    cleanup(mc, full_project_name, [work_project_dir])
    prepare_mergin_project(mc, full_project_name)

    # patch config to fit testing purposes
    config.update(
        {
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "OPERATION_MODE": "copy",
            "REFERENCES": [
                {
                    "file": None,
                    "table": None,
                    "local_path_column": None,
                    "driver_path_column": None,
                }
            ],
            "DRIVER": "minio",
            "MINIO__ENDPOINT": MINIO_URL,
            "MINIO__ACCESS_KEY": MINIO_ACCESS_KEY,
            "MINIO__SECRET_KEY": MINIO_SECRET_KEY,
            "MINIO__BUCKET": "test",
        }
    )

    main()
    # check synced images
    driver = MinioDriver(config)
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
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "OPERATION_MODE": "copy",
            "REFERENCES": [
                {
                    "file": None,
                    "table": None,
                    "local_path_column": None,
                    "driver_path_column": None,
                }
            ],
            "DRIVER": "minio",
            "MINIO__ENDPOINT": MINIO_URL,
            "MINIO__ACCESS_KEY": MINIO_ACCESS_KEY,
            "MINIO__SECRET_KEY": MINIO_SECRET_KEY,
            "MINIO__BUCKET": "test1",
            "MINIO__BUCKET_SUBPATH": "subPath",
        }
    )

    main()
    # check synced images
    driver = MinioDriver(config)
    minio_objects = [
        o.object_name for o in driver.client.list_objects("test1", recursive=True)
    ]
    assert "subPath/img1.png" in minio_objects
    assert "subPath/images/img2.jpg" in minio_objects


def test_sync_failures(mc):
    """Test common sync failures"""
    project_name = "mediasync_fail"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(
        TMP_DIR, project_name + "_work"
    )  # working dir for mediasync
    driver_dir = os.path.join(
        TMP_DIR, project_name + "_driver"
    )  # destination dir for 'local' driver

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png", "jpg"],
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "DRIVER": "local",
            "LOCAL__DEST": driver_dir,
            "OPERATION_MODE": "copy",
            # sparse checkout filter is fixed at download time, so survey.gpkg must be referenced
            # before mc_download() below, otherwise it is not downloaded for the reference tests
            "REFERENCES": [
                {
                    "file": "survey.gpkg",
                    "table": "notes",
                    "local_path_column": "photo",
                    "driver_path_column": "ext_url",
                }
            ],
        }
    )
    driver = LocalDriver(config)
    files_to_sync = mc_download(mc)
    # "remove" .mergin hidden dir to mimic broken working directory
    os.rename(
        os.path.join(work_project_dir, ".mergin"),
        os.path.join(work_project_dir, ".hidden"),
    )

    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, files_to_sync)
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
        media_sync_push(mc, driver, files_to_sync)
    assert "The project working directory does not exist" in exc.value.args[0]
    os.rename(work_project_dir + "_renamed", work_project_dir)

    # incorrect gpkg details for reference table
    config.update(
        {
            "REFERENCES": [
                {
                    "file": "survey.gpkg",
                    "table": "notes_error",
                    "local_path_column": "photo",
                    "driver_path_column": "ext_url",
                }
            ],
        }
    )
    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, files_to_sync)
    assert "SQLITE error" in exc.value.args[0]

    # reference file not in project -> fail before media files are removed in move mode
    config.update(
        {
            "OPERATION_MODE": "move",
            "REFERENCES": [
                {
                    "file": "missing.gpkg",
                    "table": "notes",
                    "local_path_column": "photo",
                    "driver_path_column": "ext_url",
                }
            ],
        }
    )
    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, files_to_sync)
    assert "Reference file not found in project" in exc.value.args[0]
    assert not os.path.exists(os.path.join(work_project_dir, "missing.gpkg"))
    assert os.path.exists(os.path.join(work_project_dir, "img1.png"))

    config.update(
        {
            "OPERATION_MODE": "copy",
            "REFERENCES": [
                {
                    "file": "survey.gpkg",
                    "table": "notes",
                    "local_path_column": "photo",
                    "driver_path_column": "ext_url",
                }
            ],
        }
    )

    # introduce some unknown local file (files outside of sparse checkout filter are ignored)
    shutil.copyfile(
        os.path.join(work_project_dir, "img1.png"),
        os.path.join(work_project_dir, "new.png"),
    )
    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, files_to_sync)
    assert "There are changes to be added - it should never happen" in exc.value.args[0]
    os.remove(os.path.join(work_project_dir, "new.png"))

    # remove local file which should have been synced -> just skipped
    os.remove(os.path.join(work_project_dir, "img1.png"))
    shutil.rmtree(driver_dir)
    media_sync_push(mc, driver, files_to_sync)
    # check synced images
    assert not os.path.exists(os.path.join(driver_dir, "img1.png"))
    assert os.path.exists(os.path.join(driver_dir, "images", "img2.jpg"))


def test_multiple_tables(mc):
    project_name = "mediasync_test_multiple"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(
        TMP_DIR, project_name + "_work"
    )  # working dir for mediasync
    driver_dir = os.path.join(
        TMP_DIR, project_name + "_driver"
    )  # destination dir for 'local' driver

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    # patch config to fit testing purposes
    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png"],
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "DRIVER": "local",
            "LOCAL__DEST": driver_dir,
            "OPERATION_MODE": "copy",
            "REFERENCES": [
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
    )

    main()
    # check synced images
    assert os.path.exists(os.path.join(driver_dir, "img1.png"))

    # check references have been changed and pushed
    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v2"

    copied_file = os.path.join(driver_dir, "img1.png")
    # check that both tables were updated
    gpkg_conn = sqlite3.connect(
        os.path.join(work_project_dir, config.references[0].file)
    )
    gpkg_cur = gpkg_conn.cursor()
    sql = f"SELECT count(*) FROM {config.references[0].table} WHERE {config.references[0].driver_path_column}='{copied_file}'"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 1
    # check that both tables were updated
    gpkg_conn = sqlite3.connect(
        os.path.join(work_project_dir, config.references[1].file)
    )
    gpkg_cur = gpkg_conn.cursor()
    sql = f"SELECT count(*) FROM {config.references[1].table} WHERE {config.references[1].driver_path_column}='{copied_file}'"
    gpkg_cur.execute(sql)
    assert gpkg_cur.fetchone()[0] == 1


@pytest.mark.parametrize(
    "project_name,config_update",
    [
        (
            "mediasync_test_without_references",
            {},
        ),
        (
            "mediasync_test_with_references_none",
            {"REFERENCES": None},
        ),
        (
            "mediasync_test_with_references_empty_list",
            {"REFERENCES": []},
        ),
    ],
)
def test_sync_without_references(mc, project_name: str, config_update: dict):
    """
    Test media sync running sync without references. It should not fail and just copy files.
    The test just checks that main() runs without errors.
    """
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(
        TMP_DIR, project_name + "_work"
    )  # working dir for mediasync
    driver_dir = os.path.join(
        TMP_DIR, project_name + "_driver"
    )  # destination dir for 'local' driver

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    config.update(
        {
            "ALLOWED_EXTENSIONS": ["png", "jpg"],
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "DRIVER": "local",
            "LOCAL__DEST": driver_dir,
            "OPERATION_MODE": "copy",
            "BASE_PATH": None,
        }
    )
    config.update(config_update)

    main()

    assert os.path.exists(os.path.join(driver_dir, "img1.png"))
    assert os.path.exists(os.path.join(driver_dir, "images", "img2.jpg"))
    # geopackage is not needed without references so it is not downloaded nor changed
    assert not os.path.exists(os.path.join(work_project_dir, "survey.gpkg"))
    assert mc.project_info(full_project_name)["version"] == "v1"


def test_google_drive_backend(mc):
    """Test media sync connected to google drive backend"""
    project_name = "mediasync_test_googledrive"
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, project_name + "_work")

    cleanup(mc, full_project_name, [work_project_dir])
    prepare_mergin_project(mc, full_project_name)

    # invalid config
    config.update(
        {
            "MERGIN__USERNAME": API_USER,
            "MERGIN__PASSWORD": USER_PWD,
            "MERGIN__URL": SERVER_URL,
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "OPERATION_MODE": "copy",
            "REFERENCES": [
                {
                    "file": None,
                    "table": None,
                    "local_path_column": None,
                    "driver_path_column": None,
                }
            ],
            "DRIVER": "google_drive",
            "GOOGLE_DRIVE__FOLDER": GOOGLE_DRIVE_FOLDER,
            "GOOGLE_DRIVE__SHARE_WITH": "",
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
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "OPERATION_MODE": "copy",
            "REFERENCES": [
                {
                    "file": None,
                    "table": None,
                    "local_path_column": None,
                    "driver_path_column": None,
                }
            ],
            "DRIVER": "google_drive",
            "GOOGLE_DRIVE__SERVICE_ACCOUNT_FILE": GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE,
            "GOOGLE_DRIVE__FOLDER": GOOGLE_DRIVE_FOLDER,
            "GOOGLE_DRIVE__SHARE_WITH": "",
        }
    )

    google_drive_delete_folder(GoogleDriveDriver(config), GOOGLE_DRIVE_FOLDER)

    main()

    google_drive_files = google_drive_list_files_in_folder(
        GoogleDriveDriver(config), GOOGLE_DRIVE_FOLDER
    )

    assert "img1.png" in google_drive_files
    assert "images/img2.jpg" in google_drive_files

    # files in mergin project still exist (copy mode)
    assert os.path.exists(os.path.join(work_project_dir, "img1.png"))
    assert os.path.exists(os.path.join(work_project_dir, "images", "img2.jpg"))


def _setup_recovery_test(mc, project_name, operation_mode):
    """Prepare project on server and config with references for tests of sync recovery"""
    full_project_name = WORKSPACE + "/" + project_name
    work_project_dir = os.path.join(TMP_DIR, project_name + "_work")
    driver_dir = os.path.join(TMP_DIR, project_name + "_driver")

    cleanup(mc, full_project_name, [work_project_dir, driver_dir])
    prepare_mergin_project(mc, full_project_name)

    config.update(
        {
            "MERGIN__PROJECT_NAME": full_project_name,
            "PROJECT_WORKING_DIR": work_project_dir,
            "DRIVER": "local",
            "LOCAL__DEST": driver_dir,
            "OPERATION_MODE": operation_mode,
            "REFERENCES": [
                {
                    "file": "survey.gpkg",
                    "table": "notes",
                    "local_path_column": "photo",
                    "driver_path_column": "ext_url",
                }
            ],
        }
    )
    return full_project_name, work_project_dir, driver_dir


# conflicted copy of media file created by other client
SERVER_CONFLICTED_COPY = "img1 (conflicted copy, other v1).png"


def _update_survey(project_dir, sql):
    gpkg_conn = sqlite3.connect(os.path.join(project_dir, "survey.gpkg"))
    gpkg_conn.enable_load_extension(True)
    gpkg_cur = gpkg_conn.cursor()
    gpkg_cur.execute('SELECT load_extension("mod_spatialite")')
    gpkg_cur.execute(sql)
    gpkg_conn.commit()
    gpkg_conn.close()


def _add_new_photo(project_dir):
    """Add new .png file and reference it in .gpkg file"""
    shutil.copyfile(
        os.path.join(TEST_DATA_DIR, "img1.png"),
        os.path.join(project_dir, "img_new.png"),
    )
    _update_survey(
        project_dir, "update notes set photo = 'img_new.png' where name = 'test'"
    )


def _edit_synced_reference(project_dir):
    """Edit the same column of the same feature as media sync does (edit conflict)"""
    _update_survey(
        project_dir, "update notes set ext_url = 'manual_url' where name = 'img1'"
    )


def _change_schema(project_dir):
    """Change schema of .gpkg file, so that local changes can not be rebased"""
    _update_survey(project_dir, "alter table notes add column extra text")


def _add_conflicted_copy(project_dir):
    """Add conflicted copy of media file"""
    shutil.copyfile(
        os.path.join(TEST_DATA_DIR, "img1.png"),
        os.path.join(project_dir, SERVER_CONFLICTED_COPY),
    )


def _push_server_change(full_project_name, change):
    """Update project on server by other client
    :param change: function modifying files in the project directory
    """
    mc = MerginClient(SERVER_URL, login=API_USER, password=USER_PWD)
    project_dir = os.path.join(TMP_DIR, full_project_name.split("/")[-1] + "_other")
    if os.path.exists(project_dir):
        shutil.rmtree(project_dir)
    mc.download_project(full_project_name, project_dir)
    change(project_dir)
    mc.push_project(project_dir)


def _server_synced_references(mc, full_project_name, project_dir):
    """Returns number of features with references to synced files in .gpkg file on server"""
    if os.path.exists(project_dir):
        shutil.rmtree(project_dir)
    mc.download_project(full_project_name, project_dir)
    gpkg_conn = sqlite3.connect(os.path.join(project_dir, "survey.gpkg"))
    gpkg_cur = gpkg_conn.cursor()
    gpkg_cur.execute("SELECT count(*) FROM notes WHERE ext_url IS NOT NULL")
    return gpkg_cur.fetchone()[0]


@pytest.mark.parametrize("operation_mode", ["copy", "move"])
def test_recover_after_failed_push(mc, mocker, operation_mode):
    """Test that changes left by failed push are rebased on server changes and pushed in the next sync"""
    project_name = f"mediasync_recover_{operation_mode}"
    full_project_name, work_project_dir, driver_dir = _setup_recovery_test(
        mc, project_name, operation_mode
    )
    driver = LocalDriver(config)

    files_to_sync = mc_download(mc)
    # push of updated references fails (e.g. network error)
    mocker.patch.object(mc, "push_project", side_effect=ClientError("Network error"))
    with pytest.raises(MediaSyncError) as exc:
        media_sync_push(mc, driver, files_to_sync)
    mocker.stopall()
    assert "Mergin client error on push" in exc.value.args[0]
    assert os.path.exists(os.path.join(driver_dir, "img1.png"))
    assert os.path.exists(os.path.join(driver_dir, "images", "img2.jpg"))
    assert mc.project_info(full_project_name)["version"] == "v1"

    # meanwhile project is updated on server by someone else
    _push_server_change(full_project_name, _add_new_photo)
    assert mc.project_info(full_project_name)["version"] == "v2"

    # next sync pushes pending changes rebased on top of server changes and syncs new files
    files_to_sync = mc_pull(mc)
    assert mc.project_info(full_project_name)["version"] == "v3"
    assert [f["path"] for f in files_to_sync] == ["img_new.png"]
    media_sync_push(mc, driver, files_to_sync)
    assert os.path.exists(os.path.join(driver_dir, "img_new.png"))

    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v4"
    server_files = [f["path"] for f in project_info["files"]]
    if operation_mode == "move":
        assert "img1.png" not in server_files
        assert "images/img2.jpg" not in server_files
        assert "img_new.png" not in server_files
    else:
        assert "img_new.png" in server_files
    assert (
        _server_synced_references(
            mc, full_project_name, os.path.join(TMP_DIR, project_name + "_check")
        )
        == 3
    )

    # sync is not blocked afterwards
    assert mc_pull(mc) == []
    assert mc.project_info(full_project_name)["version"] == "v4"


@pytest.mark.parametrize("operation_mode", ["copy", "move"])
def test_recover_after_edit_conflict(mc, mocker, capsys, operation_mode):
    """Test that sync continues after rebase of pending changes with edit conflict (same feature edited on server)"""
    project_name = f"mediasync_recover_edit_conflict_{operation_mode}"
    full_project_name, work_project_dir, driver_dir = _setup_recovery_test(
        mc, project_name, operation_mode
    )
    driver = LocalDriver(config)

    files_to_sync = mc_download(mc)
    mocker.patch.object(mc, "push_project", side_effect=ClientError("Network error"))
    with pytest.raises(MediaSyncError):
        media_sync_push(mc, driver, files_to_sync)
    mocker.stopall()

    # meanwhile the same column of the same feature is updated on server by someone else
    _push_server_change(full_project_name, _edit_synced_reference)

    # pending changes are rebased with edit conflict and pushed
    capsys.readouterr()
    assert mc_pull(mc) == []
    assert mc.project_info(full_project_name)["version"] == "v3"
    edit_conflicts = [
        f for f in os.listdir(work_project_dir) if "(edit conflict, " in f
    ]
    assert len(edit_conflicts) == 1
    # user is warned only in the move mode, where reference to a new file could be lost
    warned = (
        "please check references to media files manually" in capsys.readouterr().out
    )
    assert warned == (operation_mode == "move")

    # edit conflict file does not block next sync and the warning is not repeated
    assert mc_pull(mc) == []
    assert mc.project_info(full_project_name)["version"] == "v3"
    assert "please check references" not in capsys.readouterr().out


@pytest.mark.parametrize("operation_mode", ["copy", "move"])
def test_recover_after_conflict(mc, mocker, operation_mode):
    """Test that project is downloaded and synced again when pending changes can not be rebased on server changes"""
    project_name = f"mediasync_recover_conflict_{operation_mode}"
    full_project_name, work_project_dir, driver_dir = _setup_recovery_test(
        mc, project_name, operation_mode
    )
    driver = LocalDriver(config)

    files_to_sync = mc_download(mc)
    mocker.patch.object(mc, "push_project", side_effect=ClientError("Network error"))
    with pytest.raises(MediaSyncError):
        media_sync_push(mc, driver, files_to_sync)
    mocker.stopall()

    # meanwhile schema of .gpkg file is changed on server, so local changes can not be rebased
    _push_server_change(full_project_name, _change_schema)

    # project is downloaded again and all media files are synced
    files_to_sync = mc_pull(mc)
    assert sorted(f["path"] for f in files_to_sync) == ["images/img2.jpg", "img1.png"]
    assert not [f for f in os.listdir(work_project_dir) if "conflict" in f]
    assert mc.project_info(full_project_name)["version"] == "v2"
    media_sync_push(mc, driver, files_to_sync)

    project_info = mc.project_info(full_project_name)
    assert project_info["version"] == "v3"
    server_files = [f["path"] for f in project_info["files"]]
    assert ("img1.png" in server_files) == (operation_mode == "copy")
    assert (
        _server_synced_references(
            mc, full_project_name, os.path.join(TMP_DIR, project_name + "_check")
        )
        == 2
    )
    assert mc_pull(mc) == []


def test_conflict_on_push_retry(mc, mocker):
    """Test that project is downloaded and synced again when push is retried and local changes can not be rebased"""
    project_name = "mediasync_conflict_push_retry"
    full_project_name, work_project_dir, driver_dir = _setup_recovery_test(
        mc, project_name, "copy"
    )
    driver = LocalDriver(config)
    mocker.patch("media_sync.SYNC_ATTEMPT_WAIT", 0)

    files_to_sync = mc_download(mc)
    # schema of .gpkg file is changed on server between pull and push of media sync
    push_project = mc.push_project

    def push_after_server_change(directory):
        if mc.push_project.call_count == 1:
            _push_server_change(full_project_name, _change_schema)
        return push_project(directory)

    mocker.patch.object(mc, "push_project", side_effect=push_after_server_change)
    media_sync_push(mc, driver, files_to_sync)

    assert not [f for f in os.listdir(work_project_dir) if "conflict" in f]
    assert mc.project_info(full_project_name)["version"] == "v3"
    assert (
        _server_synced_references(
            mc, full_project_name, os.path.join(TMP_DIR, project_name + "_check")
        )
        == 2
    )


def test_recover_after_unfinished_pull(mc):
    """Test that project is downloaded and synced again after unfinished pull, but only without manual changes"""
    project_name = "mediasync_unfinished_pull"
    full_project_name, work_project_dir, driver_dir = _setup_recovery_test(
        mc, project_name, "copy"
    )
    mc_download(mc)
    # pull failed in the final stage, server version of locked file is kept in metadata dir
    unfinished_pull_dir = os.path.join(work_project_dir, ".mergin", "unfinished_pull")
    os.makedirs(unfinished_pull_dir)
    shutil.copyfile(
        os.path.join(work_project_dir, "survey.gpkg"),
        os.path.join(unfinished_pull_dir, "survey.gpkg"),
    )
    assert mc.has_unfinished_pull(work_project_dir)

    # local changes not made by media sync are not lost
    new_file = os.path.join(work_project_dir, "new.png")
    shutil.copyfile(os.path.join(work_project_dir, "img1.png"), new_file)
    with pytest.raises(MediaSyncError) as exc:
        mc_pull(mc)
    assert "There are pending changes in the local directory" in exc.value.args[0]
    assert os.path.exists(new_file)
    os.remove(new_file)

    # project is downloaded again and all media files are synced
    files_to_sync = mc_pull(mc)
    assert sorted(f["path"] for f in files_to_sync) == ["images/img2.jpg", "img1.png"]
    assert not mc.has_unfinished_pull(work_project_dir)
    assert mc_pull(mc) == []


def test_sync_conflicted_copy_from_server(mc):
    """Test that conflicted copy of media file pushed to server by other client is synced as any other file"""
    project_name = "mediasync_server_conflicted_copy"
    full_project_name, work_project_dir, driver_dir = _setup_recovery_test(
        mc, project_name, "copy"
    )
    driver = LocalDriver(config)

    media_sync_push(mc, driver, mc_download(mc))
    assert mc.project_info(full_project_name)["version"] == "v2"

    _push_server_change(full_project_name, _add_conflicted_copy)
    files_to_sync = mc_pull(mc)
    assert [f["path"] for f in files_to_sync] == [SERVER_CONFLICTED_COPY]
    media_sync_push(mc, driver, files_to_sync)
    assert os.path.exists(os.path.join(driver_dir, SERVER_CONFLICTED_COPY))

    # tracked conflicted copy does not block next sync
    assert mc_pull(mc) == []
    assert mc.project_info(full_project_name)["version"] == "v3"


def test_push_retried_after_server_change(mc, mocker):
    """Test that push rejected because of newer version on server is retried after pull of server changes"""
    project_name = "mediasync_push_retry"
    full_project_name, work_project_dir, driver_dir = _setup_recovery_test(
        mc, project_name, "copy"
    )
    driver = LocalDriver(config)
    mocker.patch("media_sync.SYNC_ATTEMPT_WAIT", 0)

    files_to_sync = mc_download(mc)
    # project is updated on server by someone else between pull and push of media sync
    push_project = mc.push_project

    def push_after_server_change(directory):
        if mc.push_project.call_count == 1:
            _push_server_change(full_project_name, _add_new_photo)
        return push_project(directory)

    mocker.patch.object(mc, "push_project", side_effect=push_after_server_change)
    media_sync_push(mc, driver, files_to_sync)

    # first push is rejected, after pull the new file is synced too
    assert os.path.exists(os.path.join(driver_dir, "img_new.png"))
    assert mc.project_info(full_project_name)["version"] == "v4"
    assert (
        _server_synced_references(
            mc, full_project_name, os.path.join(TMP_DIR, project_name + "_check")
        )
        == 3
    )


def test_sync_not_recovered(mc):
    """Test that sync is not resumed when there are local changes not made by media sync"""
    project_name = "mediasync_not_recovered"
    full_project_name, work_project_dir, driver_dir = _setup_recovery_test(
        mc, project_name, "copy"
    )
    mc_download(mc)

    # unknown local file
    new_file = os.path.join(work_project_dir, "new.png")
    shutil.copyfile(os.path.join(work_project_dir, "img1.png"), new_file)
    with pytest.raises(MediaSyncError) as exc:
        mc_pull(mc)
    assert "There are pending changes in the local directory" in exc.value.args[0]
    os.remove(new_file)

    # media files are not removed in the copy mode
    os.rename(
        os.path.join(work_project_dir, "img1.png"),
        os.path.join(TMP_DIR, project_name + "_img1.png"),
    )
    with pytest.raises(MediaSyncError) as exc:
        mc_pull(mc)
    assert "There are pending changes in the local directory" in exc.value.args[0]
    os.rename(
        os.path.join(TMP_DIR, project_name + "_img1.png"),
        os.path.join(work_project_dir, "img1.png"),
    )

    assert mc_pull(mc) == []
    assert mc.project_info(full_project_name)["version"] == "v1"
