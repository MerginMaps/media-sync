# mergin-media-sync
Sync media files from Mergin projects to other storage backends. Currently, supported backend are local drive (mostly for testing)
and MinIO (S3-like) backend.

Sync works in two modes, in COPY mode, where media files are only copied to external drive and MOVE mode, where files are
subsequently removed from mergin project (on cloud).

Also user can choose whether references to media files should be updated.

### Running with Docker

The easiest way to run Media sync is with Docker.
To build a local docker image:
```
docker build -t mergin_media_sync .
```

To run the container, use a command like the following one: 
```shell
  docker run -it \
  -e MERGIN__USERNAME=john \
  -e MERGIN__PASSWORD=myStrongPassword \
  -e MERGIN__PROJECT_NAME=john/my_project \
  mergin_media_sync python3 media_sync_daemon.py
```
The sync process will start, regularly checking Mergin service copy/move media files from mergin project to external storage.
Local drive is a default backend, you need to mount volume from host machine for data to persist. 

If you'd like to update references to media files (probably useful with MOVE mode), you can run:
```shell
docker run -it \
  -v /tmp/mediasync:/data \
  --name mergin-media-sync \
  -e MERGIN__USERNAME=john \
  -e MERGIN__PASSWORD=myStrongPassword \
  -e MERGIN__PROJECT_NAME=john/my_project \
  -e LOCAL__DEST=/data \
  -e OPERATION_MODE=move \
  -e REFERENCE__FILE=my_survey.gpkg \
  -e REFERENCE__TABLE=my_table \
  -e REFERENCE__LOCAL_PATH_FIELD=col_with_path \
  -e REFERENCE__DRIVER_PATH_FIELD=col_with_ext_url \
  mergin-media-sync python3 media_sync_daemon.py
```
Make sure you have correct structure of you .gpkg file. Otherwise leave all `REFERENCE__` variables empty.


Last, in case you want to switch to different driver, you can run:
```shell
docker run -it \
  --name mergin-media-sync \
  -e MERGIN__USERNAME=john \
  -e MERGIN__PASSWORD=myStrongPassword \
  -e MERGIN__PROJECT_NAME=john/my_project \
  -e MERGIN__PROJECT_NAME=ttest/mediasync_test \
  -e DRIVER=minio \
  -e MINIO__ENDPOINT="minio-server-url" \
  -e MINIO__ACCESS_KEY=access-key \
  -e MINIO__SECRET_KEY=secret-key \
  -e MINIO__BUCKET=destination-bucket \
  -e MINIO__SECRET=1 \
  mergin-media-sync python3 media_sync_daemon.py
```

**Please note double underscore `__` is used to separate config group and item.**

### Installation

If you would like to avoid the manual installation steps, please follow the guide on using sync with Docker above. We use pipenv for managing python virtual environment.

```shell
  pipenv install --three
```

If you get `ModuleNotFoundError: No module named 'skbuild'` error, try to update pip with command
`python -m pip install --upgrade pip`


### How to use

If you want to modify references to media files in some geopackage in your project, please make sure you have two columns there,
one with reference to local file and another for external URL where file can be downloaded from.

Initialization:

1. set up configuration in config.ini  (see config.ini.default for a sample)
2. all settings can be overridden with env variables (see docker example above)
3. run media-sync
```shell
  pipenv run python3 media_sync.py
```

### Running Tests
You need to install also dev packages:
```shell
  pipenv install --three --dev
```

and run local minio server:
```shell
docker run \
  -p 9000:9000 \
  -p 9001:9001 \
  --name minio\
  -e "MINIO_ROOT_USER=EXAMPLE" \
  -e "MINIO_ROOT_PASSWORD=EXAMPLEKEY" \
  quay.io/minio/minio server /data --console-address ":9001"
```

To run automatic tests:
```shell
  export TEST_MERGIN_URL=<url>                # testing server
  export TEST_API_USERNAME=<username>
  export TEST_API_PASSWORD=<pwd>
  export TEST_MINIO_URL="localhost:9000"
  export TEST_MINIO_ACCESS_KEY=EXAMPLE
  export TEST_MINIO_SECRET_KEY=EXAMPLEKEY
  pipenv run pytest test/
```
