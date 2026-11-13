# Mergin Maps Media Sync
Sync media files from Mergin Maps projects to other storage backends. Currently, supported backends are AWS S3 (and S3-compatible storage), Google Drive and local drive (mostly used for testing).

Sync works in two modes, in COPY mode, where media files are only copied to external drive and MOVE mode, where files are subsequently removed from Mergin Maps project (on cloud). Optionally, references to media files in project geopackages can be updated.

![Overview](docs/images/overview.png)

## Getting started

There are three steps to get Media Sync up and running:

1. Install the tool. See the [installation guide](docs/install.md)

2. Set up configuration and storage backend. See [Using Media Sync](docs/using.md).

3. Run the tool (see [Using Media Sync](docs/using.md))

Not sure where to start? Check out our [quick start](docs/quick_start.md) guide to set up sync from a new Mergin Maps project to your AWS S3 bucket.

## Documentation

- [How to install](docs/install.md)
- [Using Media Sync](docs/using.md)
- [AWS S3 setup](docs/aws-s3-setup.md)
- [S3-compatible storage](docs/s3-compatible.md)
- [Google Drive setup](docs/google-drive-setup.md)
- [For developers](docs/development.md)

<div><a href="https://community.merginmaps.com">Join our community</a> and ask questions!</div>
