---
category-fit: agents
generated: 2026-07-04
license: Apache-2.0
name: Dagster
referenced-by: [minio]
slug: dagster
type: external-service
upstream: https://github.com/dagster-io/dagster
---

# Dagster

## 1. Headline
Asset-aware orchestration platform that could model Atlas lakehouse tables, ML artifacts, and downstream dashboards as first-class assets once Atlas has a real asset-lineage workflow and an Airflow coexistence plan.

## 2. Watchlist decision (2026-07-04)

Keep Dagster on the watchlist for now: Atlas **must not add `services/dagster/service.yml` yet** because Airflow remains Atlas' default scheduler for code-defined DAGs, SparkSubmit lakehouse jobs, seeded service connections, and the data-eng-lab validation contract. Dagster should not become a second scheduler with unclear table ownership.

Dagster is still a strong future candidate. Its asset model is a better conceptual fit for lineage-rich lakehouse workflows than generic task DAGs, and upstream docs now include Docker Compose deployment patterns, instance configuration, run coordinators, daemons, code locations, and Airflow migration/Airlift paths. The right first slice is not "run another scheduler"; it is "model one concrete asset-lineage workflow" and decide whether Dagster observes Airflow, migrates selected DAGs through Airlift, or owns a clearly separate class of asset jobs.

Future service shape, if a later asset-orchestration ticket promotes this:

- Track membership: `data-eng`, `ml-eng`, and `all`. Do not create a new orchestration track unless Atlas introduces an explicit orchestrator selector.
- Service category: `agents`, matching Airflow and n8n as workflow orchestration surfaces.
- Source values/default: `DAGSTER_SOURCE=disabled|container`, disabled by default.
- Relationship to Airflow: Airflow remains the default scheduler until a migration/coexistence plan says otherwise. Do not run duplicate schedules over the same lakehouse tables.
- Wizard placement: data/ML orchestration section next to Airflow, with prompt copy warning that Airflow remains default unless a Dagster asset workflow is selected.
- Topology and port strategy: allocate one `agents` topology slot for the webserver only when a service manifest is added. Daemon and code-location ports stay internal.
- Kong alias and route behavior: `dagster.localhost` only when `DAGSTER_SOURCE=container`; route must be protected with Dagster login or upstream auth before production use.
- Direct URL expectations: direct host port for local admin/dev; Kong URL for browser use.
- Required dependencies: Supabase/Postgres for Dagster instance storage, generated config/secrets as needed, and the selected asset workflow dependencies.
- Optional dependencies: Airflow REST API for observation or Airlift, MinIO/Iceberg/Trino/Spark for lakehouse assets, MLflow for model assets, Label Studio for dataset-review assets, Superset for BI asset outputs, and OpenMetadata later for catalog governance.
- Downstream consumers: humans through the Dagster UI, and possibly Airflow only during a deliberate Airlift migration/proxy step.
- `data_flow.calls` topology edges for a future service: `dagster -> supabase`, optional `dagster -> airflow`, optional `dagster -> minio`, optional `dagster -> trino`, optional `dagster -> spark`, optional `dagster -> mlflow`, and optional `dagster -> label-studio`.
- Init companion: likely yes. It must bootstrap instance configuration, storage schema/migrations if needed, admin/dev auth if enabled, and seed only safe example assets.
- Containers: expect at least webserver, daemon, and one user-code/code-location container. A future PR must make this explicit instead of squeezing everything into one opaque container.
- Volumes and secrets: `DAGSTER_HOME`/config volume, workspace/code-location config, generated admin/auth secrets if used, scoped Airflow/Trino/MinIO/Spark credentials, no hardcoded example secrets.
- Asset-workflow readiness gate: identify one concrete asset graph first, such as `landing -> bronze -> silver -> gold` Iceberg tables, MLflow run-to-model promotion, or Label Studio export-to-training-data.
- Tests required for a future service PR: manifest validation, source validation, env assembly, topology/category, track membership, Kong route/auth, compose source-permutation coverage, init idempotency, docs drift, Airflow coexistence rules, disabled-dependency behavior, and at least one smoke for the chosen asset graph.
- Edge cases: disabled Airflow, disabled MinIO/Iceberg/Trino/Spark, duplicate schedule ownership, stale `.env`, custom `BASE_PORT`, code-location reloads, user-code image drift, prod profile route/auth restrictions, and generated-doc drift.

## 3. Problem it solves
Atlas already has Airflow for scheduled DAGs, but Airflow does not make data assets the center of the product. Dagster would become valuable once Atlas wants a visible asset graph for lakehouse tables, model artifacts, dataset review outputs, and BI dashboards. It should enter only when that asset graph exists and the user can see how Dagster complements or replaces specific Airflow responsibilities.

## 4. Stack wiring sketch
- dagster -> supabase for instance/run/event storage.
- dagster -> airflow when observing existing DAGs or using Airlift migration paths.
- dagster -> minio for asset inputs/outputs and lakehouse object storage.
- dagster -> trino for SQL asset checks over Iceberg tables.
- dagster -> spark for materializing Spark-backed assets.
- dagster -> mlflow for model/experiment assets if MLflow is enabled.
- dagster -> label-studio for reviewed dataset assets if Label Studio is enabled.
- optional root-dashboard -> dagster as a launch link only.

## 5. Effort
medium-to-large — the webserver and daemon are straightforward, but a useful Atlas integration needs a Postgres-backed instance, code-location packaging, run-launcher/executor choices, auth, Airflow boundary docs, and at least one real asset graph.

## 6. Risks & open questions
- Scheduler duplication: Airflow already owns Atlas DAG scheduling and SparkSubmit lakehouse execution.
- Code-location packaging: Dagster deployments normally separate framework containers from user-code containers; Atlas must not hide that complexity in an opaque image.
- Migration posture: Airlift can observe or migrate Airflow work, but Atlas needs an explicit policy before mixing schedulers.
- Operational weight: webserver, daemon, code location, instance storage, and possible run workers add more moving parts than a docs-only asset graph.
- Value proof: without a concrete asset-lineage workflow, Dagster would be an empty UI next to an already-working Airflow stack.

## 7. Upstream evidence
- https://github.com/dagster-io/dagster
- https://docs.dagster.io/deployment/oss/deployment-options/docker
- https://docs.dagster.io/deployment/oss/oss-instance-configuration
- https://docs.dagster.io/deployment/oss/dagster-yaml
- https://docs.dagster.io/migration/airflow-to-dagster/basic-migration
- https://docs.dagster.io/migration/airflow-to-dagster/airlift-v1/task-level-migration
