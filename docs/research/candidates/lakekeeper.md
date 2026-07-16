---
category-fit: data
generated: 2026-07-04
license: Apache-2.0
name: Lakekeeper
referenced-by: [minio]
slug: lakekeeper
type: external-service
upstream: https://github.com/lakekeeper/lakekeeper
---

# Lakekeeper

## 1. Headline
Apache-licensed Iceberg REST catalog implementation that adds a real management layer around warehouses, identifiers, authn/authz, vended credentials, remote signing, and production catalog operations.

## 2. Watchlist decision (2026-07-04)

Keep Lakekeeper on the watchlist for now: Atlas **must not add `services/lakekeeper/service.yml` yet** because the current Apache Iceberg REST fixture already satisfies the local `data-eng-lab` A1-A9 contract. Atlas should first prove that MinIO-backed lakehouse usage has real write/concurrency pressure, multi-engine governance requirements, vended credentials requirements, or OIDC/authorization needs that the current catalog cannot satisfy.

This is a promotion gate, not a rejection. Lakekeeper is a strong candidate once Atlas moves beyond a single local warehouse and into cross-user catalog governance. Upstream Lakekeeper now documents features that matter to Atlas later: Postgres-backed state, projects and warehouses, a `/catalog` Iceberg REST API, a `/management` API, case-insensitive but case-preserving identifiers, OIDC integration, OpenFGA/Cedar-style authorization options, soft deletion, S3 remote signing, vended credentials, optional event delivery, and external Vault-like secret stores.

Future service shape, if a later lakehouse governance ticket promotes this:

- Track membership: `data-eng` and `all`; optionally `ml-eng` only if MLflow/model-registry analytics tables become real consumers.
- Service category: `data`.
- Source values/default: `LAKEKEEPER_SOURCE=disabled|container`, disabled by default.
- Relationship to existing catalog: do not overload `ICEBERG_REST_SOURCE` until Atlas has a migration plan. The first implementation should either be mutually exclusive with `iceberg-rest` or explicitly run against a separate warehouse.
- Wizard placement: data-eng/lakehouse section, after MinIO and before Spark/Trino consumers, with prompt copy explaining that Lakekeeper is an advanced Iceberg REST catalog for governance, authz, vended credentials, and multi-engine catalog management.
- Topology and port strategy: allocate one `data` topology slot only when a service manifest is added. Catalog traffic can remain internal; management APIs need route protection before browser exposure.
- Kong alias and route behavior: no public unauthenticated management route. If a route is added, it must be gated by SSO or Kong auth. Query engines should prefer internal Docker DNS.
- Direct URL expectations: in-stack clients use `http://lakekeeper:8181/catalog` or the chosen container route. Host direct access is for smoke tests only.
- Required dependencies: Supabase/Postgres-compatible persistence, MinIO/S3 warehouse storage, generated Lakekeeper config/encryption secrets, and an idempotent bootstrap path for a `lakehouse` warehouse.
- Optional dependencies: Authentik/Keycloak for OIDC, OpenFGA or Cedar authorization, OpenBao/Infisical/Vault-like secret storage, Redpanda/Kafka or NATS events, and future Superset or Dagster consumers.
- Downstream consumers: Spark Connect, standalone Spark, Trino, JupyterHub/PyIceberg, Zeppelin, Airflow, and later Superset/Dagster if those services land.
- `data_flow.calls` topology edges for a future service: `lakekeeper -> minio`, `lakekeeper -> supabase`, optional `lakekeeper -> authentik/keycloak`, optional `lakekeeper -> openfga`, optional `lakekeeper -> redpanda/nats`; consumers `spark -> lakekeeper`, `trino -> lakekeeper`, `jupyterhub -> lakekeeper`, `zeppelin -> lakekeeper`, and `airflow -> lakekeeper`.
- Init companion: yes. It must initialize the database/schema, create or verify the default project and `lakehouse` warehouse, attach the existing MinIO bucket/account, and remain idempotent.
- Volumes and secrets: no table-data volume; table data remains in MinIO and metadata remains in Postgres. Secrets should include Lakekeeper encryption/config secrets and scoped MinIO warehouse credentials, never MinIO root credentials.
- Migration/rollback: require a documented path from `iceberg-rest` metadata to Lakekeeper or a fresh-warehouse strategy. Do not point two catalog writers at the same warehouse location without explicit ownership rules.
- Tests required for a future service PR: manifest validation, source validation, env assembly, topology slot/category checks, track membership, compose source-permutation coverage, Kong route/auth tests, docs drift, client config tests for Spark/Trino/JupyterHub/Zeppelin/Airflow, init idempotency tests, and a live-gated smoke that creates a namespace/table and reads it through two engines.
- Edge cases: disabled MinIO or Supabase, stale `.env`, custom `BASE_PORT`, existing `iceberg-rest` metadata, namespace case-sensitivity changes, soft-delete/table-location behavior, prod profile restrictions, missing OIDC/authz backends, vended credentials disabled, and generated-doc drift.

## 3. Problem it solves
Atlas' current lakehouse catalog is intentionally small: an Apache Iceberg REST fixture rebuilt with the PostgreSQL JDBC driver and backed by Supabase Postgres. That is enough for local notebooks, Spark, Trino, Airflow, and `data-eng-lab` scenarios. Lakekeeper becomes interesting when Atlas needs a managed catalog surface: project/warehouse administration, safer identifier behavior across engines, remote signing or vended credentials, catalog-level authz, soft deletion, and event-driven governance hooks.

## 4. Stack wiring sketch
- lakekeeper -> supabase for Postgres catalog and secret persistence.
- lakekeeper -> minio for the `lakehouse` warehouse and object storage.
- spark -> lakekeeper for Spark SQL and Spark Connect Iceberg catalog operations.
- trino -> lakekeeper for multi-engine SQL reads/writes.
- jupyterhub -> lakekeeper through PyIceberg and notebook clients.
- zeppelin -> lakekeeper through the seeded Spark interpreter.
- airflow -> lakekeeper through SparkSubmit jobs and optional client-side checks.
- optional authentik/keycloak -> lakekeeper for OIDC.
- optional openfga/cedar -> lakekeeper for authorization policy.
- optional redpanda/nats <- lakekeeper for catalog events.

## 5. Effort
medium-to-large — a stateless Rust service is not the hard part. The hard part is safe coexistence or migration from `iceberg-rest`, warehouse bootstrap, authz/secret posture, client rewiring, and proving Spark/Trino/JupyterHub/Zeppelin/Airflow still satisfy the delivered `data-eng-lab` lakehouse contract.

## 6. Risks & open questions
- Migration risk: Atlas already has `iceberg-rest` metadata in Supabase and a `lakehouse` warehouse in MinIO. Replacing the catalog must not strand existing tables.
- Authz complexity: Lakekeeper's richer authn/authz value depends on OIDC and an authorization backend; adding it before Atlas' SSO posture is settled could create a half-secured catalog.
- Client config drift: Spark, Trino, PyIceberg, Zeppelin, and Airflow all need consistent `/catalog` URI and warehouse settings.
- Warehouse ownership: vended credentials and soft deletion change how table locations are created and reused; tests must cover table recreate and namespace behavior.
- Operational fit: Lakekeeper should be justified by real multi-engine or multi-user demand, not by novelty while the current fixture works.

## 7. Upstream evidence
- https://github.com/lakekeeper/lakekeeper
- https://docs.lakekeeper.io/getting-started/
- https://docs.lakekeeper.io/docs/latest/concepts/
- https://docs.lakekeeper.io/docs/latest/engines/
- https://iceberg.apache.org/rest-catalog-spec/
