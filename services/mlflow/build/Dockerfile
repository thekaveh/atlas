# Keep this default synchronized with MLFLOW_IMAGE in service.yml and Compose.
ARG BASE_IMAGE=ghcr.io/mlflow/mlflow:v3.15.1
FROM ${BASE_IMAGE}

COPY build/requirements.txt /tmp/atlas-mlflow-requirements.txt
COPY build/requirements-locked.txt /tmp/atlas-mlflow-requirements-locked.txt
RUN python -m pip install --no-cache-dir \
      -r /tmp/atlas-mlflow-requirements.txt \
      -c /tmp/atlas-mlflow-requirements-locked.txt \
 && python -m pip check \
 && python -c 'import boto3, psycopg2; from mlflow.store.artifact.s3_artifact_repo import S3ArtifactRepository; from sqlalchemy import create_engine; engine = create_engine("postgresql://atlas:atlas@127.0.0.1/atlas"); assert engine.dialect.dbapi.__name__ == "psycopg2"; assert S3ArtifactRepository'

WORKDIR /opt/atlas
COPY atlas_server.py /opt/atlas/atlas_server.py
