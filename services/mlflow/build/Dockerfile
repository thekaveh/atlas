# Keep this default synchronized with MLFLOW_IMAGE in service.yml and Compose.
ARG BASE_IMAGE=ghcr.io/mlflow/mlflow:v3.15.1
FROM ${BASE_IMAGE}

# Debian security fixes normally arrive through a reviewed BASE_IMAGE refresh.
# Atlas pins exactly MLflow 3.15.1 (atlas_server.py guard, gateway SSRF review),
# whose Debian 11 base lags 32 fixed HIGH advisories (2026-09-18, #1002), and
# the newest published v3.16.1 base still carries 45, so take the published
# updates the same way services/backend, services/jupyterhub and services/neo4j
# do. Debian 11 left LTS on 2026-08-31 and deb.debian.org no longer serves the
# bullseye-security pool, so the last LTS state is pinned through
# snapshot.debian.org at DEBIAN_SNAPSHOT: the same date always yields the same
# packages, which keeps this layer reproducible. Moving off the EOL base needs
# the MLflow 3.16.x re-review tracked on #1002.
ARG DEBIAN_SNAPSHOT=20260901T000000Z
RUN set -eux; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s bullseye main\n' "${DEBIAN_SNAPSHOT}" > /etc/apt/sources.list; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/%s bullseye-security main\n' "${DEBIAN_SNAPSHOT}" >> /etc/apt/sources.list; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s bullseye-updates main\n' "${DEBIAN_SNAPSHOT}" >> /etc/apt/sources.list; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends; \
    rm -rf /var/lib/apt/lists/*

COPY build/requirements.txt /tmp/atlas-mlflow-requirements.txt
COPY build/requirements-locked.txt /tmp/atlas-mlflow-requirements-locked.txt
RUN python -m pip install --no-cache-dir \
      -r /tmp/atlas-mlflow-requirements.txt \
      -c /tmp/atlas-mlflow-requirements-locked.txt \
 && python -m pip check \
 && python -c 'import boto3, psycopg2; from mlflow.store.artifact.s3_artifact_repo import S3ArtifactRepository; from sqlalchemy import create_engine; engine = create_engine("postgresql://atlas:atlas@127.0.0.1/atlas"); assert engine.dialect.dbapi.__name__ == "psycopg2"; assert S3ArtifactRepository'

WORKDIR /opt/atlas
COPY atlas_server.py /opt/atlas/atlas_server.py
