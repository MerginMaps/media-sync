# Mergin Maps Media Sync
Sync media files from Mergin Maps projects to other storage backends. Currently, supported backends are MinIO (S3-like), Dropbox, Google Drive and local drive (mostly used for testing).

Sync works in two modes, in COPY mode, where media files are only copied to external drive and MOVE mode, where files are
subsequently removed from Mergin Maps project (on cloud).

Also user can choose whether references to media files should be updated.

**IMPORTANT**: structure of the config file was changed in the latest version. Therefore old .ini config files should be migrated and enviromnent values should be updated.

### Quick start

Not sure where to start? Check out our [quick start](docs/quick_start.md) guide to set up sync from a new Mergin Maps project to your MinIO/S3 bucket.

<div><img align="left" width="45" height="45" src="https://raw.githubusercontent.com/MerginMaps/docs/main/src/public/slack.svg"><a href="https://merginmaps.com/community/join">Join our community chat</a><br/>and ask questions!</div><br />

Basic principle is that media-sync deamon COPY or MOVE your pictures from Mergin Maps server to other storage backend. Here is an example of MOVE operation:
![Overview](docs/images/overview.png)

#### Running with Docker
To run the container, use a command like the following one:
```shell
  docker run -it \
  -e MERGIN__USERNAME=john \
  -e MERGIN__PASSWORD=myStrongPassword \
  -e MERGIN__PROJECT_NAME=john/my_project \
  lutraconsulting/mergin-media-sync python3 media_sync_daemon.py
```
The sync process will start, regularly checking Mergin Maps service copy/move media files from a Mergin Maps project to an external storage.
Local drive is a default backend, you need to mount volume from host machine for data to persist.

#### Update reference table in geopackage
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
  -e REFERENCES = "[{file='my_survey.gpkg', table='my_table', local_path_column='col_with_path', driver_path_column='col_with_ext_url'}]" \
  lutraconsulting/mergin-media-sync python3 media_sync_daemon.py
```
Make sure you have correct structure of you .gpkg file. Otherwise leave all `REFERENCE__` variables empty.

#### Using MinIO backend
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
  -e MINIO__SECURE=1 \
  -e MINIO__BUCKET_SUBPATH=SubFolder \
  lutraconsulting/mergin-media-sync python3 media_sync_daemon.py
```

**Please note double underscore `__` is used to separate [config](config.yaml.default) group and item.**

The specification of `MINIO__BUCKET_SUBPATH` is optional and can be skipped if the files should be stored directly in `MINIO__BUCKET`.

#### Using Dropbox backend

You will need a Dropbox app with an OAuth2 refresh token. Follow these steps once to generate your credentials:

1. Go to [https://www.dropbox.com/developers/apps](https://www.dropbox.com/developers/apps) and create a new app.
   - Choose **Scoped access** → **Full Dropbox** (or **App folder** if you prefer isolation).
   - Under _Permissions_, enable **`files.content.write`** and **`sharing.write`**, then save.
2. On the app's _Settings_ tab, note your **App key** and **App secret**.
3. Generate a refresh token by running the following and following the prompts:
   ```shell
   pip install dropbox
   python3 - <<'EOF'
   import dropbox
   from dropbox import DropboxOAuth2FlowNoRedirect

   APP_KEY = "<your_app_key>"
   APP_SECRET = "<your_app_secret>"

   auth_flow = DropboxOAuth2FlowNoRedirect(APP_KEY, APP_SECRET, token_access_type="offline")
   print("Authorize this app:", auth_flow.start())
   code = input("Enter auth code: ").strip()
   result = auth_flow.finish(code)
   print("Refresh token:", result.refresh_token)
   EOF
   ```
4. Copy the printed **refresh token** — this is a long-lived credential that media-sync uses to authenticate.

```shell
docker run -it \
  --name mergin-media-sync \
  -e MERGIN__USERNAME=john \
  -e MERGIN__PASSWORD=myStrongPassword \
  -e MERGIN__PROJECT_NAME=john/my_project \
  -e DRIVER=dropbox \
  -e DROPBOX__APP_KEY=your_app_key \
  -e DROPBOX__APP_SECRET=your_app_secret \
  -e DROPBOX__REFRESH_TOKEN=your_refresh_token \
  -e DROPBOX__FOLDER=mediasync \
  lutraconsulting/mergin-media-sync python3 media_sync_daemon.py
```

Uploaded files are exposed as direct-download shared links (`?dl=1`) stored in the GeoPackage reference column. If a shared link already exists for a file it is reused automatically.

`DROPBOX__FOLDER` is optional. When set, all files are placed under that folder in your Dropbox (e.g. `DROPBOX__FOLDER=mediasync` stores files at `/mediasync/img1.png`).

| Environment variable | Required | Description |
|---|---|---|
| `DROPBOX__APP_KEY` | yes | Dropbox app key (from the developer console) |
| `DROPBOX__APP_SECRET` | yes | Dropbox app secret (from the developer console) |
| `DROPBOX__REFRESH_TOKEN` | yes | Long-lived OAuth2 refresh token (generated above) |
| `DROPBOX__FOLDER` | no | Root folder inside Dropbox for all uploaded files |

#### Using Google Drive backend
For setup instructions and more details, please refer to our [Google Drive guide](./docs/google-drive-setup.md).

### Installation

#### Docker
The easiest way to run Media sync is with Docker provided on our [docker hub repo](https://hub.docker.com/repository/docker/lutraconsulting/mergin-media-sync). You can build your own local docker image, by first cloning the repo:

```
git clone git@github.com:lutraconsulting/mergin-media-sync.git
```

And then building the image:

```
docker build -t mergin_media_sync .
```
#### Manual installation

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

1. set up configuration in config.yaml  (see config.yaml.default for a sample)
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
  # Dropbox backend tests (optional)
  export TEST_DROPBOX_APP_KEY=<app_key>
  export TEST_DROPBOX_APP_SECRET=<app_secret>
  export TEST_DROPBOX_REFRESH_TOKEN=<refresh_token>
  export TEST_DROPBOX_FOLDER=mediasync-test
  pipenv run pytest test/
```

### Releasing new version

1. Update `version.py` and `CHANGELOG.md`
2. Tag the new version in git repo
3. Build and upload the new container (both with the new version tag and as the latest tag)
   ```
   docker build --no-cache -t lutraconsulting/mergin-media-sync .
   docker tag lutraconsulting/mergin-media-sync lutraconsulting/mergin-media-sync:0.1.0
   docker push lutraconsulting/mergin-media-sync:0.1.0
   docker push lutraconsulting/mergin-media-sync:latest
