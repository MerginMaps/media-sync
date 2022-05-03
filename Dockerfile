FROM python:3.8-slim-buster
MAINTAINER Martin Dobias "martin.dobias@lutraconsulting.co.uk"

# to fix issue with mod_spatialite.so
RUN apt-get update && apt-get install -y libsqlite3-mod-spatialite && rm -rf /var/lib/apt/lists/*

# istall dependencies via pipenv system-wide
RUN pip3 install --upgrade pip
RUN pip3 install pipenv
COPY Pipfile Pipfile.lock ./
RUN pipenv install --system --deploy

# media sync code
WORKDIR /mergin-media-sync
COPY version.py config.py drivers.py media_sync.py media_sync_daemon.py ./

# create deafult config file (can be overridden with env variables)
COPY config.yaml.default ./config.yaml
