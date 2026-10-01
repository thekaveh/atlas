# Keep this default synchronized with MLFLOW_IMAGE in service.yml and Compose.
ARG BASE_IMAGE=ghcr.io/mlflow/mlflow:v3.16.1
FROM ${BASE_IMAGE}

# Debian security fixes normally arrive through a reviewed BASE_IMAGE refresh.
# Atlas pins exactly MLflow 3.16.1 (atlas_server.py guard, #1287). Its published
# base is Debian 13 (trixie), a supported release whose own apt sources carry
# trixie-security, so take the published updates the same way services/backend,
# services/jupyterhub and services/neo4j do; the final-image scan still gates
# every refresh. (The 3.15.1 base was Debian 11, past LTS, and needed a pinned
# snapshot.debian.org state instead.) The build refuses any other base codename,
# so a future base change gets this layer reviewed rather than upgraded blindly.
RUN set -eux; \
    . /etc/os-release; \
    if [ "${VERSION_CODENAME:-}" != trixie ]; then \
      echo "MLflow base is ${VERSION_CODENAME:-unknown}, not trixie: review this update layer before building" >&2; \
      exit 1; \
    fi; \
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
