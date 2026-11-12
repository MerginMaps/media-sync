# How to install Media Sync

## Docker

The easiest way to run Media Sync is with Docker image provided on our [docker hub repo](https://hub.docker.com/repository/docker/lutraconsulting/mergin-media-sync):

```shell
docker pull lutraconsulting/mergin-media-sync
```

See [Using Media Sync](using.md) for how to run the container.

You can also build your own local docker image, by first cloning the repo:

```shell
git clone git@github.com:lutraconsulting/mergin-media-sync.git
```

And then building the image:

```shell
docker build -t mergin_media_sync .
```

## Manual installation

If you would like to avoid the manual installation steps, please follow the guide on using sync with Docker above. We use pipenv for managing python virtual environment.

```shell
  pipenv install --three
```

If you get `ModuleNotFoundError: No module named 'skbuild'` error, try to update pip with command
`python -m pip install --upgrade pip`

Then set up configuration and run the tool as described in [Using Media Sync](using.md#running-without-docker).
