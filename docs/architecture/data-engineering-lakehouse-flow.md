# 6.9. Data Engineering Lakehouse Flow

MinIO, Iceberg REST, Spark, JupyterHub, Zeppelin, Airflow, Trino, Redpanda, Jenkins, and init containers.

## 1. Diagram

[Open the interactive diagram](./data-engineering-lakehouse-flow.html).

## 2. How To Read This View

MinIO is the object data plane and Iceberg REST owns table metadata. Spark and Trino execute against that shared lakehouse; JupyterHub, Zeppelin, and Airflow submit interactive or scheduled work, while Redpanda supplies streaming inputs.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`
