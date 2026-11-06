#!/usr/bin/env bash
# Start S3-compatible server in docker for tests and print TEST_S3_* variables for it.
# All servers use the same dummy credentials, which tests use by default (see test/conftest.py).
#
# Usage: test/s3_servers/start.sh <silo|rustfs|garage|seaweedfs>
#   locally: export $(test/s3_servers/start.sh rustfs)
#   in CI:   test/s3_servers/start.sh rustfs >> "$GITHUB_ENV"
#
# The server listens on 127.0.0.1:${TEST_S3_PORT:-9000}, the container is named media-sync-<server>.
set -euo pipefail

SERVER=${1:?Usage: $0 <silo|rustfs|garage|seaweedfs>}
PORT=${TEST_S3_PORT:-9000}
DIR=$(cd "$(dirname "$0")" && pwd)
CONTAINER="media-sync-$SERVER"
# in format required by Garage (GK + 24 hex chars, 64 hex chars), other servers accept any key
ACCESS_KEY=GK000000000000000000000001
SECRET_KEY=0000000000000000000000000000000000000000000000000000000000000002

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

case "$SERVER" in
  silo)
    docker run -d --name "$CONTAINER" -p "127.0.0.1:$PORT:9000" \
      -e MINIO_ROOT_USER=$ACCESS_KEY -e MINIO_ROOT_PASSWORD=$SECRET_KEY \
      pgsty/silo:RELEASE.2026-10-18T00-00-00Z server /data >/dev/null
    ;;
  rustfs)
    docker run -d --name "$CONTAINER" -p "127.0.0.1:$PORT:9000" \
      -e RUSTFS_ACCESS_KEY=$ACCESS_KEY -e RUSTFS_SECRET_KEY=$SECRET_KEY \
      rustfs/rustfs:1.0.3 >/dev/null
    ;;
  garage)
    docker run -d --name "$CONTAINER" -p "127.0.0.1:$PORT:3900" \
      -v "$DIR/garage.toml:/etc/garage.toml:ro" \
      dxflrs/garage:v2.5.0 >/dev/null
    ;;
  seaweedfs)
    docker run -d --name "$CONTAINER" -p "127.0.0.1:$PORT:8333" \
      -v "$DIR/seaweedfs_s3.json:/etc/seaweedfs/s3.json:ro" \
      chrislusf/seaweedfs:4.51 mini -dir=/data -s3.config=/etc/seaweedfs/s3.json >/dev/null
    ;;
  *)
    echo "Unknown server: $SERVER" >&2
    exit 1
    ;;
esac

# wait until server answers http requests
for _ in $(seq 1 60); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/")" != "000" ]; then
    break
  fi
  sleep 1
done

if [ "$SERVER" = garage ]; then
  # single node cluster with media-sync key and bucket, the key can not create buckets itself
  # output of garage CLI is shown only on failure
  garage() {
    local output
    if ! output=$(docker exec "$CONTAINER" /garage "$@" 2>&1); then
      echo "$output" >&2
      return 1
    fi
    echo "$output"
  }
  NODE_ID=$(garage status | awk '/^[0-9a-f]{16}/ {print $1; exit}')
  garage layout assign -z dc1 -c 1G "$NODE_ID" >/dev/null
  garage layout apply --version 1 >/dev/null
  garage key import --yes -n media-sync "$ACCESS_KEY" "$SECRET_KEY" >/dev/null
  garage bucket create test >/dev/null
  garage bucket allow --read --write --owner test --key media-sync >/dev/null
  echo "TEST_S3_REGION=garage"
  echo "TEST_S3_CREATE_BUCKET=false"
fi

echo "TEST_S3_ENDPOINT=127.0.0.1:$PORT"
