# 5.2.54. Trino

Trino is an optional, disabled-by-default SQL query engine for the Data Engineering track. Atlas wires Trino to the existing Iceberg REST catalog and MinIO-backed lakehouse buckets so notebooks, Zeppelin, Airflow tasks, and local tools can query the same tables through a SQL surface.

## 1. Overview

Image: `trinodb/trino:482`.

The first Atlas slice is intentionally narrow: one single-node coordinator, one `lakehouse` catalog, and no extra workers. The catalog uses Iceberg REST for table metadata and MinIO for object storage. This keeps Trino aligned with the Spark/Iceberg path already used by the data-eng stack.

## 2. Access

| Surface | URL | Notes |
|---|---|---|
| Kong | `http://trino.localhost:${KONG_HTTP_PORT}` | Routed only when `TRINO_SOURCE=container`. |
| Direct | `http://localhost:${TRINO_PORT}` | Coordinator UI and HTTP API. |
| In-network | `http://trino:8080` | Use from notebooks, Zeppelin JDBC, Airflow tasks, and other containers. |

## 3. Configuration

```dotenv
TRINO_SOURCE=disabled
TRINO_IMAGE=trinodb/trino:482
TRINO_PORT=
TRINO_SCALE=
ICEBERG_REST_SOURCE=disabled
MINIO_SOURCE=container
```

`TRINO_SOURCE=container` requires both `MINIO_SOURCE=container` and `ICEBERG_REST_SOURCE=container`. The bootstrapper fails early if either dependency is disabled.

## 4. Architecture & Wiring

The mounted catalog file at `services/trino/catalog/lakehouse.properties` defines:

- `connector.name=iceberg`
- `iceberg.catalog.type=rest`
- `iceberg.rest-catalog.uri=http://iceberg-rest:8181`
- `iceberg.rest-catalog.warehouse=s3://lakehouse/`
- `fs.s3.enabled=true` for Trino 482 native S3 access to MinIO at `http://minio:9000`
- scoped Iceberg MinIO credentials through `${ENV:MINIO_ICEBERG_ACCESS_KEY}` and `${ENV:MINIO_ICEBERG_SECRET_KEY}`

This first local-development slice has no Trino authenticator configured. The
example user `atlas` is a convention shared by Atlas notebooks and clients, not
an authentication boundary; the local coordinator accepts any user string until
a future auth issue adds a real authenticator. Do not treat this no-auth shape
as production access control.

Minimal SQL smoke once Spark or another writer has created tables:

```sql
SHOW CATALOGS;
SHOW SCHEMAS FROM lakehouse;
SHOW TABLES FROM lakehouse.bronze;
SELECT * FROM lakehouse.bronze.<table_name> LIMIT 10;
```

Atlas seeds a Zeppelin JDBC interpreter when both `ZEPPELIN_SOURCE=container` and `TRINO_SOURCE=container`. Use the named Zeppelin 0.12.1 prefix `%trino`:

```text
%trino
SHOW TABLES FROM lakehouse.bronze;
```

The seeded profile uses JDBC URL `jdbc:trino://trino:8080/lakehouse`, driver class `io.trino.jdbc.TrinoDriver`, and Maven dependency `io.trino:trino-jdbc:482`. Zeppelin's generic JDBC docs still describe `%jdbc(prefix)` for multiple connections; Atlas uses the named interpreter prefix because Zeppelin 0.12.1 created JDBC profiles run as `%trino` in this stack.

Python clients inside the Docker network can use the official `trino` DB-API package:

```python
import trino

conn = trino.dbapi.connect(
    host="trino",
    port=8080,
    user="atlas",
    catalog="lakehouse",
    schema="gold",
)
cur = conn.cursor()
cur.execute("SHOW SCHEMAS FROM lakehouse")
print(cur.fetchall())
```

Host-side Python clients use the assigned host port:

```python
import os
import trino

conn = trino.dbapi.connect(
    host="localhost",
    port=int(os.environ["TRINO_PORT"]),
    user="atlas",
    catalog="lakehouse",
    schema="gold",
)
```

Live CTAS smoke after the lakehouse path is up:

```sql
CREATE SCHEMA IF NOT EXISTS lakehouse.gold;
CREATE TABLE lakehouse.gold.atlas_trino_ctas_smoke AS
SELECT 1 AS id, 'atlas' AS note;
SELECT * FROM lakehouse.gold.atlas_trino_ctas_smoke;
DROP TABLE lakehouse.gold.atlas_trino_ctas_smoke;
```

Atlas does not create bronze/silver/gold namespaces at stack startup; data-eng-lab or the operator owns those namespaces. The `CREATE SCHEMA` line is part of the live smoke only.

## 5. Dependencies & Integrations

### 5.1 Current — Upstream (this service calls)

| Service | Category |
|---|---|
| iceberg-rest | data |
| minio | data |

### 5.2 Current — Downstream (services that call this)

| Service | Category |
|---|---|
| zeppelin | apps |

### 5.3 Architecture diagram

![trino architecture](./architecture.svg)

[Open the interactive HTML diagram](./architecture.html) for a full-screen view.

### 5.4 Future — Missing pair integrations

Add richer notebook examples that create a Spark-written Iceberg table and query it through Trino.

### 5.5 Future — Candidate new services

Superset or another BI UI can sit downstream of Trino once the lakehouse query path is stable.

### 5.6 Future — Unused features in this service

Worker scaling, query resource groups, access-control files, and additional catalogs are intentionally out of scope for this first integration.

## 6. Troubleshooting

- **Catalog missing:** confirm `ICEBERG_REST_SOURCE=container` and that `iceberg-rest` is healthy before Trino starts.
- **S3 access denied:** confirm `minio-init` completed and populated `MINIO_ICEBERG_ACCESS_KEY` / `MINIO_ICEBERG_SECRET_KEY`.
- **Kong alias does not load:** run `./start.sh --setup-hosts` so `trino.localhost` resolves locally.
