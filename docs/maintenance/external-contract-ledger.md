# External Dependency Contract Ledger

Durable ledger for maintenance checks against consumed external contracts, as
required by the overnight maintenance spec's external-dependency contract
section. Each row records what was checked, the pinned or configured version,
the current/latest status observed during the pass, and the evidence source.

## 1. 2026-07-02 Maintenance Pass

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| Docker Compose `include:` support | Minimum v2.20.3, recommended v2.26.0 | Existing `DockerManager.check_compose_version()` parses `docker compose version --short` | Compose version floor is now enforced during startup preflight before the modular `include:` compose file is used | Guarded by `bootstrapper/tests/test_start_dependency_preflight.py`. The floor was raised to v2.23.1 on 2026-11-01 ([section 11](#11-2026-11-01-maintenance-pass)). |
| scikit-learn requirement in backend | `scikit-learn>=1.9.0` | PyPI reports 1.9.0 as the latest release, released 2026-06-02 | Requirement is valid for the Python 3.12 backend image, but host Python 3.10 cannot collect it | Do not lower the requirement for host-tool compatibility; run audits with the target Python version |
| JupyterHub Python requirements | `torch==2.4.1`, `nltk` unpinned | `pip-audit 2.10.1` under host Python 3.10 collected the requirements and reported 22 known vulnerabilities across `torch 2.4.1` and `nltk 3.9.4` | Vulnerability status only; no automatic upgrade attempted because PyTorch/PyG wheels are image-coupled | Resolved 2026-07-14 by the coordinated PyTorch 2.11/PyG migration and NLTK 3.10 floor documented below |
| MinIO client service-account commands | `pgsty/mc:RELEASE.2026-09-16T00-00-00Z` (Silo client; was `minio/mc:RELEASE.2025-08-13T08-35-41Z`) | MinIO current docs center `mc admin accesskey`; legacy `mc admin user svcacct` remains a replacement-bound contract to verify before future client bumps | `services/minio/init/scripts/init-minio.sh` still uses `mc admin user svcacct` create/edit/list | Validated end to end on 2026-09-28 against the pinned Silo server (see "MinIO client provisioning"); `svcacct` stays the contract until a deliberate move to `mc admin accesskey` or to another server (#1277) |
| n8n community package load path | `n8nio/n8n:2.48.7` | Self-hosted n8n loads manually installed community packages from `/home/node/.n8n/nodes/node_modules`; the image ships Node 24 and `n8n-workflow` 2.48.3 | Atlas runs the same pinned n8n image as a pre-start init, installs a committed npm lock with scripts disabled into the shared user folder, and blocks n8n startup on failure; custom specs must be exact versions | Guarded by `bootstrapper/tests/test_shell_script_contracts.py`, Compose byte-equivalence, and live pinned-image startup validation |

## 2. 2026-07-14 Maintenance Pass

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| JupyterHub Python/PyG stack | Superseded PyTorch 2.11 / PyG 0.6 family | Official CPU and PyG wheel indexes resolved for Linux/Python 3.11 | Core pair, PyG wheel index, and extension set move together | Superseded 2026-08-02 by the Torch 2.13 / PyG 0.8 family below |
| Ray server/client protocol | Server images `2.56.0`; clients `>=2.56.0,<2.57` | Multi-architecture CPU/GPU image manifests available | Compose defaults, service manifest, generated env, Backend, and Jupyter clients share one minor line | Fragment, Ray configuration, env assembler, and security-floor tests pass |
| Airflow core/providers | Core image `3.3.2`; independently resolved current providers | Airflow 3.3.2 (PyPI, 2026-09-17) is the maintained release and publishes amd64/arm64 images; 3.3.1 carries PYSEC-2026-3988, -3989 and -3990, none of which PyPI reports against 3.3.2 | Official constraints install core in the base image; later provider install must not reapply core constraints | Linux/Python 3.13 locks match on both architectures with Spark 4.1.2. See [provider-graph evidence](#21-airflow-provider-graph-evidence) for the 3.3.2 refresh and remaining live check. |
| Parakeet GPU model stack | NeMo `>=3.0.0,<4.0`, ONNX `>=1.22`, Protobuf `>=5.29.6,<5.30`, Transformers `4.57.x` | NeMo 2.7.x still requires Transformers below 4.58 | Patched NeMo/model-file/protobuf floors resolve; remaining Transformers advisories require an operator-selected malicious model artifact | Request payloads cannot select `PARAKEET_MODEL`; compatibility exception is documented in requirements and `SECURITY.md` |
| Python package security floors | Click 8.4.2, Pillow 12.3.0, Requests 2.34.2, python-multipart 0.0.31/0.0.32 | OSV/PyPI audit run against every shipped requirements surface and both committed uv lockfiles | Patchable findings are upgraded; residual findings are classified by concrete code path and platform | Bootstrapper audit is clean; service exceptions are recorded in `SECURITY.md` and guarded by focused tests |
| DiskCache transitive advisory | `diskcache==5.6.3` via Ragas and Instructor | CVE-2025-69872 / GHSA-w8v5-vhqr-4h9v has no patched release as of 2026-07-14 | Backend Ragas code imports no disk-cache adapter and creates no attacker-writable cache directory; the vulnerable deserialization path is unreachable | Retain only as a documented exception; `test_ragas_advisory_surface_remains_unreachable` guards the code-path assumption |
| vLLM Metal managed host installer | Plugin `0.3.0.dev20260713103604`; core `0.24.0` | Latest signed GitHub release on 2026-07-13; upstream installer builds vLLM core 0.24.0 then installs the release wheel | Atlas now mirrors the upstream two-stage install using checksum-pinned release assets and verifies the API server import before recording success | Manager unit tests guard exact URLs, hashes, stale-environment reconciliation, and complete process-group shutdown |
| Docker Compose modular `include` floor | `>=2.20.3` | Docker's Compose file reference identifies 2.20.3 as the first release supporting top-level `include` | Startup must reject 2.20.0–2.20.2 before loading Atlas' modular Compose graph | Superseded 2026-11-01 by the 2.23.1 inline `configs.content` floor ([section 11](#11-2026-11-01-maintenance-pass)); `DockerManager`, preflight tests and operator docs now use 2.23.1 |
| Docling PDF pipeline controls | `docling==2.102.1` | The pinned API exposes `PdfPipelineOptions`, `TableFormerMode`, `AcceleratorDevice`, OCR full-page control, and formula/code enrichment | Atlas request and environment controls map to those exact pipeline fields; unsupported output formats fail validation | Pure contract tests cover every mapping without downloading models; provider tests guard stable public errors |
| Parakeet MLX aligned transcription | `parakeet-mlx==0.5.2` | The pinned result contract exposes `AlignedResult.sentences` with nested timestamped tokens | Segment and word responses serialize sentences and tokens rather than nonexistent top-level `segments`/`words` attributes | Focused fixture tests cover object and mapping results, absent timestamps, and request-flag semantics |
| Cloudflare Tunnel origin routing | `cloudflare/cloudflared:2026.6.1` | Cloudflare ingress rules support `httpHostHeader` to override the Host header sent to an origin | Each public hostname targets Kong and must override Host to the exact Atlas route alias, with Cloudflare Access in front | Service and security guides state the dual-layer requirement; a docs regression rejects omission |
| Local Deep Researcher runtime graph | Upstream commit `38f769f…`; generated hash-pinned lock | A fresh `pip-audit` found fixable Click, langchain-classic, LangSmith, and Soup Sieve advisories in the prior graph | Refresh adds explicit security floors while retaining the verified upstream lock and exact CLI/build pins | Regenerated locks resolve Click 8.4.2, langchain-classic 1.0.8, LangSmith 0.10.4, and Soup Sieve 2.8.4; `pip-audit` reports no known vulnerabilities |
| LightRAG document ingestion | Image `ghcr.io/hkuds/lightrag:v1.5.4` | Tagged `v1.5.4` routing and document APIs inspected on 2026-07-14 | `/documents/text` canonicalizes `file_source` to a basename and returns `409` for an existing source | Backend emits a deterministic path-and-content hash basename and treats only `409` as idempotent replay success; focused adapter tests cover stability, path uniqueness, and replay |
| Celery task retry envelope | `celery[redis]>=5.6.2,<5.7`; verified with 5.6.3 | Celery 5.6 task retry semantics inspected on 2026-07-14 | Replacement kwargs must clear original positional args; lease contention and transient recovery require independent counters; commit-ambiguous work/result claims must preserve ownership | RAG retries pass `args=()` with a complete kwargs envelope and retain the transient counter across lease-loss retries. Memory dispatch is kwargs-only; retries preserve a bounded transient counter and stable execution owner, while result-commit recovery resumes the Redis completion step without rerunning consolidation. |
| Weaviate object lifecycle | Image `cr.weaviate.io/semitechnologies/weaviate:1.38.17` | Official object CRUD and REST API inspected on 2026-07-15; the 2026-09-03 move from 1.38.2 to 1.38.13 and the 2026-10-01 move to 1.38.17 (#1286) are security patches inside the same minor line and do not alter the reviewed REST surface; the 1.38.17 move was smoke-tested against schema creation, object write and read, vector search and the gRPC port | Collection-qualified `HEAD /v1/objects/{class}/{id}` checks existence; `PUT` replaces an object; `PATCH` partially updates properties; GraphQL object queries expose `_additional.id`; `422` is not exclusively a duplicate signal | RAG ingestion replaces confirmed objects and reconciles stale profile IDs. Memory UUIDs provide deterministic IDs, while a durable Postgres marker reconciles desired active state and all legacy `pgFactId` matches; GraphQL `errors` prevent intent clearance. Focused tests cover validation, replay, shrink/removal, fallback, public state changes, races, supersession, and retention |

### 2.1. Airflow provider-graph evidence

The Linux/Python 3.13 provider graph resolves byte-identically for amd64 and arm64 with Spark 4.1.2.

The 2026-09-27 move to 3.3.2 recompiled the lock against itself. Only `apache-airflow`, `apache-airflow-core`, `apache-airflow-task-sdk`, `boto3` and `botocore` were released.

The overlay's `botocore` cap follows the base. The 3.3.2 image ships `aiobotocore` 3.9.1 (`botocore>=1.43.66,<1.43.76`) with `boto3`/`botocore` 1.43.75. The cap therefore moved from `<1.43.57` to `<1.43.76`, and the lock pins 1.43.75.

A pins-only bump retaining `botocore` 1.43.56 would have failed the Dockerfile's `pip check`.

Without Docker, `pip check` was reproduced from PyPI metadata over the base set. That set used `apache-airflow[<image extras>]==3.3.2` under `constraints-3.3.2`, overlaid with the lock. It reported zero problems on amd64 and arm64, matching the method's result for the shipped 3.3.1 pair.

The final image's real `pip check` and vulnerability gate run in CI's final-image job. A live bring-up on 3.3.2 passed on 2026-11-08; see [initialization evidence](#43-airflow-initialization-evidence) (#1276).

On 2026-10-28 the overlay also began upgrading the base's Snowflake connector. The 3.3.2 constraints ship `snowflake-connector-python` 4.7.3, which requires `filelock<4,>=3.5`.

The overlay now requires Snowflake 4.8.1 (`<5`), which accepts `filelock<5,>=3.12`. Its former `filelock<4` cap therefore became `filelock>=4.2.1,<5`. The lock pins Snowflake 4.8.1 and filelock 4.2.1.

virtualenv 21.14.10 requires `filelock>=3.24.2,<5`. Snowflake provider 6.17.0 requires connector 3.17.0 or later. `test_airflow_snowflake_repair_retains_reviewed_base_compatibility` guards these ranges.

The overlay also moves OpenTelemetry to 1.45.1 and its beta packages to 0.66b1. The base constraints ship `opentelemetry-exporter-prometheus` 0.65b0, which rejects that family. The overlay therefore pins exporter 0.66b1.

These ranges come from PyPI metadata and the `constraints-3.3.2` file, rechecked on 2026-10-29. On 2026-11-08 a no-cache build's `pip check` printed "No broken requirements found", and a live bring-up passed (#1276).

## 3. 2026-08-02 Maintenance Pass

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| JupyterHub Python/PyG stack | PyTorch `2.13.0`, torchvision `0.28.0`, torch-geometric `2.7.0` | torch-geometric ships a pure-Python `py3-none-any` wheel on PyPI and runs on its native fallbacks; the compiled accelerators are unavailable, as `pyg_lib` was published only at `data.pyg.org` — unresolvable since 2026-09-02, its CNAME target carrying no A record — and no scatter/sparse/cluster wheels were built for 2.13 | Torch, torchvision, and the compiled constraints move as one family; the image depends on no third-party wheel index, and unused torchaudio plus every unavailable extension are absent | The Linux/Python 3.13 graph resolves on both audited architectures, its Docker install is CPU-first, and lock/security-floor tests guard the family |
| Docling GPU Torch stack | `pytorch/pytorch:2.13.0-cuda12.6-cudnn9-runtime` at digest `sha256:6acf…f64b4`; Torch `2.13.0`, torchvision `0.28.0` | Official image manifest and matching Python pair resolved on 2026-08-02 | Base image and installed Torch pair must share the same release | Digest pin, provider contract test, and generated env parity guard all surfaces |
| Compiled service runtime graphs | Twelve committed runtime constraints: Backend, Airflow, JupyterHub, Parakeet GPU, Parakeet MLX, bootstrapper fallback, asset-baker, asset-worker, Docling GPU, Docling LightRAG adapter, MCP runtime, and the shared Open WebUI/LiteLLM init closure | Constraint-anchored compilation reproduces every closure for its target Python/platform, including Apple Silicon for Parakeet MLX and arm64 Linux for JupyterHub, asset-worker, and MCP; the shared init closure exactly owns both init Dockerfiles' pinned installs | Ranged direct manifests remain contributor-friendly while runtime installs use exact transitive constraints; the `.txt` suffix keeps transitive pins visible to Dependabot | `scripts.check_runtime_locks` is part of the required audit job and fails on input/lock closure drift |
| Runtime vulnerability audit | `pip-audit==2.10.0` over committed runtime/test constraints and both committed uv projects; `npm audit` over the asset-worker and n8n locks | PyPI and npm advisory results checked on 2026-08-25 | New advisories, registry errors, malformed audit responses, and stale exception entries fail CI; every discovered dependency manifest must appear in the audited inventory | Exceptions are exact and reachability-reviewed; Parakeet MLX retains only two advisories whose fixed wheel is unavailable for its resolved Apple Silicon graph. All other findings either fail the audit or carry their existing code-adjacent review rationale and deadline. The npm findings follow the same contract through `NPM_PROJECT_EXCEPTIONS`, which holds only the n8n Zod entry. The former asset-worker GHSA-vfj7-8cjw-p6xm (braces 3.0.3) entry was retired after GitHub withdrew the advisory on 2026-10-23. Three capped advisories carry time-boxed exceptions; see [Reviewed Deferrals](#5-reviewed-deferrals). |
| cAdvisor isolation boundary | cAdvisor `v0.55.1` | Upstream documents raw-cgroup collection independently from Docker-only discovery and exposes a configurable `--docker` endpoint | Atlas points Docker discovery at a nonexistent socket, disables stored container labels, and mounts only `/sys`; no Docker socket/API proxy, host root, Docker data directory, or disk-device tree is exposed. The container is read-only with all capabilities dropped and `no-new-privileges` | Live smoke proof served cgroup CPU metrics with the restricted mount set; series use cgroup IDs instead of Docker names by design. Contract: [cAdvisor runtime options](https://github.com/google/cadvisor/blob/v0.55.1/docs/runtime_options.md) |
| Channel-tagged container defaults | Chatterbox GPU, TEI arm64, Jenkins LTS JDK 21, Node 22 bookworm-slim | Registry multi-platform indexes inspected on 2026-08-02 | Human-readable channel tags remain operator-overridable, but checked-in defaults bind the inspected immutable index digest | Manifest/Compose/Dockerfile pin tests reject regression to bare channel tags |
| Dependabot deleted-path alerts | 38 alerts for historical `tts-provider/localhost/uv.lock` | Path absent from every live branch; history retained in Git | Deleted manifests must not remain actionable security inventory | All 38 dismissed as `not_used`; active-path alerts remained open for remediation or explicit reachability triage |
| LightRAG async Docling parser | Image `ghcr.io/hkuds/lightrag:v1.5.4`, tag commit `9a45b64c` | Pinned client source and image contract inspected on 2026-08-03 | Client submits multipart field `files` to `POST /v1/convert/file/async`, polls `GET /v1/status/poll/{task_id}`, downloads `GET /v1/result/{task_id}`, and expects a ZIP result | The isolated adapter implements only this public route subset; executable contract tests assert methods, paths, field name, payloads, polling states, and cleanup |

## 4. 2026-08-03 Contract Closure

| Integration point | Exact version inspected | Consumed contract checked | Evidence / disposition |
|---|---:|---|---|
| MinIO client provisioning | `pgsty/mc:RELEASE.2026-09-16T00-00-00Z@sha256:cfc83108…` (Silo client, commit `e952aa7`; was `minio/mc:RELEASE.2025-08-13T08-35-41Z`, local image ID `sha256:5dee113e…63ad1`) | The pinned executable accepts `mc admin policy create TARGET POLICYNAME POLICYFILE`; `mc admin user svcacct add ALIAS ACCOUNT` with `--access-key`, `--secret-key`, and `--policy`; and `svcacct edit ALIAS SERVICE-ACCOUNT` with `--secret-key` and `--policy` | Pinned command help and Silo provisioning probes passed. See [dated client evidence](#41-minio-client-provisioning-evidence), including scoped access, the binary migration and remaining live checks. |
| MinIO-compatible server image | `pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb…` (Silo, commit `2a4d514`, Go 1.27.1; replaces `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z`) | The compose command `server /data --console-address :9001` still works because the image entrypoint maps `server` to `silo server`; the `MINIO_*` environment, `/minio/health/live` (via the image's curl), `/minio/v2/metrics/cluster` with `MINIO_PROMETHEUS_AUTH_TYPE=public`, and the console on 9001 are preserved; the image runs as root like the one it replaces, so existing volumes need no ownership change | Health, metrics and console probes passed on 2026-09-28. See [dated server evidence](#42-minio-server-evidence) for migration context, the false-positive review and unresolved upgrade checks. |
| n8n workflow/package lifecycle | `n8nio/n8n:2.48.7` (2.28.2 CLI paths in the 2026-08-03 row; 2.48.7 re-executed in section 22) | `import:workflow` accepts `--input` and `--activeState=fromJson`; `publish:workflow` accepts `--id`. Community packages are installed with committed `npm ci --omit=dev --ignore-scripts`; Atlas does not consume an n8n package-management REST endpoint. | Executed both pinned-image CLI help paths on 2026-08-03. Existing executable workflow/import tests and the committed npm lock guard the remaining contract. |
| Airflow initialization | `apache/airflow:3.3.2`, local image ID `sha256:8f238ca7…3b494` (linux/arm64) | `airflow db migrate`. The admin user is created and re-synced by `services/airflow/init/scripts/airflow_admin.py`, which calls the FAB provider's `user_command.users_create` and `user_reset_password` in-process, so no password is on argv. Connections are imported by `airflow_connection.py` with `airflow connections delete` and `airflow connections import --overwrite <file>`. | Re-verified on 2026-11-12 against a live `data-eng` stack: the admin row carries the `.env` password with the Admin role, the connections store the `.env` values, and `example_etl_with_llm` reached the landing bucket through `minio_default`. The 2026-11-08 `users create` and `connections add` evidence below describes the old init and is kept for history. See [initialization evidence](#43-airflow-initialization-evidence) (#1276, #1381). |
| Alpine init-image base | `alpine:3.24.4@sha256:f59abe65…c71bf` (index digest; `musl` 1.2.6-r3, `zlib` 1.3.2-r1) | Every exact `apk add` pin in the init images resolves in this base: `ca-certificates` 20261020-r0, `curl` 8.23.0-r0, `python3` 3.14.10-r1, `jq` 1.8.2-r0, `bash` 5.3.9-r1, `openssl` 3.5.9-r0, `gettext` 1.0-r0, `wget` 1.25.0-r3 | On 2026-11-09 the comfyui-init, hermes-init, lightrag-init, ollama-pull, openclaw-init, label-studio-init, langfuse-init and mlflow-init images built on it. Pinned Trivy 0.74.0 reported no HIGH or CRITICAL OS finding in any of them, including CVE-2026-107891 (`musl`) and CVE-2026-85091 (`zlib`) from 3.24.2 (#1962). |
| Docker Compose orchestration | Docker Compose `5.1.4`; Atlas minimum `2.23.1` (was `2.20.3` until 2026-11-01) | Top-level `include`, `--env-file`, `config -q`, JSON config/ps output, explicit service targets, and project-name placement | The local exact binary passed the full Compose configuration/source-permutation suite; startup separately enforces the documented minimum before using modular includes. |
| Local Deep Researcher aiohttp floor | `aiohttp>=3.14.3` in the combined lock inputs; resolved `3.14.3` | CVE-2026-59881 and CVE-2026-69243 are fixed in 3.14.2; CVE-2026-69244 is fixed in 3.14.3 | The lock refresher now applies the patched floor over the manifest-pinned upstream graph; byte-equivalence and the repository-wide vulnerability audit guard it. |

### 4.1. MinIO client provisioning evidence

Each pinned-image `--help` path was executed on 2026-08-03. These match the commands and flags in `services/minio/init/scripts/init-minio.sh`; the previous migration deferral is closed.

On 2026-09-28, `quay.io/minio` had stopped serving anonymous pulls. The `mc` binary from the pinned `pgsty/mc` image ran the unmodified `init-minio.sh` twice against the pinned Silo server. The first run created 16 buckets and 13 scoped service accounts. The second edited all 13 accounts.

boto3 probes confirmed read-write, read-only and no-access scoping, multipart upload and presigned GET. A scoped key's attempt to create a service account was denied, covering the CVE-2025-62506 escalation. The live stack checks passed on 2026-11-08 (#1278).

On 2026-10-03 (#1288), `services/jenkins` and `services/backup` moved onto this same digest-pinned image. They left the abandoned official `minio/mc` release `RELEASE.2025-08-13T08-35-41Z` (Go 1.24.6, 43 HIGH/CRITICAL scanner rows).

Both Dockerfiles use `COPY --from` to copy statically linked `/usr/bin/mc` to `/usr/local/bin/mc`. The binary reports `mc version RELEASE.2026-09-16T00-00-00Z` and Go 1.27.1. SHA-256 values re-extracted from the index that day are `1e745aaf…86b8` on amd64 and `d89a6daf…48fa` on arm64.

The backup entrypoint checks the committed checksums and version at every start. It fails closed instead of downloading. A Trivy 0.74.0 scan of that binary on both platforms reports no HIGH/CRITICAL finding.

### 4.2. MinIO server evidence

MinIO stopped publishing community images in October 2025 and deleted `minio/minio` from Docker Hub on 2026-09-11. Since 2026-09-24, `quay.io/minio` has returned 401 to anonymous pulls. Silo was adopted as an actively released AGPL fork with dated multi-arch tags.

On 2026-09-28, probes used the binary extracted from the pinned amd64 image. Health, metrics and console each returned 200.

The CVE-2026-39414 fix is present in source: `internal/s3select/csv/reader.go` bounds each record at 1 MiB. Trivy's pseudo-version match is therefore excepted as a false positive.

On 2026-11-08 Silo started on a volume written by `RELEASE.2025-09-07`, and the seeded object and service account survived. After S3 traffic the Grafana panels `minio_s3_requests_total` and `minio_cluster_usage_total_bytes` populated. A fresh bring-up, the S3A and lakehouse smokes, and a local-mode backup and restore also passed (#1278). The long-term server choice remains tracked in #1277.

### 4.3. Airflow initialization evidence

All three pinned-image CLI help paths were re-executed against `3.3.1` on 2026-08-22. These were `db migrate`, `users create` with all six flags, and `connections add` with all eight typed flags. Init-script tests guard the exact flags and idempotent delete/add sequence.

The 2026-09-27 move to `apache/airflow:3.3.2` was checked statically, without Docker. Its platform manifests were `linux/amd64@sha256:6f870ad5…` and `linux/arm64@sha256:0a56a5db…`.

A wheel diff of `apache-airflow-core` 3.3.1 against 3.3.2 leaves the relevant arguments in `cli/cli_config.py` unchanged. Those arguments belong to `db migrate` and `connections add`. The `users create` command comes from `apache-airflow-providers-fab==3.7.3`, which the lock keeps.

The `connections add` command now prints success as a table with redacted extras instead of a URI. The init script does not parse that output.

On 2026-11-08 the help paths were re-executed against the pulled 3.3.2 image (#1276). It has local image ID `sha256:8f238ca7…3b494` on linux/arm64, and its index lists the same platform manifests as above. `db migrate`, `users create` with all six flags, and `connections add` with all eight typed flags are present.

On 2026-11-12 the init script changed (#1381): `airflow_admin.py` and `airflow_connection.py` replace the `users create` and `connections add` calls. A `data-eng` stack on this branch ran `airflow-init` cleanly. The admin row verified against the `.env` password, the Admin role was present, the stored connection values matched `.env`, and the example DAG's MinIO task succeeded.

The same day, a disposable stack built the Airflow image on that base without cache. Its `pip check` reported "No broken requirements found". `airflow-init` ran `db migrate`, created the admin user and seeded the five connections. A second run re-synced the admin password and re-added every connection, which `connections add` allows only after the delete. `/api/v2/monitor/health` returned 200 with healthy metadatabase, scheduler and DAG processor. `lakehouse_spark_submit_smoke` and `example_etl_with_llm` both completed with state `success`.

## 5. Reviewed Deferrals

| Gap | Interim controls | Owner | Review trigger / deadline |
|---|---|---|---|
| Four final-image builds outside the hosted scanner profile | Manifest bases and remote Dockerfile/base-index drift are checked; excluded final contents are not fully scanned. See [residual coverage](#51-final-image-exclusion-coverage). | Atlas maintainers | Resolve or renew the residual final-image gap with evidence by 2027-02-12 (renewed 2026-11-14 for 90 days from 2026-11-30, owner-approved; earlier renewed from 2026-09-15 with both rationales re-verified; the `expired_at` dates in `.container-scan-exclusions.yml` and CHANGELOG.md record it); re-open immediately if any excluded build becomes a published Atlas artifact. |
| Parakeet GPU fsspec and Hydra advisories under time-boxed exceptions | `services/parakeet/provider/gpu/requirements-locked.txt` keeps fsspec 2024.12.0 (PYSEC-2026-4217 / GHSA-27vj-qcqg-25rc, fixed in 2026.6.0). It also keeps hydra-core 1.3.2 (PYSEC-2026-4221 / GHSA-c3wx-c55w-pxjq, fixed in 1.3.6). NeMo 3.0.0 admits only `hydra-core>1.3,<=1.3.2` and `lightning>2.2.1,<=2.4.0`. Lightning 2.4.0 requires `fsspec[http]<2026.0`. Atlas restores only the operator-selected `PARAKEET_MODEL`; this is not a reachability review. The owner approved time-boxed exceptions for both IDs on 2026-10-29, in `scripts/audit_runtime_locks.py`. | Atlas maintainers | Re-review by 2027-02-09 (renewed 2026-11-12 for 90 days), and at each NeMo release; 3.0.0 was the newest on 2026-10-29. Remove the exceptions on an upstream cap lift. |
| n8n community-node Zod advisory under a time-boxed exception | `services/n8n/init/config` resolves zod 3.25.76 (GHSA-mrhx-c7xc-m3w5, fixed in 4.6.6). `n8n-workflow` 2.48.3 resolves `@n8n/expression-runtime` 0.39.3, which pins zod 3.25.76 exactly, as 0.38.0 under `n8n-workflow` 2.47.0 did (rechecked 2026-11-22). Reachability through the community nodes is not proven. The owner approved a time-boxed exception on 2026-10-29. | Atlas maintainers | Re-review by 2027-02-09, and at each `n8n-workflow` bump. Remove the exception on an upstream move to Zod 4. |
| CPython 3.10 end of life in shipped Spark, Ray and Zeppelin runtimes | PEP 619 ended Python 3.10 security support in October 2026. Both Zeppelin `build/pyspark-env/` locks pin `python-3.10.21` because that env must match the Python 3.10 executors of `apache/spark:4.1.2`. `rayproject/ray:2.56.0` also runs Python 3.10. The `apache/zeppelin:0.12.1` base also carries Ubuntu 20.04 and stock conda Python 3.7 and 3.9 envs. The final-image scan reports CVEs, not interpreter end of life. No image changed on 2026-11-01. | Atlas maintainers; owner decision pending (the Ray side relates to #1374) | Exit: move Spark and Ray to Python 3.11+ bases, then re-solve the Zeppelin `pyspark` locks for the matching minor. Review at each Spark, Ray or Zeppelin base bump. |

### 5.1. Final-image exclusion coverage

The two CUDA final builds still have their exact manifest base images scanned.

For the two remote LLM Graph Builder contexts, the nightly watch fetches the commit-pinned Dockerfiles. It compares every literal base-image index digest with the reviewed baseline in `.container-scan-exclusions.yml`.

This detects upstream Dockerfile/base-index drift but does not build or vulnerability-scan the excluded final images. With unchanged bases, it does not cover apt, pip, yarn or downloaded model contents.

The exclusion ledger is machine-checked against the complete Compose build inventory. It requires substantive reasons and expires every exclusion.

## 6. 2026-08-05 Maintenance Pass

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| TEI reranker amd64 + GPU image pins | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9@sha256:ad950d30…ea07` (amd64 CPU) and `…:1.9@sha256:536efce2…df91` (GPU) | Single-platform `linux/amd64` indexes resolved via `docker buildx imagetools inspect` on 2026-08-05; the `cpu-1.9` family tag and bare `1.9` minor tag are republished per patch | Checked-in defaults bind the immutable index digest so two pulls days apart resolve to identical content; mirrors the existing arm64 `cpu-arm64-latest@sha256:35c50…` pin | Pinned in `services/tei-reranker/service.yml`, `service_config.py` fallbacks, and the compose inline fallback; `.env.example` regenerated; the channel-tag pin test now covers both TEI architectures and the fragment-equivalence baseline normalizes the pinned forms |
| Jenkins compose build-arg fallback | `jenkins/jenkins:lts-jdk21@sha256:c1e4c349…16d0` (Jenkins 2.568.3; re-resolved from the `lts-jdk21` channel on 2026-09-17, previously `f4f65e6c…d1d02` = 2.568.1) | Effective default was already digest-pinned via the Dockerfile ARG, service.yml, and `.env.example`; only the compose inline `${JENKINS_IMAGE:-…}` fallback still read the bare LTS channel tag | An operator running `docker compose up` without the bootstrapper-generated `.env` previously resolved the bare moving tag; the fallback now matches the pinned effective default | Mirrored into `services/jenkins/compose.yml`; rendered config unchanged because `JENKINS_IMAGE` is always supplied |
| Parakeet startup supervised restart | `services/parakeet/provider/startup.py` `ModelStartup` | Reviewed 2026-08-05 against the documented "fatal timeouts return 504 before the provider process exits for supervised restart" contract | The generic-exception branch previously left the container alive returning 503 on transient loader failures; it now mirrors the deadline branch and terminates for supervised restart | Pinned by `test_startup_loader_failure_is_generic_unhealthy_and_terminates`; see the [CHANGELOG](../CHANGELOG.md) entry "Fixed — 2026-08-05 — Provider startup, bounded-subprocess, and supply-chain hardening" |
| Bounded-subprocess parent-loss + deadline | `bootstrapper/core/process_runner.py` | Reviewed 2026-08-05; the reaper now bounds its post-SIGKILL wait and the cleanup contextmanager intercepts SIGHUP | A D-state leader can no longer wedge the deadline path, and `start_new_session=True` children are reaped on terminal close / SSH disconnect | Full `test_process_runner.py` + `test_bounded_subprocess.py` (153 tests) pass; see the [CHANGELOG](../CHANGELOG.md) entry "Fixed — 2026-08-05 — Provider startup, bounded-subprocess, and supply-chain hardening" |

## 7. 2026-08-25 Maintenance Pass

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| asyncpg pool lifecycle | Backend lock's `asyncpg==0.31.0` | Public pool methods inspected on 2026-08-25 | Pool reuse checks `Pool.is_closing()` rather than the private `_closed` field; readiness independently validates the same pool bounds before its dedicated probe; shutdown awaits `Pool.close()` with a finite deadline and calls `Pool.terminate()` if the deadline expires | Backend unit tests cover invalid size bounds, public pool state, readiness validation, graceful close, and forced termination of a stuck close |
| Ray job submission and cancellation | Backend runtime's `ray[client]==2.56.0` | Exact pinned SDK surface inspected on 2026-08-25 | `JobSubmissionClient.submit_job(submission_id=…)` provides the stable caller-owned ID; duplicate submissions surface `RuntimeError`; `get_job_info` reconciles ambiguous submission outcomes; and `stop_job` supplies cooperative cancellation | Backend contract tests pin caller-supplied submission IDs, duplicate reconciliation, status lookup, and cancellation without requiring a live Ray cluster |
| ComfyUI targeted job cancellation | Upstream core `v0.27.0` via `COMFYUI_REF` / `COMFYUI_MPS_REF` | The pinned HTTP surface exposes `POST /api/jobs/{job_id}/cancel` with an exact `{\"cancelled\": bool}` response and records `execution_interrupted` in job history | Atlas must cancel by percent-encoded job id atomically, never infer ownership from a queue snapshot or fall back to bodyless global `/interrupt`; unsupported older localhost instances fail closed | Both ComfyUI adapters validate status, JSON shape, and the boolean type. Provider, compatibility-route, gateway-redelivery, identity, and interrupted-history tests run without a live stack. |
| Celery OpenTelemetry propagation | `opentelemetry-instrumentation-celery==0.65b0` | The exact backend runtime graph compiles for the target Linux/Python 3.12 environment | FastAPI and Celery workers share the configured OTLP endpoint, while each process owns its tracer provider; producer instrumentation shares the FastAPI provider inside the web process, and each worker process initializes its own instrumentation after fork | Backend tests verify producer instrumentation with the web provider, worker signal initialization, per-process service naming, and Compose environment forwarding; the exact runtime-lock gate prevents dependency drift |
| GitHub Actions cache action | `actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9` (`v6.1.0`) | The v6.1.0 release tag was resolved to this commit through the GitHub API on 2026-09-16 (previously `v4` at `0057852b…`, resolved 2026-08-25); v5+ runs on the Node.js 24 runtime, which the hosted `ubuntu-24.04` runners provide | Workflow execution is bound to the reviewed commit rather than a mutable major tag | Repository tests scan every workflow action reference and reject mutable pins |
| GitHub repository security settings | Secret scanning, push protection, and Dependabot security updates enabled | Live repository settings re-read through the GitHub API on 2026-08-25 | New committed secrets are blocked and supported dependency advisories receive automated security-update PRs | Enabled all three controls. Five additional alerts whose only manifest path was the retired `tts-provider/localhost/uv.lock` were dismissed individually as `not_used`; 12 active-path alerts remain governed by the repository's exact, stale-checked reachability exceptions. |
| Container image vulnerability gate | Trivy `v0.74.0`; `aquasecurity/setup-trivy` pinned to commit `81e51434…f567` | Official release and immutable action commit resolved through the GitHub API on 2026-08-25 | Inputs are attempted on both declared scan platforms; failures remain visible. See [detailed evidence](#71-container-image-vulnerability-gate-evidence) for inventory counts, gating, exceptions and resource limits. | Inventory, source contracts and exclusions are executable. The [detailed evidence](#71-container-image-vulnerability-gate-evidence) names their owners and retains the distinction from registry discovery. |
| LLM Graph Builder remote base images | Upstream source commit `4a412f4688cf4096976045c019edc0a7f6ddcb6b`; `python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b`; `node:20@sha256:8f693eaa7e0a8e71560c9a82b55fd54c2ae920a2ba5d2cde28bac7d1c01c9ba5`; `nginx:alpine@sha256:fb4658584bd9a916ffb6f6e365f97c25cfdb2923801f551ea08356e695f14ff7` | The pinned Dockerfiles and all index moves were reviewed. See [dated base-image evidence](#72-llm-graph-builder-base-image-evidence) for content changes, unchanged platforms and the package-closure limitation. | The reviewed baseline must exactly equal the three literal bases discovered from the two pinned Dockerfiles; every nightly check resolves their current multi-platform index digests and fails closed on added, removed, or changed bases. | `.container-scan-exclusions.yml` records only the reviewed registry indexes. `bootstrapper/tests/test_container_security.py` pins the reviewed fixture and ledger evidence, while `scripts.upstream_drift_watch` owns bounded live source and registry verification. |

### 7.1. Container image vulnerability gate evidence

The reviewed inventory contains:

- All 72 manifest-owned image defaults.
- 2 Dockerfile-only build inputs.
- 3 digest-qualified bases from commit-pinned remote build contexts.

Inputs are attempted on both `linux/amd64` and `linux/arm64` unless the manifest declares an explicit single architecture. This is currently producing 147 isolated image-platform scans. Scheduled scan failures expose unsupported registry variants instead of silently omitting them.

The required Build-validation job fetches and parses the pinned remote Dockerfiles, verifies their reviewed base digests, and scans those bases when remote inputs change. It builds no local Dockerfile. The Final-image scan job builds and scans every hosted-runner-supported local runtime image, then removes it. That job reports on every run but is not a required check (#1002).

Local final-image scans fail every fixable High/Critical finding. Vendor-unfixed findings remain visible in the scheduled complete scan and automatically fail the local-image scans when a fix appears.

A fixable finding may be deferred only by an exact package/path exception. The exception needs a substantive reason and a review horizon of at most 90 days.

The executable inventory, remote-source contract and reviewed final-build exclusions are provided by:

- `.github/workflows/container-security.yml` and `.github/workflows/services-lint.yml`.
- `.trivyignore.yaml` and `.container-scan-exclusions.yml`.
- `scripts.container_security` and `scripts.upstream_drift_watch`.

The former multi-architecture deferral is closed by explicit dual-platform attempts, not registry discovery. The scheduled matrix is capped at four concurrent jobs. The Final-image scan and the scheduled local-image scan reclaim each loaded image and its BuildKit cache before proceeding, bounding runner disk use.

### 7.2. LLM Graph Builder base-image evidence

The pinned column above retains the reviewed source commit and three base-image indexes. The following record preserves each dated review.

#### 7.2.1. 2026-09-02 — Python index and Nginx content

Both commit-pinned upstream Dockerfiles were fetched from `neo4j-labs/llm-graph-builder`. Their literal `FROM` instructions were re-read on 2026-09-02 UTC.

Docker Hub index inspection initially found only the Python channel had moved. Its index identified Python `3.12.14-slim-trixie` from `docker-library/python@688a0b86bb44289df16a363e9f41d90514c1a5f9` on the hosted scan architectures. The reviewed platform manifests were:

- `linux/amd64@sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79`.
- `linux/arm64/v8@sha256:3949e4271b0a3ff82afac7306764c313dcc8edeeb89c0376a3c2ac6007c66b1d`.

The Python index move re-published only attestation manifests. Both reviewed platform manifests re-resolved unchanged, so scanned image content was identical. The recorded index advanced from `sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc`.

Nginx moved the same day with genuinely new content: both platform manifests changed. A pinned Trivy scan of outgoing `sha256:db35bfc6...` reported four fixed HIGH findings:

- CVE-2026-14456 in libcrypto3/libssl3.
- CVE-2026-66046 and CVE-2026-76641 in libexpat.

Incoming `sha256:a9ae6f6d...`, on Alpine 3.24.1, reported none. The baseline advanced because the new image strictly reduced exposure.

Nginx re-published its index later that day, from `sha256:a9ae6f6d...` to `sha256:72ba65eb...`. Both platform manifests re-resolved unchanged. This second move was attestation-only and carried no new content to review.

#### 7.2.2. 2026-09-16 — Nginx update and attestation

The Nginx index moved from `sha256:72ba65eb...` to `sha256:17ad11d8...` with genuinely new content. Both platform manifests changed:

- `linux/amd64`: `sha256:bd43d3d8...` to `sha256:2aacff5f...`.
- `linux/arm64/v8`: `sha256:ac1c5b25...` to `sha256:7e311142...`.

This carried nginx 1.31.5 to 1.31.6 on the same Alpine 3.24.1. A pinned Trivy 0.74.0 scan of the outgoing amd64 manifest reported seven fixed HIGH findings in util-linux's `libuuid` 2.42.1-r0:

- CVE-2026-53612, CVE-2026-53613 and CVE-2026-53614.
- CVE-2026-76642, CVE-2026-78408, CVE-2026-78409 and CVE-2026-78410.

The incoming manifest reported none. The baseline advanced because the replacement strictly reduced exposure.

Later that day, Nginx re-published the index from `sha256:17ad11d8...` to `sha256:c8497b18...`. The reviewed `linux/amd64@sha256:2aacff5f...` and `linux/arm64/v8@sha256:7e311142...` manifests re-resolved unchanged. That move was attestation-only and carried no new content to review.

#### 7.2.3. 2026-09-17 and 2026-09-18 — Alpine point release

On 2026-09-17 UTC, the Nginx index moved from `sha256:c8497b18...` to `sha256:3a7edb15...` with genuinely new content:

- `linux/amd64`: `sha256:2aacff5f...` to `sha256:7396be67...`.
- `linux/arm64/v8`: `sha256:7e311142...` to `sha256:03cceb15...`.

The same nginx 1.31.6 moved from Alpine 3.24.1 to Alpine 3.24.2. Pinned Trivy 0.74.0 scans of outgoing and incoming amd64 manifests both reported zero fixed HIGH or CRITICAL findings. The baseline advanced on a point release that neither added nor removed exposure.

On 2026-09-18 UTC, the index moved from `sha256:3a7edb15...` to `sha256:62ff2089...`. The reviewed `linux/amd64@sha256:7396be67...` and `linux/arm64/v8@sha256:03cceb15...` manifests re-resolved unchanged. That move was attestation-only and carried no new content to review.

#### 7.2.4. 2026-09-19 — Python Debian update and repeated indexes

The Python index moved from `sha256:78387bc3...` to `sha256:23b5dc88...` with genuinely new content:

- `linux/amd64`: `sha256:2fe5997d...` to `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`.
- `linux/arm64/v8`: `sha256:3949e427...` to `sha256:950206c37262dd86c55659797f6ee418fee30535072f65a82ed470d985f5cda5`.

The same Python 3.12.14 moved from Debian 13.6 to Debian 13.7. A pinned Trivy 0.74.0 scan of the outgoing amd64 manifest reported thirteen fixed HIGH findings:

- Seven in `perl-base` 5.40.1-6: CVE-2026-8376, CVE-2026-13221, CVE-2026-42496, CVE-2026-42497, CVE-2026-48962, CVE-2026-57432 and CVE-2026-57433.
- Three in `libpcre2-8-0` 10.46-1~deb13u1: CVE-2026-86145, CVE-2026-89157 and CVE-2026-89161.
- Two in `libsqlite3-0` 3.46.1-7+deb13u1: CVE-2026-11822 and CVE-2026-11824.
- One in `gzip` 1.13-1: CVE-2026-41992.

The incoming manifest reported none. The baseline advanced because the replacement strictly reduced exposure.

The index moved again that day, from `sha256:23b5dc88...` to `sha256:b699c2a5...`. Both reviewed platform manifests re-resolved unchanged, making that move attestation-only.

A third move, from `sha256:b699c2a5...` to `sha256:2f17fc04...`, again left both reviewed platform manifests unchanged. Three index moves in one day without content changes inform whether this baseline should pin indexes or platform manifests.

#### 7.2.5. 2026-09-23 — package-closure comparison and its limit

The Nginx index moved from `sha256:62ff2089...` to `sha256:1ed1b0e1...` with genuinely new content:

- `linux/amd64`: `sha256:7396be67...` to `sha256:0530961f...`.
- `linux/arm64/v8`: `sha256:03cceb15...` to `sha256:84dd96a0...`.

Seven of eight layer digests differed; image creation dates changed from 2026-09-17 to 2026-09-22. The usual pinned-Trivy comparison was unavailable because the vulnerability database was unreachable from this host. Both `mirror.gcr.io/aquasec/trivy-db:2` and `ghcr.io/aquasecurity/trivy-db:2` repeatedly returned `context deadline exceeded`.

The outgoing and incoming amd64 manifests were therefore compared by installed-package closure. Both reported Alpine `3.24.2`, nginx `1.31.6` and a **byte-identical** `apk info -v` inventory. The full package list had zero diff.

Trivy's Alpine OS-package analysis derives entirely from that installed-package set. Identical inventories therefore imply identical OS-package findings, despite the changed layers.

This proof substitutes package-closure equivalence for a CVE-count delta and does not cover non-package file content. Re-run the pinned scan at the next reviewed move when the database is reachable.

#### 7.2.6. 2026-09-27 — unscanned-architecture carry-forwards

Both channels moved without changing any scanned content:

- Python: `sha256:2f17fc04...` to `sha256:f77ac9e4...`.
- Nginx: `sha256:1ed1b0e1...` to `sha256:df221db8...`.

Each index rebuilt only `linux/riscv64` and its attestation manifest:

- Python: `sha256:89e07fe8...` to `sha256:a3294718...`, created 2026-09-25.
- Nginx: `sha256:6d10d98c...` on 1.31.5 to `sha256:bc775025...` on 1.31.6, created 2026-09-26.

The reviewed, scanned manifests re-resolved unchanged:

- Python: `linux/amd64@sha256:44ff437b...` and `linux/arm64/v8@sha256:950206c3...`.
- Nginx: `linux/amd64@sha256:0530961f...` and `linux/arm64/v8@sha256:84dd96a0...`.

Atlas neither builds nor scans `linux/riscv64`, so neither move carried new content to review. Both baselines advanced as carry-forwards. The pinned column had still shown the pre-2026-09-23 Nginx index `sha256:62ff2089...`; it was corrected to the current one.

#### 7.2.7. 2026-10-02 — Python security reduction and later index

The Python index moved from `sha256:f77ac9e4...` to `sha256:eeb8088e...` with genuinely new content:

- `linux/amd64`: `sha256:44ff437b...` to `sha256:6b1f85a08c199d29d5b6d71ab9c27bd5b3b393492e01216a15758ff69c4be8b8`.
- `linux/arm64/v8`: `sha256:950206c3...` to `sha256:18d23907c5d7ef2ca1b0ea8b4a73fe4a6ae47114c257a1afed8bc66c3886f8cb`.

Python 3.12.14 moved to `3.12.15-slim-trixie` from `docker-library/python@2a3b794c223ab067d122719541cdd54a068732a5`. The image stayed on Debian 13.7 and was created on 2026-10-01.

Pinned Trivy 0.74.0 scans used the database of 2026-10-01 19:00 UTC. The outgoing amd64 manifest reported seven fixed HIGH findings:

- CVE-2026-75804 and CVE-2026-84782 in OpenSSL 3.5.7-1~deb13u2 (`libssl3t64`, `openssl`, `openssl-provider-legacy`).
- CVE-2026-103111 in `libpcre2-8-0` 10.46-1~deb13u2.

The incoming manifest used OpenSSL 3.5.7-1~deb13u3. It retained only CVE-2026-103111 in unchanged `libpcre2-8-0`. The baseline advanced because the replacement strictly reduced exposure.

Later that day, the index moved from `sha256:eeb8088e...` to `sha256:dddfd7e0...` without changing scanned content. Only `linux/ppc64le` and its attestation manifest were rebuilt: `sha256:32e353a2...` on 3.12.14 became `sha256:5ff0e967...` on 3.12.15, created 2026-10-02.

The reviewed `linux/amd64@sha256:6b1f85a0...` and `linux/arm64/v8@sha256:18d23907...` manifests re-resolved unchanged. Atlas neither builds nor scans `linux/ppc64le`, so the baseline advanced as a carry-forward.

#### 7.2.8. 2026-10-04 — riscv64-only index

The Python index moved from `sha256:dddfd7e0...` to `sha256:02108f5d...` without changing scanned content. Only `linux/riscv64` and its attestation manifest were rebuilt. Its manifest changed from `sha256:a3294718...` to `sha256:7fc49aec...` on 3.12.15, created 2026-10-04.

The reviewed `linux/amd64@sha256:6b1f85a0...` and `linux/arm64/v8@sha256:18d23907...` manifests re-resolved unchanged. Atlas neither builds nor scans `linux/riscv64`, so the baseline advanced as a carry-forward.

#### 7.2.9. 2026-10-30 — Nginx and Python content updates

The Nginx index moved from `sha256:df221db8...` to `sha256:785835ea...` with genuinely new content:

- `linux/amd64`: `sha256:0530961f...` to `sha256:2db7d28517d1fba17c2da3210395f7be11a11b419246ad0da2edf782af72f367`.
- `linux/arm64/v8`: `sha256:84dd96a0...` to `sha256:41c8801e327f9c60c47aa700390ea5cf1862a6852bd27d103ba2cac41a7d6ec7`.

This carried nginx 1.31.6 to 1.31.8 and Alpine 3.24.2 to 3.24.3; the image was created on 2026-10-21. Pinned Trivy 0.74.0 scans used the database of 2026-10-30 01:01 UTC.

The outgoing amd64 manifest reported eight fixed HIGH or CRITICAL findings. The incoming amd64 and arm64 manifests each reported three of them:

- CVE-2026-111497 (CRITICAL) in `brotli-libs` 1.2.0-r1.
- CVE-2026-92392 in `curl` and `libcurl` 8.22.0-r0.

The update cleared CVE-2026-77214 and CVE-2026-93990 in `libexpat`, CVE-2026-88806 in `libx11`, CVE-2026-103111 in `pcre2` and CVE-2026-4775 in `tiff`. The baseline advanced because the replacement strictly reduced exposure.

The Python index moved from `sha256:02108f5d...` to `sha256:aa943ce4...` with genuinely new content:

- `linux/amd64`: `sha256:6b1f85a0...` to `sha256:1148dbb014615300fa7e713b2f94f550c0d1c69780c0805f84f093ceee34dd0b`.
- `linux/arm64/v8`: `sha256:18d23907...` to `sha256:b224e2fe306f9d303251ada22961635e5b52a69c0414a0050611be31c59f5f5b`.

Python 3.12.15 moved to 3.12.16 from `docker-library/python@3d7e95597e56a415d9e4abc0f15877e005d2cb2e`. The image stayed on Debian 13.7 and was created on 2026-10-28.

The outgoing amd64 manifest reported one fixed HIGH finding, CVE-2026-103111 in `libpcre2-8-0` 10.46-1~deb13u2. The incoming amd64 and arm64 manifests reported none. Unfixed Debian findings stayed at 44. The baseline advanced because the replacement strictly reduced exposure.


#### 7.2.10. 2026-11-08 — Nginx and Python rebuilds

The Nginx index moved from `sha256:785835ea...` to `sha256:713fc2eb...` with new content:

- `linux/amd64`: `sha256:2db7d285...` to `sha256:697b0b156c70b91130853fd0675c6be36665939d5f93dce2686be04b4c3f98a8`.
- `linux/arm64/v8`: `sha256:41c8801e...` to `sha256:5e3f1baf6e6505e8bc2b7ba50c790f94bb189b5e96c0ab2298fc745081c519a3`.

nginx stayed at 1.31.8 from `nginx/docker-nginx@6fabb4c960a02a1e576d2789974c45f4cd2aa854`. Alpine moved from 3.24.3 to 3.24.4, and the image was created on 2026-11-05. Pinned Trivy 0.74.0 scans used the database of 2026-11-07 18:32 UTC.

The outgoing amd64 and arm64 manifests each reported seven fixed HIGH or CRITICAL findings: CVE-2026-111497 in `brotli-libs`, four `libexpat` CVEs (CVE-2026-113351, -115506, -117610, -118370) and CVE-2026-92392 in `curl` and `libcurl`. The incoming manifests reported none. The baseline advanced because the replacement strictly reduced exposure.

The Python index moved from `sha256:aa943ce4...` to `sha256:4840d514...` with new content:

- `linux/amd64`: `sha256:1148dbb0...` to `sha256:05a994d2344eb364cc186910f4fc8786a38215f39f00d0acc25bff33a08f4177`.
- `linux/arm64/v8`: `sha256:b224e2fe...` to `sha256:c61cffc3bacb94ed026ff8b45d5114b7693dd0420baca0a1ace0e3d0ce9035fd`.

Python stayed at 3.12.16 from `docker-library/python@3d7e95597e56a415d9e4abc0f15877e005d2cb2e` on Debian 13.7, rebuilt on 2026-11-05. The outgoing manifests reported four fixed HIGH findings; the incoming manifests reported two. The update cleared CVE-2026-109518 in `apt` and `libapt-pkg7.0`; CVE-2026-76844 and CVE-2026-85091 in `zlib1g` remain. Unfixed Debian findings stayed at 47. The baseline advanced because the replacement strictly reduced exposure.

#### 7.2.11. 2026-11-08 — riscv64-only index

The Python index moved from `sha256:4840d514...` to `sha256:5e2bdb1b...` without changing scanned content (#988, reopened by the drift watch at 2026-11-08T10:16Z). Only `linux/riscv64` and its attestation manifest were rebuilt: the manifest changed from `sha256:97472c87b6d7...` to `sha256:78d3d1c609af...`, and the attestation from `sha256:d6b999ad4940...` to `sha256:23d38364aabe...`.

The reviewed `linux/amd64@sha256:05a994d2...` and `linux/arm64/v8@sha256:c61cffc3...` manifests re-resolved unchanged (Python 3.12.16, created 2026-11-05). Atlas neither builds nor scans `linux/riscv64`, so the baseline advanced as a carry-forward, as in §7.2.8.

#### 7.2.12. 2026-11-21 — Nginx rebuild

The Nginx index moved from `sha256:713fc2eb...` to `sha256:fb465858...` with new content:

- `linux/amd64`: `sha256:697b0b15...` to `sha256:e68702efeaad0be9ced2b4d1cedb62cd15f5d51865d913955a883f653cd9e117`.
- `linux/arm64/v8`: `sha256:5e3f1baf...` to `sha256:d9c353d322c3c8bf7247f2c432e5dedc82792bd6c361589cf6b4b9aef5eae29c`.

nginx moved from 1.31.8 to 1.31.9. Alpine stayed at 3.24.4. Pinned Trivy 0.74.0 scanned the amd64 and arm64 platform manifests for HIGH and CRITICAL vulnerabilities on 2026-11-21.

The outgoing amd64 manifest reported four fixed HIGH findings: CVE-2026-86144 in `libxml2` 2.13.9-r2 (fixed in 2.13.9-r3), and CVE-2026-125350, CVE-2026-87636 and CVE-2026-9947 in `libxslt` 1.1.43-r3 (fixed in 1.1.43-r4). The incoming amd64 and arm64 manifests report one: CVE-2026-86144. The libxslt findings are gone, and the libxml2 finding carries over unchanged. The outgoing arm64 manifest was not scanned.

The baseline advances because the replacement removes three fixed HIGH findings and adds none. No exception is added. CVE-2026-86144 stays a fixable HIGH in the base image. The remote-context final-image scan is excluded in `.container-scan-exclusions.yml`, so this finding is not gate-enforced today. It clears when upstream rebuilds with libxml2 2.13.9-r3. The next digest move needs its own review.

## 8. 2026-09-20 TrueForge Adoption (#1159)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| TrueForge agent-runtime image | `tfy.jfrog.io/tfy-images/trueforge:0.2.0-8b1d98e` (index digest `sha256:db9de64e1ff354a858dc646f23cf16c70e300c457acb86527e4a0b1cd556f573`, single-platform `linux/amd64` OCI image manifest) | `docker buildx imagetools inspect` on 2026-09-20; upstream GitHub releases re-checked the same day — `@truefoundry/trueforge@0.2.0` (2026-09-18) is the newest release, so the pin is current at adoption | The adoption review found 25 unique HIGH findings and no CRITICAL findings. See [adoption evidence](#81-trueforge-adoption-evidence) for platform, reachability and expiry qualifications. | `services/trueforge/service.yml` (pin + platform), `.trivyignore.yaml` (25 purl-scoped entries with per-cluster reachability statements), `scripts.container_security` schema/expiry/duplicate gates, and the required "Scan changed manifest-owned remote images" CI step. Controller and init containers reuse this same image (no additional remote contract). |


### 8.1. TrueForge adoption evidence

Anonymous pull from `tfy.jfrog.io` succeeds without credentials. The tag embeds the build hash, making it effectively immutable. The recorded digest is the review reference for the next bump.

The image is amd64-only; the manifest declares `platform: "linux/amd64"`. The required remote-image scan therefore attempts exactly one platform, following the Chatterbox/Docling precedent. It runs emulated on Apple Silicon.

A pinned Trivy 0.74.0 scan reports 25 unique HIGH findings and 0 CRITICAL findings. The Debian 12 base findings comprise:

- Five util-linux advisories across eight base packages.
- Eight perl-base advisories.
- zlib1g's MiniZip CVE, ncurses, systemd libs, gzip and libacl1.

Those findings have no fixed Debian 12 package. Three additional libpcre2 findings have a Debian fix (`10.42-1+deb12u1`) but await an upstream image rebuild (the #1002 wave).

Three upstream-bundled node_modules packages carry four advisories: two in brace-expansion 5.0.7, one in ip-address 10.2.0 and one in node-tar 7.5.19. Their fixes need the next TrueForge release.

All 25 findings have purl-scoped exceptions expiring 2026-12-09. Upstream releases roughly daily; clearance should come through a reviewed pin bump, not exception renewal.

## 9. 2026-10-28 Docling dependency repair

### 9.1. Current package contract

Both providers pin `docling==2.142.0` and resolve `docling-slim==2.142.0`.
The July record above retains its historical version and evidence.

The locks use IBM models 4.0.4, parse 7.22.4, core 2.104.0 and doclang 0.8.0.
These are the earliest compatible family updates that admit Pillow 13.0.0.
Torch 2.13.0, torchvision 0.28.0 and the GPU base image remain unchanged.
Localhost requires Python 3.11 or later.

The inputs preserve patched certifi, setuptools, fsspec, Transformers and accelerate floors.
Pillow 13.0.0 and filelock 4.2.1 apply to GPU and localhost.
The Python 3.11 minimum removes the former localhost filelock 3.29.0 branch.

The [Docling metadata](https://pypi.org/pypi/docling/2.142.0/json),
[slim metadata](https://pypi.org/pypi/docling-slim/2.142.0/json) and
[parse metadata](https://pypi.org/pypi/docling-parse/7.22.4/json) define the required companions.
The [core metadata](https://pypi.org/pypi/docling-core/2.104.0/json) and
[IBM models metadata](https://pypi.org/pypi/docling-ibm-models/4.0.4/json) admit Pillow 13.

### 9.2. Local evidence and qualification limits

The Linux GPU closure reproduces all 131 pins byte-for-byte with frozen constraints.
This verifies dependency resolution; no GPU image installation or inference ran.

Fresh native macOS installations on Python 3.11 and 3.12 each contain 114 packages and pass dependency consistency checks.
On each interpreter, real core 2.104.0 tests cover 48 formula, export and chunk combinations, plus reload and deployment-import cases.
All four exports pass with warnings as errors through their direct APIs.
The earlier core 2.99.0 qualification established byte parity between the deprecated DocTags alias and its replacement.

Sixty real converter configurations pass on each tested macOS interpreter with network access blocked.
No pipelines initialize, conversions run or model downloads occur in those checks.

Counts below compare each lock with the committed `docling==2.102.1` baseline (`775c9056`).
The GPU lock moves from 127 to 131 pins and changes 10 versions.
They are certifi, doclang, docling, docling-core, docling-ibm-models, docling-parse, docling-slim, filelock, Pillow and setuptools.
It adds langcodes, language-data, marisa-trie, olefile and python-oxmsg, and removes jsonlines.

The localhost lock moves from 134 to 137 records and changes 12 versions: those ten plus fsspec and RapidOCR.
It adds the same five names and a macOS-only SciPy 1.16.0 record.
It removes jsonlines and the Python 3.10 backports exceptiongroup and tomli.

The former SciPy 1.15.3 macOS wheel fails direct native import independently of Docling on this host.
Localhost resolves SciPy 1.16.0 on macOS.
Other platforms retain 1.15.3; their native wheel behavior was not retested.

### 9.3. Remaining advisory findings

Before the Python 3.11 minimum change, scoped GPU audits fell from 59 to 38 advisory records.
Explicit Python 3.10 localhost requirements fell from 60 to 41 records.
Localhost Python 3.12 Linux and native macOS requirements each retained 38 records.
These counts describe the dated pre-floor audit, not the revised project, every deployment or future advisory state.

The pre-floor findings concerned Pillow 12.3.0 and, on Python 3.10, filelock 3.29.0.
After the Python minimum changed, a fresh audit still found 39 Pillow advisory records per provider.
The former Docling family required Pillow below 13, so the Python minimum change alone did not remove those findings.

After the family update, scoped audits reported no findings across the 131 GPU pins and 114 native localhost packages.
These results describe the reviewed closures on 2026-10-28; they do not cover every deployment or future advisory state.
No advisory exceptions were added.

The audit tool skips export rows whose markers fail on its own host.
The CI audit therefore projects the localhost export onto Linux x86_64, Linux aarch64 and macOS arm64 with CPython 3.11.
It audits each target separately, so the macOS-only SciPy branch is covered.
On 2026-10-29 the targets selected 133, 133 and 114 packages and reported no advisories.

Authentication and request bounds do not establish safety against every parser issue.

## 10. 2026-10-28 Local Deep Researcher serving repair

### 10.1. Current package contract

The combined lock requires `langgraph-api>=0.15.9` and `opentelemetry-api>=1.45.1` on Python 3.11 or later.
Both floors use uv's own rendering, `name>=x ; python_full_version >= '3.11'`.
A refresh therefore validates the `uv add` output it writes.
LangGraph API resolves to 0.15.9 with runtime-inmem 0.35.0.
OpenTelemetry's stable packages resolve to 1.45.1 and matching beta packages to 0.66b1.

The [LangGraph API advisory](https://github.com/advisories/GHSA-4hm6-w6qq-w73v) is fixed in 0.15.0.
Version 0.15.9 also admits the telemetry family required by the [OpenTelemetry fix](https://github.com/advisories/GHSA-xcq7-p3pf-p88h).
Earlier 0.15 releases retain a Prometheus exporter constraint that excludes that family.

Counts below compare the export with the committed baseline (`775c9056`).
Seventeen package versions change, and two OpenTelemetry exporter packages are new.
They include langgraph-cli 0.4.31 to 0.4.32 and langgraph-sdk 0.3.15 to 0.4.6.
They also include langgraph 1.1.3 to 1.2.11 and langgraph-prebuilt 1.0.8 to 1.1.0.
On Python 3.11, websockets moves from 17.1 to 16.1.1, the version Python 3.10 already used.

The upstream commit, upstream lock digest and Python 3.11 image remain unchanged.
The combined project's upstream-shaped Python 3.10 metadata remains intact.

The committed export is rendered by uv 0.11.19, the version CI pins.
uv 0.13.5 renders one pycparser marker differently, so the byte-equivalence check needs the pinned uv.

### 10.2. Local runtime evidence

A fresh native macOS Python 3.11 environment installs all 113 selected packages and passes dependency consistency checks.
The pinned, patched research graph preserves its one-loop and three-loop outputs with controlled model and search responses.

The real server packages pass health, thread creation, streaming, state retrieval, deletion and CORS checks through an in-process HTTP transport.
The fixture runs the actual server lifecycle and in-memory worker, then closes them.
No network connections, real models, search services or shared stack participate.

Ordinary trace propagation, span creation and OTLP serialization pass with warnings as errors.
The actual HTTP exporter uses a controlled transport; no telemetry leaves the fixture.
The graph retains an upstream configuration-schema deprecation warning.
No warning filters were added.

Linux Python 3.11 closure compilation selects 114 packages on both container architectures.
Resolution and native macOS fixtures do not establish a cold-start container qualification or live LiteLLM/SearXNG behavior.
The service remains experimental.

### 10.3. Deterministic advisory coverage

The runtime audit explicitly selects Linux CPython 3.11 dependencies for x86_64 and aarch64.
It sends marker-free exact pins to the audit tool and requires an exact matching package/version response.
Missing, skipped, duplicate, unexpected or mismatched results fail the check.
Unsupported marker contexts and conflicting selected versions also fail.

The earlier external Python 3.10 audit tool omitted the serving branch despite the bootstrapper's Python 3.11 minimum.
That omission prevented its strict mode from detecting the missing dependencies.
The explicit target selection removes this dependence on the audit tool's interpreter and host platform.

The approved online checks found two unreviewed advisories in the previous research-service graph.
On 2026-10-30 the registered online audit ran against the committed lock with `pip-audit` 2.10.0, the version CI pins.
It covered all 114 projected package/version pairs for both container architectures and reported no advisory.
This is dated evidence, not a future advisory guarantee.
No advisory exceptions were added.

## 11. 2026-11-01 Maintenance Pass

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| Docker Compose inline `configs` content | Minimum v2.23.1, recommended v2.26.0 | Docker's [configs reference](https://docs.docker.com/reference/compose-file/configs/), read on 2026-11-01, says inline `content` was introduced in Compose 2.23.1 | `services/langfuse/compose.yml` declares a top-level `configs` entry with inline `content`. `docker-compose.yml` always includes that fragment, so Compose 2.20.3 to 2.23.0 cannot load the project, even with Langfuse disabled. | `DockerManager.MIN_COMPOSE_VERSION` and `test_start_dependency_preflight.py` enforce 2.23.1; operator docs use the same floor. Not executed against an older Compose binary. |

## 12. 2026-11-14 Runtime-lock advisory review (#1895)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| Runtime-lock exceptions in `scripts/audit_runtime_locks.py` | Entries expire 2027-02-09. The Docling localhost Python 3.11.4 exception retired on 2026-11-25 with the Python 3.11.5 floor. | `pip-audit` on each lock and each Docling platform target, 2026-11-14. | Every entry must match a live finding on its own target. The audit fails on an unreviewed or stale entry. | Owner renewed the six on 2026-11-12. Owner approved the Docling exception on 2026-11-14. `scripts.audit_runtime_locks` exits 0. |
| soupsieve in the Airflow, JupyterHub and Docling GPU locks | 4.1 | PyPI: 4.1 needs Python 3.11.5 or later. CVE-2026-126190 is fixed in 4.0.0. CVE-2026-126192 is fixed in 2.10.0. | `uv pip compile` with the other pins held. The x86_64 and aarch64 outputs are identical. | Clears CVE-2026-126190 and CVE-2026-126192 in each lock. |
| soupsieve in Docling localhost | 4.1.3 on every target. `requires-python` raised to `>=3.11.5` on 2026-11-25. | PyPI: 3.0.2 and later need Python 3.11.5. | `uv lock` with uv 0.13.5 (revision 5) drops the 3.11.0 to 3.11.4 fork. | The 2.10 fork and its three-advisory exception are retired. Dependabot 1299, 1313 and 1314 close once main carries the floor (refs #1894). |
| dnspython, OpenTelemetry and tornado in Airflow, JupyterHub, Backend and MCP servers runtime | dnspython 2.9.2. opentelemetry-api 1.45.2 with the SDK, exporter and proto family at 1.45.2 and beta packages at 0.66b2. tornado 6.6.1. | Advisories CVE-2026-128089, CVE-2026-126360 and CVE-2026-126347, found by the audit on 2026-11-14. | Lock resolution for both platforms. Declared floors in the Airflow, JupyterHub and Backend requirements carry the fixed versions. | Moved to the fixed releases. Images were not rebuilt in this change. |

## 13. 2026-11-14 Trivy exception renewal (#1972)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| Trivy exceptions in `.trivyignore.yaml` expiring 2026-11-30 | 23 rows, renewed to 2027-02-12 | Owner approval on 2026-11-14, 90 days. gosu: newest Postgres Alpine tags still ship gosu built with Go 1.24.6. Langfuse 3.227.0 clears `migrate`; tracked in #931. | `load_exceptions` passes on 2026-11-30 and 2026-12-01. `test_container_security.py` passes, 118 of 118. | Renewed in each row's statement. Remaining fixes: gosu upstream (no release), libthrift upstream (#996), Langfuse major move (#931). |
| Build exclusions in `.container-scan-exclusions.yml` expiring 2026-11-30 | 4 rows (2 CUDA images, 2 neo4j llm-graph-builder Dockerfiles), renewed to 2027-02-12 | Owner approval on 2026-11-14, 90 days. Reasons unchanged: CUDA builds exceed the hosted runner profile; the neo4j builds need a separately pinned source archive. | `load_build_exclusions` passes on 2026-11-30 and 2026-12-01. | Renewed in each row's reason. Tracked under #1002. |
| Trivy exceptions in `.trivyignore.yaml` expiring 2026-12-09 to 2026-12-30 | 372 rows (325 due 2026-12-09, 7 due 2026-12-26, 8 due 2026-12-28, 32 due 2026-12-30), renewed to 2027-02-12 | Owner approval on 2026-11-14, 90 days, recorded in each row's statement. Reachability and fix status are unchanged from the earlier review. | `load_exceptions` passes on 2026-11-30, 2026-12-09, 2026-12-31 and 2027-01-15. `test_container_security.py` passes. | Renewed per row. Each row still needs its own fix or removal before 2027-02-12. |

## 14. 2026-11-15 MLflow 3.17.2 review (#1948)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| MLflow image (`MLFLOW_IMAGE`) | `ghcr.io/mlflow/mlflow:v3.17.2`, moved from v3.16.1 | Image pulled 2026-11-15: Python 3.11.15, MLflow 3.17.2, SQLAlchemy 2.1.5. Fixes CVE-2026-116692 (CRITICAL), CVE-2026-116690 and CVE-2026-53995 (HIGH). | Guard tests; `pip check` in the built image; guard smoke on the built image; Trivy 0.74.0 with `--ignore-unfixed` and `.trivyignore.yaml`, 0 fixable HIGH or CRITICAL findings. | Moved in `service.yml`, `compose.yml`, the Dockerfile and the `atlas_server.py` guard. Old value listed in `RETIRED_DEFAULTS`. License record pins LICENSE at commit `d0b397f8`. |
| MLflow route table, 3.16.1 to 3.17.2 | 356 Flask rules, identical in both. 3.17.2 adds `/health`, `/api/3.0/mlflow/server-info`, `/ajax-api/3.0/mlflow/server-info` and two `/gateway` routes. | Route tables of both wheels diffed on 2026-11-15. | Denylist run over every route on both versions. Every gateway route is closed; the three new read-only routes stay open. | MLflow's code says server-info carries no secrets, credentials, tenant data or internal addresses. Assistant and MCP access routes were open on 3.16.1 too and are now closed. |
| Postgres driver | Explicit `postgresql+psycopg2://` in both MLflow store URIs. | SQLAlchemy 2.1.5 selects psycopg 3 for a bare `postgresql://` URL. | Dockerfile dialect check and smoke driver probe. MLflow's URI classifier matches the scheme by prefix. | Atlas installs `psycopg2-binary`, so the explicit driver keeps the audited pin in use. |
| MLflow overlay pins | GitPython 3.2.1, sqlparse 0.6.0, pillow 13.0.0, pyasn1 0.6.5, aiohttp 3.14.4, certifi 2026.10.19 | MLflow 3.17.2 declares `gitpython<4,>=3.1.9` and `sqlparse<1,>=0.4.0`. The 3.17.2 base ships aiohttp 3.14.5. | `pip check` clean in the built image. Trivy has no fixable finding for these packages. | Overlay still pins aiohttp 3.14.4, one patch below the base. Raising that pin is a follow-up. |




## 15. 2026-11-15 Promptfoo recipe advisories (#1894)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| Promptfoo recipe lock (`examples/promptfoo-security`) | Promptfoo 0.124.1 kept. Nine npm findings accepted for the example. | `npm view promptfoo` on 2026-11-15: 0.125.1 is the newest release. It pins @hono/node-server 2.1.5 exactly, minimatch ^10 and glob ^13. | `npm audit --package-lock-only`: nine findings. Overrides clear them to zero, but they break declared ranges, and the macOS qualification cannot run here (#2029). | Owner decision on 2026-11-15 (#1894). Recorded in `examples/promptfoo-security/README.md`. The lock is outside the runtime audit, and no CI job runs npm audit on it. |

## 16. 2026-11-15 Blender 4.5 LTS for asset-baker (#1905)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| Blender (`ASSET_BAKER_BLENDER_VERSION`, `ASSET_BAKER_BLENDER_SHA256`) | 4.5.16 LTS, linux-x64 `23432b06…b10c11`, moved from 4.3.2 | Newest 4.5 LTS build on download.blender.org on 2026-11-15, checksum matched Blender's published `.sha256`. | Archive SHA-256 check. `blender -b --version` in the built image. Real headless bake in the 121-test asset-baker suite. | Blender's bundled certifi 2026.10.19 contains no emSign ECC Root CA - G3 in either copy (main and pip-vendored). |
| Blender-bundled pip, setuptools, urllib3 | pip 26.3 (installer pin), setuptools 84.1.0, urllib3 2.8.0 from `requirements-blender-locked.txt` | Trivy 0.74.0, HIGH and CRITICAL, `--ignore-unfixed`, against `.trivyignore.yaml`. Before the upgrade the image had 10 live findings in the bundled packages. | `pip check` in Blender's Python. Trivy: 0 fixable findings after the rebuild. | Runtime audit scans the new lock. The 11 exception rows scoped to `opt/blender/4.3/` are removed because their paths no longer exist. |
| blender-mcp `managed-localhost` bridge | Not changed in this review | The bridge was verified live on Blender 4.3.2. Blender is not installed on this host, so the 4.5 bridge was not run live. | Not run. | Done 2026-11-22, see section 23: live bridge run on 4.5.16 before this is called done. |

## 17. 2026-11-15 ComfyUI certifi overlay (#1905)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| ComfyUI ai-dock runtime, `certifi` | 2024.8.30 frozen by `ai-dock-v2-cpu-22.04-v0.2.7.txt`. Overlay `certifi==2026.10.19` (`--require-hashes`, `--no-deps`). | PyPI: 2026.10.19 is the release that omits the emSign ECC Root CA - G3. Its wheel digest is `42d35340…`. | `test_comfyui_container_provision.py`, `test_comfyui_manifest_generation.py` and `test_comfyui_custom_node_locks.py` pass (131 tests). Runtime audit scans the overlay lock. | Documented exception to the AI-Dock frozen-version rule, recorded in `services/comfyui/README.md`. The generator publishes the overlay under its SHA-256. A missing or altered overlay fails provisioning closed. Container sources only. The managed MPS host is not covered. |
| ComfyUI live check | Not run here. The ai-dock image is not pulled. | Not applicable. | Provisioning with the overlay in a running container. | Open: run the ComfyUI container and confirm the overlay installs and `certifi` reports 2026.10.19. |

## 18. 2026-11-21 LiteLLM /metrics requires a key (#1486)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| LiteLLM `/metrics` (`litellm_settings.require_auth_for_metrics_endpoint`) | Pinned image `ghcr.io/berriai/litellm:v1.83.14-stable.patch.2`, which reads the key from `litellm_settings`. | Source read from the pinned image on 2026-11-21. The middleware returns 401 without a key. | `test_prometheus_litellm_metrics_token.py` asserts the setting. | Unauthenticated `/metrics` with a cross-origin `*` was reproduced on 2026-10-08 (#1486). |
| Prometheus scrape credential (`litellm_metrics_token` secret) | `start.py` writes `volumes/prometheus/litellm-metrics-token` from `LITELLM_MASTER_KEY`, mode 0644. Compose mounts it read-only at `/run/secrets/litellm_metrics_token`. | Not applicable. | Token contents, mode and fail-closed behaviour are unit tested. The fragment baseline lists the secret. | Owner choice on 2026-11-21: a generated credentials file. The master key is held by the Prometheus container. Anyone with local read access to the volume directory can read it. |
| Prometheus config validation (`promtool check config`) | prom/prometheus:v2.55.1 | Not run. The Docker daemon returned HTTP 500 on `_ping` during this change. | Not run. | Open: run `promtool check config` and confirm the `litellm` target is UP after a restart. |
| LiteLLM live check | Not run. | Not run (same Docker outage). | Unauthenticated request returns 401. Bearer master key returns 200. | Open: run the check once Docker is back. |

## 19. 2026-11-21 Dependabot runtime-lock takes (#1895)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| Airflow overlay lock: virtualenv, fsspec | virtualenv 21.14.10 to 21.14.13 (floor raised to `>=21.14.13`). fsspec 2026.7.0 to 2026.9.0. | PyPI release metadata, 2026-11-21. Dependabot PRs #2062 and #2063 proposed these. Both are in range and have no cap in the Airflow graph. | `scripts.check_runtime_locks`; `scripts.audit_runtime_locks`; floor and lock-audit tests (276 pass). | Taken by hand on develop. The PRs are closed as superseded. |
| Parakeet MLX lock: fsspec | fsspec 2026.7.0 to 2026.9.0 (aarch64-apple-darwin) | PyPI release metadata, 2026-11-21. Dependabot PR #2073 proposed it. The MLX lock has no Lightning or NeMo, so the GPU cap does not apply. | Lock checker; audit | Taken by hand. PR #2073 is closed as superseded. |
| Docling GPU lock: mail-parser | mail-parser 4.5.0 to 4.6.3 | PyPI release metadata. Dependabot PR #2074 proposed it, and the PR body names no advisory ids. | Lock checker; audit | Taken by hand, pinned to 4.6.3 (the release the PR reviewed). PR #2074 is closed as superseded. |
| Docling localhost uv.lock: mail-parser | mail-parser 4.4.0 to 4.6.3. The 4.6.3 block is spliced from PR #2075's branch. | `uv lock --upgrade-package` picked 4.11.0, which is not the reviewed release. The spliced block has no dependencies. | `uv lock --check` passes. | Taken by hand. PR #2075 is closed as superseded. |

## 20. 2026-11-21 ragas 0.4.4 take (#1895)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| Backend runtime, backend test and JupyterHub locks: ragas | ragas 0.4.3 to 0.4.4 (final), PYSEC-2026-3046 exception retired from the backend, backend-test and JupyterHub audit specs | PyPI release metadata, 2026-11-21. 0.4.4 uploaded 2026-11-14. pip-audit reports 0.4.3 as PYSEC-2026-3046 with no fix version and 0.4.4 with no findings. | `scripts.check_runtime_locks`; `scripts.check_test_locks`; `scripts.audit_runtime_locks`; `bootstrapper/tests` (634 pass, 8 skip); backend `tests/test_rag_eval_service.py` on ragas 0.4.4 (11 pass, `-W error`). | Hand-taken on develop, replacing Dependabot PRs #2065 (backend) and #2067 (JupyterHub). The `langchain-community<0.4` cap is kept: 0.4.4 guards its vertexai import, so the cap is a deliberate hold for the LangChain 1.x pass. |
| ragas 0.4.4 live path: `llm_factory` through LiteLLM | OpenAI-compatible client to LiteLLM, unchanged | The #2065 PR body (rc1 notes) describes a `max_completion_tokens` change for dotted GPT models on the OpenAI path. The final 0.4.4 changelog was not checked. | Live LiteLLM run of `POST /api/rag/evaluate` | Pending. Docker is unresponsive, so the live run is blocked. #1895 stays open until it passes. |

## 21. 2026-11-22 LiteLLM 1.84.0 pin (#1486)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| LiteLLM image (`LITELLM_IMAGE`) | `ghcr.io/berriai/litellm:v1.84.0`, index digest `sha256:29252f25ed1b538d44f6b76ec97412c5537a180b39ede744b9f3e86ffdd278f5`. Replaces `v1.83.14-stable.patch.2` (digest `sha256:73cbb215d3d43ae5b42816217b200386eb08d567767fcb36cba163152c5f3c79`). | Earliest stable release with the fix. 1.83.14 and every 1.83 patch, including `stable.patch.3`, carry the body-consuming middleware. 1.84.0rc1 is the first release with the fix, and it is a prerelease, so it is not used. | PyPI `litellm==1.84.0` and GitHub tag `v1.84.0` (commit `e1fc955464bf493c15aef08f98e0e22bdf24d4cf`) have identical `prometheus_auth_middleware.py`, and it buffers and replays the body. | Owner choice (a) on #1486. The fix is verified live (below). Tag naming changes: from 1.84.0 the `-stable` suffix is dropped. |
| LiteLLM behaviour changes (upstream 1.84.0 notes and source) | `require_auth_for_metrics_endpoint` defaults to `True`; Atlas sets it explicitly. The master key now appears as alias `litellm_proxy_master_key`, not its SHA-256. Four additive Prisma migrations run at startup. | Read from the 1.84.0 wheel and the official release notes. | Atlas config in `bootstrapper/utils/litellm_settings.py`; no Atlas code reads the hash alias. | Nothing dropped or renamed. Grafana's LiteLLM dashboard metric names survive; label values for LiteLLM-side rejects change. Rollback to 1.83.14 after migration is untested. |
| Live checks, 2026-11-22, stack on the new image | Stack relaunched from `fix/1486-litellm-1-84`. | Container healthy within 110 s. | Unauthenticated `/metrics/` returns 401 in 4 ms with no `Access-Control-Allow-Origin` header. Bearer master key returns 200 in 6 ms with 72 `litellm_` series. Prometheus `litellm` target UP. `promtool check config` passes. Model list returns 20 models. The live RAG evaluation through LiteLLM returns 200 with the same scores as on 1.83.14. | Closes the #1486 live checks that failed on 1.83.14. |

## 22. 2026-11-22 n8n 2.48.7 with n8n-workflow 2.48.3 (Dependabot #2061)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| n8n server and init images (`N8N_IMAGE`, `N8N_INIT_IMAGE`) | `n8nio/n8n:2.48.7`, index digest `sha256:fabce3e9678a07d2dde83bd8f5124017f3ec457d0a3fc6b63bb4cec465d63861`. Replaces `n8nio/n8n:2.28.2` (digest `sha256:08b9411994c0d091c5cc0969ee9edcb85b5e79f26640703c0c342076ac6f2b17`). | Newest 2.48 patch. The image bundles `n8n-workflow` 2.48.3, so the server and peer match. 2.48.3 itself bundles 2.48.2, so it was not used. | `docker run --entrypoint sh` read of the bundled `n8n-workflow` package.json. Release notes from 2.28.0 to 2.48.7 scanned for breaking markers. | Owner decision (4) on the #2061 thread: move the server. |
| Community peer (`services/n8n/init/config`) | `n8n-workflow` 2.48.3 (exact). `@n8n/expression-runtime` 0.38.0 to 0.39.3, `@n8n/utils` 1.54.0 to 1.55.0. zod stays 3.25.76. | `npm view n8n-workflow@2.48.3`. Only these three packages change in the lock. | `scripts.audit_runtime_locks` passes. The zod exception `GHSA-mrhx-c7xc-m3w5` (owner-approved, time-boxed) is unchanged and now re-reviewed by 2027-02-09 (ledger row 133 corrected from 2026-11-28 to match the audit). | `test_shell_script_contracts` asserts the peer at 2.48.3. |
| Live checks, 2026-11-22, stack relaunched on `n8nio/n8n:2.48.7` | Local `.env` pins both images to 2.48.7. | Server healthy within 70 s. Database migrations ran from 2.28.2 to 2.48.7 (`Finished migration` lines in the server log). `n8n-init` exited 0 and installed the 140-package lockfile set. | Seed path (`seed-workflows.sh`) run in a throwaway `n8nio/n8n:2.48.7` container against a one-workflow plan built from `searxng-research-workflow.json`: `import:workflow` succeeded, and `publish:workflow` set `activeVersionId`. The test row was then unpublished and deleted by SQL, and no `atlas-consumer` rows remain. | Live owner setup: `/rest/settings` reports `showSetupOnFirstLoad: true` on a fresh stack. First-party MCP nodes exist in the image (`@n8n/n8n-nodes-langchain` `dist/nodes/mcp`). |
| 2.48 behaviour to watch | Recorded from the release notes and the config dist, not proven on this stack. | Not applicable. | `OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS` is removed only in v3. `N8N_UNVERIFIED_PACKAGES_ENABLED` defaults to false only in v3, which would stop ComfyUI community nodes on a future v3 move. | Not applicable. | A future v3 move needs both settings reviewed. |
| Rollback | The pre-upgrade `postgres` database dump is in the scratchpad (`supabase-pre-upgrade.dump`). | Not applicable. | n8n migrations are not reversible in place. | Restore the dump, then the 2.28.2 image pins, to roll back. |

## 23. 2026-11-22 Blender 4.5.16 for the managed blender-mcp bridge (#1905)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Host Blender for `managed-localhost` (`BLENDER_MCP_BLENDER_PATH`) | Blender 4.5.16 LTS at `/Applications/Blender-4.5.app`. The 4.3.2 install at `/Applications/Blender.app` is unchanged and still auto-detected for other tools. | Official `blender-4.5.16-macos-arm64.dmg`, 311568732 bytes. SHA-256 `c5cd40979f30033bc55ca84fccd00f2c1ddebb1d2f41618f220ef1f596d8b0f6` matches the published `blender-4.5.16.sha256`. | Managed preflight reports `Blender binary: /Applications/Blender-4.5.app/...`, addon `6641189231ca` provisioned, port free. | Local env only: `BLENDER_MCP_BLENDER_PATH` set; `BLENDER_MCP_SOURCE` restored to `disabled` after the run. |
| Live bridge run on 4.5.16 | Managed bridge started with `./start.sh blender-mcp start`. | `ps` shows `Blender --background --python ~/.atlas/blender-mcp/launcher.py` from `/Applications/Blender-4.5.app`. | `./start.sh blender-mcp health` reports running and port open. A `get_scene_info` call over `127.0.0.1:9876` returns `status: success` with the default scene (3 objects). | Stopped with `./start.sh blender-mcp stop`; port 9876 closed afterwards. |
| Blender's bundled Python certifi | Blender 4.5.16 `lib/python3.11` certifi. | Read with `Blender -b --factory-startup --python-expr`. | `emSign ECC Root CA - G3` is not in the bundle. | Blender's own Python does not trust G3. The system store is a separate question (ComfyUI, open on #1905). |

## 24. 2026-11-22 ComfyUI derived image without the emSign G3 root (#1905)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| ComfyUI image (`services/comfyui/build/Dockerfile`, built as `atlas-comfyui:local`) | Base `ghcr.io/ai-dock/comfyui:v2-cpu-22.04-v0.2.7` (digest `sha256:b47cd16007c309ebbb78b85f87bbc69ac9f7f3fc7596607e81940eeb7dca2421`, unchanged). The derived layer pins `ca-certificates=20260816ubuntu0.22.04.2` (jammy-updates). | No newer ai-dock CPU tag exists. The owner chose the derived image (route 1). | `grep` and `ls` checks inside the build fail the image if G3 is still enabled or linked. | Ubuntu `ca-certificates` 20230311 to 20260816. Roots 137 to 120. G3 unlinked. The postinst keeps G3 disabled in `/etc/ca-certificates.conf`. |
| Outbound TLS from the derived image | Not applicable. | Not applicable. | HTTPS to `github.com`, `huggingface.co`, `pypi.org` and `raw.githubusercontent.com` answers. `git ls-remote https://github.com/ltdrdata/ComfyUI-Manager` succeeds. | The 44 removed roots did not break these hosts. |
| Known vulnerabilities (Trivy 0.74.0, HIGH and CRITICAL, unfixed excluded) | Not applicable. | Derived and base images each have 1712 unique findings. | Set comparison: 0 new, 0 gone. | The derived layer adds no findings. The findings come from the 2024-era base, which has not been rebuilt since 2024-11. That is a separate, pre-existing item. |
| Live check, 2026-11-22 | `atlas-comfyui:local` running on the stack. | Container healthy within about 50 s after recreation. | `/opt/environments/python/comfyui` certifi 2026.10.19 with no G3 (pip-vendored path). System store: G3 link count 0, `ca-certificates` 20260816. | Observed, not caused by this change: the ComfyUI core update at start fails (`local changes would be overwritten`), because the ai-dock image edits two custom-node files. The core stays at `696672905`, not `v0.27.0`. |
| Open on #1905 | Not changed here. | Not applicable. | The ai-dock image's `jupyter` Python environment (certifi 2024.08.30) still bundles G3. Atlas does not call that environment. | Owner decision: whether the derived layer should also cover that environment. |

## 25. 2026-11-23 ComfyUI container core pinned to the image commit (#2106)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| `COMFYUI_REF` (container path) | `696672905fd17af2654ced11e3ab590d6a555996` (ComfyUI v0.2.7, the commit the ai-dock image runs). Was `v0.27.0`. | Image core unchanged. Upstream v0.27.0 is not used on the container path. | Live: the AI-Dock update now targets the pinned commit. The `local changes would be overwritten` error is gone. `git rev-parse` in the container gives `696672905`. The container is healthy. | Runtime no-op: the core is the same as before. Docs now say so. |
| Migration of existing `.env` files (`RETIRED_DEFAULTS`) | `COMFYUI_REF` retires `v0.27.0`. `LITELLM_IMAGE`, `N8N_IMAGE` and `N8N_INIT_IMAGE` retire their replaced values. | Pin-refresh runs on every start and rewrites only retired values. | `build_pin_refresh` tests pass (8). Local `.env` rewrote `COMFYUI_REF` to the pinned commit on the next start. | Existing installs now migrate without a manual edit. |
| Krea 2 and the cancel endpoint on the container path | Not available on `v0.2.7`. Available on the managed MPS path, which pins `v0.27.0`. | Upstream core Krea 2 support first appeared in v0.26.0. | Test `test_comfyui_core_pin_supports_krea2` now checks the container pin and the MPS ref separately. | Documented in the README. Moving the container core is ticket #2107. |
| Transient git message at start | Not applicable. | Not reproduced on the next start. | `error: could not lock config file /home/user/.gitconfig` appeared twice during the first update, from concurrent `git config` writes. No lock file remains. | Non-fatal. Watch on later starts. |

## 26. 2026-11-23 JupyterHub conda CA bundle without the emSign G3 root (#1905)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Conda default CA bundle (`/opt/conda/ssl/cacert.pem`, `cert.pem` links to it) | Base `quay.io/jupyter/datascience-notebook:2026-08-24`. The file held G3 three times before the change. | Read from the local base image. | The Dockerfile replaces the file with certifi's bundle (pinned `2026.10.19`, no G3). The build fails if G3 survives. Static test `test_jupyterhub_ca_trust.py`. | Simulated in a throwaway container from the same base with the backend's certifi 2026.10.19 bundle: G3 count 0, 120 roots, Python's default SSL context loads. The full JupyterHub image was not rebuilt; the next build verifies it. |

## 27. 2026-11-23 ComfyUI base-image stage 1 (#2108)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Ubuntu packages in the derived layer (`services/comfyui/build/Dockerfile`) | 29 exact pins, the Ubuntu packages whose HIGH and CRITICAL findings cleared in the scan image. Versions come from `atlas-comfyui:scan-a`. | The scan image upgraded 230 packages. Only these 29 cleared fixable findings, so only they are pinned. | Rebuilt image: all 29 installed at the pinned versions. | Stage 1 of #2108. |
| ComfyUI jupyter Python environment certifi | `certifi==2026.10.19`, hash-pinned from the ComfyUI overlay lock. Was 2024.8.30 with G3. | PyPI wheel hash as in `certifi-overlay.txt`. | Rebuilt image: jupyter env certifi 2026.10.19 with G3 count 0. The build fails if G3 remains. | Closes the jupyter-env item on #1905. The ComfyUI venv overlay still applies at start. |
| Trivy 0.74.0, HIGH and CRITICAL, unfixed excluded | Derived image. | 335 unique fixable findings, down from 1712 on the base. | 0 new findings versus the base. | Residual 335 is stage 2 on #2108. |
| Live, 2026-11-23 | Recreated on the rebuilt image. | Healthy after about 50 s. Core commit `696672905`. | No `would be overwritten` lines in the log. System store: `ca-certificates` 20260816, G3 link count 0. | Runtime unchanged. |

## 28. 2026-11-23 ComfyUI custom-nodes preflight hook (#2107)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Preflight hook `preflight.d/05-atlas-custom-nodes.sh` (derived image) | Restores only tracked files under `custom_nodes/` to the checked-out commit before the AI-Dock update. Never touches untracked custom nodes. | Read from `services/comfyui/build/atlas-preflight-custom-nodes.sh`. | Scratch test on a copy of the live custom-nodes volume: the `v0.27.0` checkout fails without the hook (`Your local changes ... would be overwritten`) and succeeds with it. Untracked custom nodes are kept. | Static test `test_comfyui_preflight_hook.py`. |
| Live state, 2026-11-23 | Container core pinned to `696672905`. | The hook ran at start: `Restoring tracked custom_nodes files to the checked-out core` is in `/var/log/preflight.log`. | The tree is clean (no dirty files), HEAD is `696672905`, the API answers, and the log has no overwrite errors. | Runtime unchanged. |
| Core move to v0.27.0 | Not done. | The hook removes the blocker. The move is a separate decision. | Not applicable. | Ticket #2107 stays open for the move decision. |

## 29. 2026-11-23 ComfyUI venv stage 2 (#2108)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| ComfyUI venv (`/opt/environments/python/comfyui`, Python 3.10.12) | 26 pins in `services/comfyui/build/Dockerfile` stage 2: GitPython 3.2.1, PyJWT 2.14.0, aiohttp 3.14.4, anyio 4.14.2, certifi 2026.10.19, cryptography 50.0.0, fsspec 2026.6.0, h11 0.16.0, jaraco.context 6.1.0, jupyter_core 5.8.1, oauthlib 4.0.1, opencv-python 4.8.1.78, pillow 12.3.0, wheel 0.46.2, protobuf 6.33.5, rembg 2.0.69, sentencepiece 0.2.1, setuptools 78.1.1, soupsieve 2.10, starlette 1.3.1, tornado 6.5.9, transformers 5.10.0, urllib3 2.8.0, torch 2.10.0+cpu, torchvision 0.25.0+cpu, torchaudio 2.10.0+cpu. | Trivy fixed versions from the stage 1 scan. Where the fix needs Python 3.11 or later, the newest Python 3.10 release is pinned. | Each pin resolves on Python 3.10 (`pip download`). torch, torchvision and torchaudio move together: torch 2.10 with torchvision 0.20.1 fails with `torchvision::nms does not exist`. | Trivy 0.74.0, HIGH and CRITICAL, fixable, unique (CVE, package, installed version): 335 to 266. 103 cleared; 34 new IDs on the Python 3.10 maxima (see the next rows). Quick startup test (`main.py --cpu --quick-test-for-ci`) on a copy of the live custom-nodes volume: exit 0, custom nodes loaded. Live: ComfyUI recreated on this image, healthy, API 200, core `696672905` clean, no error lines. Rollback image: the stage 1 image ID `ea0871060d5a`. |
| Residual: pillow 13.0.0, rembg 2.0.82, soupsieve 3.0.2 and 4.0.0 | Not installable on Python 3.10. The 3.10 maxima are pinned: pillow 12.3.0 (29 rows), rembg 2.0.69 (1), soupsieve 2.10 (2). | Trivy fixed versions need Python 3.11 or later. | Not applicable on 3.10. | Open on #2108 (type:decision): a 3.11+ ComfyUI venv depends on the ai-dock base image, or an owner-approved exception. No exception is added here. |
| Residual: jaraco.context 5.3.0 and wheel 0.45.1 vendored in `setuptools/_vendor` | The pinned setuptools 78.1.1 ships these copies, so pip cannot reach the fixed versions. | Trivy reports the vendored `dist-info` paths. | Not applicable. | Open on #2108: a setuptools release that vendors fixed copies, or removal of the vendored copies. |

## 30. 2026-11-23 ComfyUI quick tunnels off (#2127)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| ai-dock Cloudflare quick tunnels (`cf_quicktunnel`, base `ghcr.io/ai-dock/comfyui:v2-cpu-22.04-v0.2.7`) | The derived image sets `CF_QUICK_TUNNELS=false`. Atlas's `ENABLE_QUICKTUNNEL=false` in compose and `service.yml` is not read by this base. | Read from `/opt/ai-dock/bin/init.sh` (`init_count_quicktunnels`) and `/etc/supervisor/supervisord/conf.d/quicktunnel.conf` (`autostart=true`). | Before: four `cloudflared` processes and three public `trycloudflare.com` URLs in the live container. After the rebuild and recreate: zero `cloudflared` processes, and `CF_QUICK_TUNNELS=false` in the environment. | Static test `test_quick_tunnels_are_off_in_the_derived_image`. Open on #2127: `WEB_ENABLE_AUTH=false` is an owner decision. |

## 31. 2026-11-24 speech weights provenance (STT and TTS review, #2111)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Kokoro-82M ONNX, Speaches TTS default (`SPEACHES_TTS_MODEL=speaches-ai/Kokoro-82M-v1.0-ONNX`) | HF mirror `speaches-ai/Kokoro-82M-v1.0-ONNX`, 3 commits listed, all March 2025; latest `dc196c76`. `model.onnx` 325,532,387 bytes, sha256 `7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5`. `voices.bin` 28,214,398 bytes, sha256 `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d`. | Mirror has no license tag; its README names only Speaches. | The conversion script in Speaches v0.9.0-rc.3 (`scripts/create_kokoro_v1_onnx_model_repos.py`) downloads `kokoro-v1.0.onnx` and `voices-v1.0.bin` from GitHub release `model-files-v1.0` of `thewh1teagle/kokoro-onnx`. | Both release assets were downloaded and hashed on 2026-11-24. Their sha256 values equal the mirror's, so the mirror is a byte-for-byte copy of that release. The hexgrad Hugging Face account is not on this chain. The reported takeover does not reach these files. Not verified: that the release matches hexgrad's upstream `.pth` weights. Open: Speaches calls `snapshot_download(repo_id)` with no revision (`src/speaches/executors/kokoro.py:181`), so Atlas cannot pin the artifact without a Speaches change or a verified local copy. |
| Kokoro-82M upstream, `hexgrad/Kokoro-82M` | Apache-2.0 (API license tag). Last commit `f3ff3571791e39611d31c381e3a41a3af07b4987`, 2025-04-10. 50 commits. | Read 2026-11-24. | Upstream weights are not the artifact Atlas loads (see above). | Recorded for lineage. |
| kokoro-onnx release source, `thewh1teagle/kokoro-onnx` | MIT (repo license), last push 2026-09-01. Release `model-files-v1.0`. | Read 2026-11-24. | Release assets carry no GitHub digest, so hashes were computed locally. | The source of the bytes Atlas loads, via the mirror. |
| Parakeet TDT v3 weights (`nvidia/parakeet-tdt-0.6b-v3`, NeMo path of `parakeet-container-gpu`) | HF license tag cc-by-4.0, last modified 2026-08-05 | Read 2026-11-24 | Service-configured weight with no inventory row (R-SVC-MODELS). | Recorded only. No row is added, because the gate matches `models` rows only to ComfyUI and Ollama catalogue pins. |
| Parakeet TDT v3, MLX port (`mlx-community/parakeet-tdt-0.6b-v3`, `parakeet-localhost`) | HF license tag cc-by-4.0, last modified 2025-08-16 | Read 2026-11-24 | As above. | Recorded only. |
| Faster-Whisper large-v3 (`Systran/faster-whisper-large-v3`, Speaches STT preload) | HF license tag mit, last modified 2023-11-23 | Read 2026-11-24 | As above. | Recorded only. The upstream weights are `openai/whisper-large-v3`, Apache-2.0 (tag, last modified 2024-08-12). |
| Chatterbox weights (`ResembleAI/chatterbox`, chatterbox-container-gpu) | HF license tag mit, last modified 2026-06-10. The GitHub README states it was retired as of August 2026 and is no longer maintained. | Read 2026-11-24 | The Atlas wrapper image `travisvn/chatterbox-tts-api` is AGPL-3.0 (row exists under `images`). | Recorded only. The upstream is retired, so new use needs an owner decision. |
| Speaches default services | No `speaches` container exists in the local stack on 2026-11-24, running or stopped. | `docker ps -a` on 2026-11-24. | Atlas's default STT and TTS engine is not deployed in this stack. | Recorded for the next live verification. |

## 32. 2026-11-24 voice-activity gate evaluation (STT silence, #2111)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Voice-activity gate in front of STT (proposed; no Atlas service ships it yet) | Silero VAD `silero_vad.onnx` from `snakers4/silero-vad` master, 2,204,103 bytes, sha256 `f6f4407b41e05e277e7f5cfd9b9ae957b1dfe763c6076a934734c0b448e09b31`. webrtcvad 2.0.10 at aggressiveness 2 for comparison. | Read 2026-11-24. | Gate decision: skip the model only when no speech is found. Speech clips must never be skipped. Silence must be skipped. | Conversational speech, 300 clips (AMI and Earnings22), attenuated 0 to -40 dB. webrtcvad wrongly skipped 7/300 at -20 dB, 30/300 at -30 dB and 78/300 at -40 dB. Silero alone (max probability >= 0.5) wrongly skipped 18/300 at -20 dB, 38/300 at -30 dB and 75/300 at -40 dB. The combined rule, keep if raw or peak-normalized Silero finds speech, wrongly skipped 0/300 at 0, -20, -30 and -40 dB. Combined rule skipped white noise and hum plus noise at -30 to -70 dBFS (5/5 each) and digital silence. Not tested: real speech over background noise, and silence with speech-like background such as TV. webrtcvad is superseded for this use. |

## 33. 2026-11-25 Speaches runs from a pinned, verified cache (#2111)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Speaches preload models (Kokoro TTS and Faster-Whisper STT) | Kokoro `speaches-ai/Kokoro-82M-v1.0-ONNX` at `dc196c76d64fed9203906231372bcb98135815df`: `model.onnx` sha256 `7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5`, `voices.bin` sha256 `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d`. Whisper `Systran/faster-whisper-large-v3` at `edaa852ec7e145841d8ffdb056a99866b5f0a478`: `model.bin` sha256 `69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1`. Seven files in total, listed in `services/speaches/pin-cache.sh`. | Hashes read from the Hugging Face API lfs fields on 2026-11-24, and from the GitHub release on the same date. | Speaches v0.9.0-rc.3 calls `snapshot_download` with no revision, so an unpinned cache follows `main`. The repo now checks each weight hash at boot and runs with `HF_HUB_OFFLINE=1` only when all three match. | Live on 2026-11-25 from the staged volume `atlas-speaches-cache`. STT `whisper-1` returned the reference text for `ami-000`. TTS (`af_heart`) returned a 140,366-byte WAV. The round trip returned the input sentence. Inside the app process, `HF_HUB_OFFLINE=1` and `PRELOAD_MODELS=[]`. The log shows zero Hub download attempts. Open: the Speaches image is a pre-release (`0.9.0-rc.3-cpu`, compressed 0.93 GB per the registry manifest). |
| Local configuration (not tracked) | `.env`: `STT_PROVIDER_SOURCE` and `TTS_PROVIDER_SOURCE` set to `speaches-container-cpu`, `SPEACHES_SCALE=1`. Previous file kept in the scratchpad as `env.before-speaches`. | Read 2026-11-25. | Speaches was `disabled` on 2026-11-24. | Enabled at the owner's request, after the disk check. |

## 34. 2026-11-25 speech weight license rows (#2111)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Kokoro-82M ONNX (Speaches TTS, pinned in section 33) | Apache-2.0 weights (upstream `hexgrad/Kokoro-82M` at `f3ff3571791e39611d31c381e3a41a3af07b4987`). Artifact sources pinned to `dc196c76d64fed9203906231372bcb98135815df`. | Read 2026-11-24 (HF API license tag). | The license gate matches each row's sources and `license_url` to `services/speaches/models.yaml`. | Rows added to `docs/reference/license-inventory.yaml`. Modes take the reviewed Apache-2.0 defaults: hosted and integration permitted, redistribution with notices. The artifact comes from the MIT `kokoro-onnx` release (section 31), which is not separately listed. |
| Faster-Whisper large-v3 (Speaches STT, pinned in section 33) | MIT (`Systran/faster-whisper-large-v3` at `edaa852ec7e145841d8ffdb056a99866b5f0a478`). Upstream `openai/whisper-large-v3` is Apache-2.0. | Read 2026-11-24 (HF API license tag). | As above. | Row added. Modes take the reviewed MIT defaults: hosted and integration permitted, redistribution with notices. |
| Parakeet v3, MLX port, Chatterbox weights | Not pinned by Atlas yet. | As before. | The gate reports these four sources as floating, carried as open items. | No rows. The R-SVC-MODELS open item says so. |

## 35. 2026-11-25 Airflow SMTP provider advisory fixed in the lock (#1895)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| `apache-airflow-providers-smtp` in the Airflow image lock (`services/airflow/build/requirements-locked.txt`) | 3.0.2 to 3.2.2. The lock keeps `pyyaml-ft==8.0.0`, which `libcst` needs. A hand edit had dropped it; the regenerated lock restores it. | 3.2.2 uploaded 2026-11-23. Advisory PYSEC-2026-4396 (OSV alias CVE-2026-49267) fixed in 3.2.2; affected range 2.0.0 up to 3.2.2. | Requires `apache-airflow>=2.11.0` and `apache-airflow-providers-common-compat>=1.12.0`. The lock has 3.3.2 and 1.18.0. | Resolved with `targeted_upgrade.py` (Python 3.12, `x86_64-unknown-linux-gnu`), then diffed against the prior lock. Atlas's Airflow configures no SMTP anywhere in `services/airflow`, so the advisory was not reachable here. The fix removes the audit failure without an exception. The audit passes on all 30 locks. |

## 36. 2026-11-25 voice-activity gate under noise (#2111, exploratory)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Combined Silero gate (section 32), tested with noise mixed into speech | Silero `silero_vad.onnx` sha256 `f6f4407b41e05e277e7f5cfd9b9ae957b1dfe763c6076a934734c0b448e09b31`. Keep threshold 0.5. | Read 2026-11-25. | The gate must not skip speech. It must skip noise and silence. | Speech mixed with noise, 300 clips. At 20 dB and 10 dB SNR, 0 speech clips were skipped (white, pink and babble). At 0 dB SNR, 6 to 13 of 300 were skipped (white and pink). Babble at 0 dB skipped 0. Exploratory: at thresholds 0.3 and 0.2, the 0 dB misses drop to 3 to 7 of 300, and all noise-only clips at -50 and -70 dBFS are still skipped. Threshold not changed. Real background audio is untested. Results in the bench folder, `RESULTS_vad_noisy.md`. |

## 37. 2026-11-25 n8n-workflow 2.48.3 to 2.49.1 (Dependabot #2131, #1895)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| `n8n-workflow` in the n8n init config (`services/n8n/init/config`) | 2.48.3 to 2.49.1. The lock's transitive `@n8n/expression-runtime` and similar packages move in step. | 2.49.1 is the newest minor on npm. Its peer dependency is still `zod` 3.25.76. | The reviewed zod exception (GHSA-mrhx-c7xc-m3w5) still applies, so the bump does not remove it. | Lock and manifest taken from Dependabot #2131. The runtime-lock audit passes with them (1 reviewed exception). `npm ci --dry-run` succeeds. 131 n8n-related bootstrapper tests pass. |

## 38. 2026-11-25 setup-uv 10.3.3 to 10.4.0 in the CI workflows (Dependabot #2130)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| `astral-sh/setup-uv` in `container-security.yml`, `docs-pages.yml`, `services-lint.yml`, `upstream-drift-watch.yml` | 10.3.3 (`68559e99cc807c1eb7e9238fd43a15bba460eec2`) to 10.4.0 (`1db35dd8eee11c5894872e0b383ae6c406cf738d`). | 10.4.0 release notes read 2026-11-25. | The new `require-checksums` input defaults to off. The other changes are fixes or maintenance. No breaking change in the notes. | The four workflow files parse as YAML. The change touches only the pin lines. The workflow-related tests pass, with the drift-watch test's recorded SHA updated. Three tests were already failing on the commit where this session began: two final-image-scan count tests and the ComfyUI inline-pin test. |

## 39. 2026-11-25 ComfyUI venv moves to Python 3.11.15 (#2108)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| ComfyUI venv in `services/comfyui/build/Dockerfile` (AI-Dock base `v2-cpu-22.04-v0.2.7`, Python 3.10.12) | Python 3.11.15 from uv's standalone builds, at the same AI-Dock venv path. | AI-Dock publishes 48 ComfyUI tags. None is Ubuntu 24.04 or Python 3.11 (checked 2026-11-25). | Packages come only from `build/requirements-venv311-locked.txt` (159 pins, uv 0.11.19, CPU torch index). OSV finds zero advisories across the 159 pins. | Image built and smoke-tested 2026-11-25. ComfyUI serves `system_stats` and only `nodes_audio.py` fails to import. Live container recreated on the same volumes; health check passes. |
| uv binary in the image | 0.11.19, tarball sha256 `7035608168e106375b36d0c818d537a889c51a8625fe7f8f7cad5e62b947c368` | Same version as the CI `setup-uv` toolchain. | Downloaded from the astral-sh release and checked before extraction. | The build fails closed on a checksum mismatch. |
| Packages removed from the ComfyUI venv | torchaudio, transparent-background, flet, flet-runtime, matrix-client | torchaudio has no torch 2.14 build for Python 3.11 on PyPI or the CPU index. flet pinned fastapi 0.115.4, which conflicted with starlette 1.x. | Owner decision 2026-11-25: option A, as recommended. | ComfyUI's audio nodes fail to import. The background-removal node in `ComfyUI_essentials` needs `transparent_background`, which is absent. The `background_removal` model folder remains on the volume. |
| AI-Dock boot step (`AUTO_UPDATE=true`, the Atlas default) | `pip install -r requirements.txt` against the ComfyUI venv on every start | The base update checks out `COMFYUI_REF`, which equals the image commit, so only the pip step changes anything. | `pip.conf` constrains the venv to the lock. The constraint file also contains `torchaudio<0`. | Verified in the image: the step logs `ResolutionImpossible` for torchaudio and returns 1. `preflight_main` still returns 0, torch stays `2.14.0+cpu`, and `pip check` is clean. Without the constraint, the step installed torchaudio 2.11.1 from PyPI, which cannot load because its CUDA runtime is missing. |
| Residual Python findings in the image | Other AI-Dock venvs still pin aiohttp 3.10.10, tornado 6.4.1 and starlette 0.27.0 (`api`, `jupyter`, `python_310`, `serviceportal`). | Not checked against those venvs' own advisories. | Out of scope for the ComfyUI venv. | Not fixed here. Follow-up needed. |
| Rollback | Previous image kept as local tag `atlas-comfyui:py310-prev`. | Not applicable. | Not applicable. | Remove the tag once the 3.11 image has run for a while. |

## 40. 2026-11-25 owner decisions on the open speech, TTS and GPU items (#1458, #1459, #2148)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Chatterbox (`services/chatterbox`, `travisvn/chatterbox-tts-api:gpu`, pinned digest) | Unchanged. Support tier stays `experimental` (generated from the manifest). | No tested replacement in Atlas. Qwen3-TTS-Base (Apache-2.0) is untested here. | The pinned revision has no CORS or auth hook. The port binds to 127.0.0.1 by default. | Decision: freeze. Status recorded in the service README. Retire after a candidate passes the same tests. Comment on #1458. |
| Speech-to-text default | `speaches-container-cpu` stays the container default. Parakeet-MLX (`parakeet-localhost`) is recommended on macOS hosts. | Speech evaluation: Parakeet-MLX ties Qwen3-ASR-1.7B on 297 conversational clips. whisper large-v3-turbo scored lower. | Parakeet has no CPU container path, so it cannot be the container default. | Decision recorded in `services/stt-provider/README.md`. No runtime default changed. |
| Voice-activity gate | Combined Silero rule, threshold 0.5. | Threshold 0.2 was exploratory, on synthetic noise only. | Placement moves to one proxy in front of Speaches. | Tracked in #2148. Real-audio test required before any threshold change. |
| NGC PyTorch container (`nvcr.io/nvidia/pytorch:26.06-py3`, about 10 GB) | Not pulled. | Needs an NVIDIA host to run. | The GPU lock question (#1459) stays open. | Decision: skip the pull on this Mac. Comment on #1459. |
| diskcache advisory GHSA-w8v5-vhqr-4h9v (CVE-2025-69872), Dependabot alerts 248, 250, 259 | Unchanged in the locks. | No fixed diskcache release exists (Dependabot shows no patched version). | The runtime audit does not flag this advisory, so no audit exception entry is needed. | Dismissed on GitHub as tolerable risk, time-boxed, review by 2027-02-09. |

## 41. 2026-11-25 Dependabot dispositions and Kokoro verification (#1895, #2109, #1894)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Kokoro ONNX mirror (`speaches-ai/Kokoro-82M-v1.0-ONNX`) against the upstream PyTorch model (`hexgrad/Kokoro-82M` at f3ff3571) | Speaches path: kokoro-onnx 0.4.5, onnxruntime 1.22.1. Upstream path: kokoro 0.9.4, torch 2.15.0. | Five phrases, one voice, CPU, macOS arm64. Metrics only; nothing was listened to. | Threshold fixed before measuring: mean absolute log-mel difference under 1.0 dB is equivalent. | ONNX is +3.2 dB louder (gain 0.687). After level matching, four phrases sit at 0.92 to 0.97 dB. Phrase 3 differs: espeak emits U+0329, which upstream's vocabulary lacks; kokoro-onnx maps it to id 175. The style row index shifts duration by up to 0.8%. Shipped output differs more (6.8 to 14.2 dB) because G2P differs (espeak against misaki). No repo change. |
| Promptfoo recipe lock alerts 828, 1020, 1232, 1261, 1283 to 1286 | Unchanged lock. Promptfoo 0.124.1. | npm audit reports nine findings on this lock. | The owner accepted these on 2026-11-15 (#1894). The overrides break Promptfoo's declared ranges and cannot be verified until the macOS qualification passes (#2029). | Dismissed as tolerable risk on GitHub, citing #1894. Overrides held on a local branch, not shipped. |
| Parakeet MLX lock, mlx (alerts 273, 274) | mlx and mlx-metal 0.29.3 to 0.29.4, compiled with `MACOSX_DEPLOYMENT_TARGET=14.0`. | OSV: 0.29.4 has no advisories. 0.29.3 has PYSEC-2025-138 and PYSEC-2025-139. | macOS 13 is no longer supported for `parakeet-localhost` (owner's recorded decision, #2109). | Shipped in #2155. The audit exception was removed. |
| Parakeet GPU lock, the re-review date in row 132 | Unchanged lock. | The audit comment in `scripts/audit_runtime_locks.py` renewed the exceptions on 2026-11-12 for 90 days. | NeMo 3.0.0 range is `>=3.0.0,<4.0`, not the older 2.7.x range. | Re-review date corrected to 2027-02-09. |

## 42. 2026-11-25 AI-Dock residual venvs in the ComfyUI image (#2108)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Residual AI-Dock venvs (`api`, `jupyter`, `python_310`, `serviceportal`) in `services/comfyui/build` | Four audited Python 3.10 locks (`requirements-{api,jupyter,python_310,serviceportal}-locked.txt`). starlette 0.52.1, tornado 6.5.10, soupsieve 2.10, jupyter-server 2.21.0 and others as in the locks. | Trivy HIGH/CRITICAL, `--ignore-unfixed`: 233 to 166 unique; Python 72 to 5. | Each venv passes `pip check`. Portal `/`, `/login`; API `/docs`; Jupyter `/api/status`; ComfyUI `/system_stats` return 200. | Owner-approved exceptions to 2027-02-09 for 4, 7, 4 and 5 findings. Open: tornado and soupsieve need Python 3.11; starlette 1.x breaks the portal templates; setuptools 70.3.0 sits in the base layer. The `python_310` kernel protocol fails on develop too, so it predates this change. |

## 43. 2026-11-26 speech gate in front of Speaches (#2148)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Silero VAD model in `services/speech-gate/model` | ONNX, sha256 `f6f4407b41e05e277e7f5cfd9b9ae957b1dfe763c6076a934734c0b448e09b31`, checked at load. Redistributed in the repo (2.2 MB). | Silero VAD is MIT-licensed upstream. | The model hash is verified before the server binds. | Owner-approved design, #2148. |
| Speech gate base image | `python:3.12.14-slim` (PSF-2.0) | Image pinned by tag in the Dockerfile. | Image scanned by the final-image scan (27 contexts, 53 images). | Added to the license inventory. |
| Speech gate labels | 1,221 rows built from the v2 manifest. Speech 1,200, missed 19 at threshold 0.5. Non-speech 21, passed 0. | `build_labels.py` reproduces the committed file byte-for-byte after its refactor. | Labels are marked `pending-owner-review`. | Owner review pending. Threshold stays at 0.5 until the labels are reviewed. |
| Live check | Silence: HTTP 200, empty text. Speech: transcribed. | Run against the stack's Speaches through a throwaway gate. Torn down afterwards. | Stack containers untouched. | Passed. |

## 44. 2026-11-26 Verba host port removed (#1458)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---|---|---|---|
| Verba host port (`services/verba/compose.yml`) | Image digest unchanged (`verba@sha256:0947d289…`). | Upstream websocket skips the same-origin check and honours a client `rag_config` URL (#1458). | Host publish removed. Kong route `verba.localhost` only, with basic-auth and ACL. | `test_verba_publishes_no_host_port` and `test_verba_compose_contract` guard it. The pinned digest's behaviour is taken from the issue, not run here. Scoped LiteLLM key tracked in #1377. |
