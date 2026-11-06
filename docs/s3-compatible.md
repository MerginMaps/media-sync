# S3-compatible storage

Instead of AWS S3 you can use any S3-compatible storage with `s3` driver. These open source servers are tested
in our CI (see [test/s3_servers/start.sh](../test/s3_servers/start.sh)):

| Server | Tested version | Notes |
|---|---|---|
| [RustFS](https://rustfs.com) | 1.0.3 | |
| [Garage](https://garagehq.deuxfleurs.fr) | v2.5.0 | needs `S3__REGION`, bucket is created with `garage` CLI, files are readable via website endpoint |
| [SeaweedFS](https://github.com/seaweedfs/seaweedfs) | 4.51 | credentials are defined in `s3.json` file |
| [Silo](https://hub.docker.com/r/pgsty/silo) (community maintained MinIO fork) | RELEASE.2026-10-18T00-00-00Z | uses MinIO names of its settings, e.g. `MINIO_ROOT_USER` |

The examples below run the server in docker on `localhost` with data stored in a local directory. Replace `my-access-key`
and `my-secret-key` with your own credentials, and `localhost` with the address of your server - the URLs written
to your project must be reachable by its users.

Media-sync is then configured with the endpoint of the server, e.g.:

```shell
  -e DRIVER=s3 \
  -e S3__ENDPOINT=http://localhost:9000 \
  -e S3__ACCESS_KEY=my-access-key \
  -e S3__SECRET_KEY=my-secret-key \
  -e S3__BUCKET=media \
```

See [README](../README.md#using-s3-backend) for all S3 settings.

## Make files readable

Media-sync writes URLs of uploaded files to your project, e.g. `http://localhost:9000/media/images/photo.jpg`.
For QGIS or a web browser to display them, files in the bucket need to be readable without credentials (read only,
uploads still need the access key). Each server below shows how to do it with its own tools. The bucket is created
by media-sync on its first start (except for Garage), so start media-sync first or create the bucket yourself.

Alternatively you can use any S3 client which supports bucket policies (e.g. `s3cmd` or `mc`) to set a policy
allowing `s3:GetObject` on `arn:aws:s3:::media/*` to everyone (`"Principal": "*"`).

## RustFS

```shell
docker run -d --name rustfs \
  -p 9000:9000 -p 9001:9001 \
  -v $(pwd)/rustfs_data:/data \
  -e RUSTFS_ACCESS_KEY=my-access-key \
  -e RUSTFS_SECRET_KEY=my-secret-key \
  rustfs/rustfs:1.0.3
```

Media-sync settings:

```shell
  -e DRIVER=s3 \
  -e S3__ENDPOINT=http://localhost:9000 \
  -e S3__ACCESS_KEY=my-access-key \
  -e S3__SECRET_KEY=my-secret-key \
  -e S3__BUCKET=media \
```

To make files readable, log in to the web console at `http://localhost:9001` with the access and secret key,
open **Settings** of the `media` bucket and set **Access Policy** to **Public** (anyone can download files,
listing and uploads still need the access key).

## SeaweedFS

Credentials are defined in `s3.json` file. The `anonymous` identity makes files in the `media` bucket readable
without credentials (other buckets stay private):

```json
{
  "identities": [
    {
      "name": "media-sync",
      "credentials": [{"accessKey": "my-access-key", "secretKey": "my-secret-key"}],
      "actions": ["Admin", "Read", "List", "Tagging", "Write"]
    },
    {
      "name": "anonymous",
      "actions": ["Read:media"]
    }
  ]
}
```

Run all-in-one SeaweedFS server, S3 API listens on port 8333:

```shell
docker run -d --name seaweedfs \
  -p 8333:8333 \
  -v $(pwd)/seaweedfs_data:/data \
  -v $(pwd)/s3.json:/etc/seaweedfs/s3.json:ro \
  chrislusf/seaweedfs:4.51 mini -dir=/data -s3.config=/etc/seaweedfs/s3.json
```

Media-sync settings:

```shell
  -e DRIVER=s3 \
  -e S3__ENDPOINT=http://localhost:8333 \
  -e S3__ACCESS_KEY=my-access-key \
  -e S3__SECRET_KEY=my-secret-key \
  -e S3__BUCKET=media \
```

## Garage

Garage needs a configuration file `garage.toml`. This one runs a single node with S3 API on port 3900 and
website endpoint on port 3902, which serves the files without credentials:

```toml
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "sqlite"
replication_factor = 1

rpc_bind_addr = "[::]:3901"
rpc_public_addr = "127.0.0.1:3901"
# generate with: openssl rand -hex 32
rpc_secret = "<random 64 hex characters>"

[s3_api]
s3_region = "garage"
api_bind_addr = "[::]:3900"
root_domain = ".s3.garage.localhost"

[s3_web]
bind_addr = "[::]:3902"
root_domain = ".web.garage.localhost"
```

Start Garage and set up the node, a key and a bucket for media-sync with `garage` CLI:

```shell
docker run -d --name garage \
  -p 3900:3900 -p 3902:3902 \
  -v $(pwd)/garage.toml:/etc/garage.toml:ro \
  -v $(pwd)/garage_meta:/var/lib/garage/meta \
  -v $(pwd)/garage_data:/var/lib/garage/data \
  dxflrs/garage:v2.5.0

alias garage="docker exec garage /garage"
garage status                                  # note the node ID
garage layout assign -z dc1 -c 10G <node ID>
garage layout apply --version 1
garage key create media-sync                   # note the key ID and secret key
garage bucket create media
garage bucket allow --read --write media --key media-sync
garage bucket website --allow media
```

Media-sync settings - region has to match `s3_region` from `garage.toml` and the bucket is not created by media-sync.
Files are served by the website endpoint at `http://<bucket>.web.garage.localhost:3902`
(`*.localhost` resolves to your computer, on a server use your own domain in `root_domain` of `[s3_web]`):

```shell
  -e DRIVER=s3 \
  -e S3__ENDPOINT=http://localhost:3900 \
  -e S3__REGION=garage \
  -e S3__ACCESS_KEY=<key ID> \
  -e S3__SECRET_KEY=<secret key> \
  -e S3__BUCKET=media \
  -e S3__CREATE_BUCKET=false \
  -e S3__PUBLIC_URL=http://media.web.garage.localhost:3902 \
```

## Silo

```shell
docker run -d --name silo \
  -p 9000:9000 -p 9001:9001 \
  -v $(pwd)/silo_data:/data \
  -e MINIO_ROOT_USER=my-access-key \
  -e MINIO_ROOT_PASSWORD=my-secret-key \
  pgsty/silo:RELEASE.2026-10-18T00-00-00Z server /data --console-address ":9001"
```

Media-sync settings:

```shell
  -e DRIVER=s3 \
  -e S3__ENDPOINT=http://localhost:9000 \
  -e S3__ACCESS_KEY=my-access-key \
  -e S3__SECRET_KEY=my-secret-key \
  -e S3__BUCKET=media \
```

To make files readable, use MinIO client `mc` included in Silo image:

```shell
docker exec silo mc alias set local http://localhost:9000 my-access-key my-secret-key
docker exec silo mc anonymous set download local/media
```

Web console of Silo is available at `http://localhost:9001`.
