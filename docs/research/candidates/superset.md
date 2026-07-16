---
category-fit: apps
generated: 2026-07-04
license: Apache-2.0
name: Apache Superset
referenced-by: [minio]
slug: superset
type: external-service
upstream: https://github.com/apache/superset
---

# Apache Superset

## 1. Headline
Apache-licensed BI and dashboard application for analyst-facing charts over curated SQL datasets, best suited for Atlas once Trino/Iceberg or Postgres analytics schemas have useful content and SSO is credible.

## 2. Watchlist decision (2026-07-04)

Keep Superset on the watchlist for now: Atlas **must not add `services/superset/service.yml` yet** until there are meaningful Trino/Iceberg or Postgres analytics datasets and a credible SSO route/auth story. Current Atlas already has a root dashboard for service discovery and Grafana for operational telemetry; Superset should arrive only when it is clearly the analyst BI surface over curated datasets.

Superset complements Grafana and the Atlas root dashboard rather than competing with them. Grafana remains for metrics, logs, traces, and alerts. The root dashboard remains service discovery, health, and launch context. Superset is the future BI layer for humans who want charts, semantic datasets, SQL Lab, and dashboard publishing over warehouse or application analytics data.

The blocker is security and data maturity, not product fit. Official Superset docs still emphasize production hardening: a unique `SUPERSET_SECRET_KEY`, a production metadata database instead of SQLite, HTTPS/reverse-proxy awareness, Flask-AppBuilder roles, OAuth/OIDC-style integration through FAB/Authlib configuration, and database drivers for each datasource. Atlas' current Trino integration is intentionally no-auth local development, so a broad BI UI over it would be premature without SSO and datasource credential policy.

Future service shape, if a later BI ticket promotes this:

- Track membership: `data-eng`, `ml-eng`, and `all`. Do not create a new BI track unless Atlas adopts multiple BI services.
- Service category: `apps`.
- Source values/default: `SUPERSET_SOURCE=disabled|container`, disabled by default.
- Wizard placement: data/ML analytics section after Trino/Iceberg and after SSO/auth prompts if they are enabled. Prompt copy should say it enables analyst dashboards over Trino/Iceberg and optional Postgres analytics schemas.
- Topology and port strategy: allocate one `apps` topology slot for the web UI only when a manifest is added.
- Kong alias and route behavior: `superset.localhost` only when `SUPERSET_SOURCE=container`; route must be protected by Superset login at minimum and preferably SSO or Kong route auth once the SSO pilot exists.
- Direct URL expectations: direct host port for local admin/bootstrap; Kong URL for browser use.
- Required dependencies: Supabase/Postgres metadata database, Redis for cache/tasks if enabled, generated `SUPERSET_SECRET_KEY`, generated admin credentials, Trino when the first BI dataset is the lakehouse, and SSO/auth docs before broad exposure.
- Optional dependencies: Iceberg REST or Lakekeeper, MinIO lakehouse data, MLflow/Label Studio analytics tables, SMTP/reports later, and Grafana only as link-out context.
- Downstream consumers: humans through the BI UI, plus an optional Atlas root dashboard link card.
- `data_flow.calls` topology edges for a future service: `superset -> supabase` for metadata, `superset -> redis` for cache/tasks, `superset -> trino` for lakehouse SQL, optional `superset -> authentik/keycloak`, and optional SQL-level reads from MLflow or Label Studio analytics exports.
- Init companion: yes. It must run database migrations, create the admin user, install or verify datasource drivers, import datasource definitions only when dependencies are enabled, and remain idempotent.
- Volumes and secrets: Superset metadata lives in Supabase/Postgres; optional config volume for `superset_config.py`; generated admin password and `SUPERSET_SECRET_KEY`; scoped Trino/Postgres datasource credentials; no hardcoded example secrets.
- Dataset readiness gate: at least one useful lakehouse or Postgres analytics dataset must exist, with sample dashboards or importable dashboard JSON, before adding the service.
- Tests required for a future service PR: manifest validation, source validation, env assembly, topology/category, track membership, Kong route/auth, compose source-permutation coverage, init idempotency, docs drift, datasource config generation, disabled-dependency behavior, and at least one smoke that can connect to Trino or a test Postgres schema.
- Edge cases: disabled Trino/Iceberg, disabled Redis, stale `.env`, missing secret key, metadata DB migration retry, custom `BASE_PORT`, prod profile route/auth restrictions, SSO disabled, datasource credential rotation, SQL Lab access control, and generated-doc drift.

## 3. Problem it solves
Atlas now has the pieces for lakehouse analytics: MinIO-backed Iceberg tables, Trino SQL, Spark, JupyterHub, Zeppelin, and data-eng-lab scenario data. What it does not yet have is a business-user BI surface for curated tables, chart building, SQL exploration, and shareable dashboards. Superset becomes valuable once Atlas has datasets worth browsing outside notebooks and once those dashboards can be protected coherently.

## 4. Stack wiring sketch
- superset -> supabase for its metadata database and encrypted connection records.
- superset -> redis for cache, async tasks, and optional reporting workers.
- superset -> trino for SQL over the Iceberg/MinIO lakehouse.
- superset -> postgres analytics schemas if Atlas creates app-side reporting tables.
- optional superset -> authentik/keycloak for SSO.
- optional root-dashboard -> superset as a launch link only.
- no direct superset -> grafana dependency; cross-links are enough.

## 5. Effort
medium-to-large — one web app is straightforward, but the production shape needs a metadata DB, secret generation, init/migration, datasource driver packaging, datasource provisioning, route/auth decisions, and sample dashboard data. The first version should not ship without a useful dataset.

## 6. Risks & open questions
- Security: broad BI surfaces expose data exploration, SQL Lab, charts, and embedded credentials. SSO and role mapping matter before real data appears.
- Datasource credentials: Trino currently accepts any user string in Atlas' local slice; Superset needs a scoped service credential or a clear local-only policy.
- Overlap: Superset should not replace Grafana's operational dashboards or the root dashboard's service-discovery role.
- Operational weight: Superset may need Redis, Celery workers, browser/reporting dependencies, and metadata migrations if reports/alerts are enabled.
- Dataset readiness: without curated Trino/Iceberg or Postgres analytics tables, Superset would become an empty UI.

## 7. Upstream evidence
- https://github.com/apache/superset
- https://superset.apache.org/admin-docs/configuration/configuring-superset/
- https://superset.apache.org/admin-docs/security/securing_superset/
- https://superset.apache.org/admin-docs/security/
- https://superset.apache.org/user-docs/databases/
