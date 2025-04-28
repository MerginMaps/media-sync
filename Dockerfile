FROM python:3.11-slim-buster
# LABEL instead of MAINTAINER (fixes deprecation warning)
LABEL maintainer="Martin Dobias <martin.dobias@lutraconsulting.co.uk>"

# to fix issue with mod_spatialite.so and pygeodiff building
RUN apt-get update && \
    apt-get install -y libsqlite3-mod-spatialite build-essential cmake libsqlite3-dev && \
    rm -rf /var/lib/apt/lists/*

# install dependencies via pipenv system-wide
RUN pip3 install --upgrade pip
RUN pip3 install pipenv
COPY Pipfile Pipfile.lock ./
RUN pipenv install --system --deploy

# media sync code
WORKDIR /mergin-media-sync
COPY version.py config.py drivers.py media_sync.py media_sync_daemon.py ./

# create deafult config file (can be overridden with env variables)
COPY config.yaml.default ./config.yaml