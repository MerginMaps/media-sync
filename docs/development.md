# Development information

Information related to development of Media Sync.

## Running Tests

You need to install also dev packages:
```shell
  pipenv install --dev
```

and run local S3-compatible server in docker (`silo`, `rustfs`, `garage` or `seaweedfs`, CI runs all of them),
the script prints `TEST_S3_*` variables for it:
```shell
  export $(test/s3_servers/start.sh silo)
```

To run automatic tests:
```shell
  export TEST_MERGIN_URL=<url>                # testing server
  export TEST_API_USERNAME=<username>
  export TEST_API_PASSWORD=<pwd>
  export TEST_API_WORKSPACE=<workspace>
  pipenv run pytest test/
```

Tests of S3 driver with S3-compatible server do not need Mergin Maps server:
```shell
  export $(test/s3_servers/start.sh garage)
  pipenv run pytest test/test_s3_compatible.py
```

Tests with AWS S3 are skipped unless an existing bucket is configured (the user only needs to list the bucket and to put
and get objects in it, uploaded test files are not deleted):
```shell
  export TEST_AWS_S3_BUCKET=<bucket>
  export TEST_AWS_S3_REGION=<region>
  export TEST_AWS_ACCESS_KEY_ID=<access-key>
  export TEST_AWS_SECRET_ACCESS_KEY=<secret-key>
```

## Releasing new version

1. Update `version.py`
2. Create a GitHub release with the new version tag (e.g. `0.1.0`). The `Build Docker Image` workflow builds the container for `linux/amd64` and `linux/arm64` and pushes it to Docker Hub with both the version tag and the `latest` tag. Pre-releases are not pushed.
