FROM python:3.12-slim-trixie AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential cmake libsqlite3-dev && \
    rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --upgrade pip pipenv
COPY Pipfile Pipfile.lock ./
# export locked deps (with hashes) and install them into an isolated prefix copied to the runtime stage
RUN pipenv requirements --hash > requirements.txt && \
    pip3 install --no-cache-dir --require-hashes --prefix=/install -r requirements.txt

FROM python:3.12-slim-trixie
LABEL maintainer="Martin Dobias <martin.dobias@lutraconsulting.co.uk>"

# mod_spatialite is loaded at runtime
# media-types provides /etc/mime.types to set correct Content-Type of uploaded files (e.g. webp, m4a)
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends libsqlite3-mod-spatialite media-types && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# drop packaging tooling from the runtime image
RUN pip3 uninstall -y pip setuptools wheel 2>/dev/null; \
    rm -rf /usr/local/lib/python3.12/ensurepip/_bundled /usr/local/lib/python3.12/site-packages/pip*

# media sync code
WORKDIR /mergin-media-sync
COPY version.py config.py drivers.py media_sync.py media_sync_daemon.py ./

# create deafult config file (can be overridden with env variables)
COPY config.yaml.default ./config.yaml
