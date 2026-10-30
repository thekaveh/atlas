# External Dependency Contract Ledger

Durable ledger for maintenance checks against consumed external contracts, as
required by the overnight maintenance spec's external-dependency contract
section. Each row records what was checked, the pinned or configured version,
the current/latest status observed during the pass, and the evidence source.

## 1. 2026-07-02 Maintenance Pass

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| Docker Compose `include:` support | Minimum v2.20.3, recommended v2.26.0 | Existing `DockerManager.check_compose_version()` parses `docker compose version --short` | Compose version floor is now enforced during startup preflight before the modular `include:` compose file is used | Guarded by `bootstrapper/tests/test_start_dependency_preflight.py` |
| scikit-learn requirement in backend | `scikit-learn>=1.9.0` | PyPI reports 1.9.0 as the latest release, released 2026-06-02 | Requirement is valid for the Python 3.12 backend image, but host Python 3.10 cannot collect it | Do not lower the requirement for host-tool compatibility; run audits with the target Python version |
| JupyterHub Python requirements | `torch==2.4.1`, `nltk` unpinned | `pip-audit 2.10.1` under host Python 3.10 collected the requirements and reported 22 known vulnerabilities across `torch 2.4.1` and `nltk 3.9.4` | Vulnerability status only; no automatic upgrade attempted because PyTorch/PyG wheels are image-coupled | Resolved 2026-07-14 by the coordinated PyTorch 2.11/PyG migration and NLTK 3.10 floor documented below |
| MinIO client service-account commands | `pgsty/mc:RELEASE.2026-09-16T00-00-00Z` (Silo client; was `minio/mc:RELEASE.2025-08-13T08-35-41Z`) | MinIO current docs center `mc admin accesskey`; legacy `mc admin user svcacct` remains a replacement-bound contract to verify before future client bumps | `services/minio/init/scripts/init-minio.sh` still uses `mc admin user svcacct` create/edit/list | Validated end to end on 2026-09-28 against the pinned Silo server (see "MinIO client provisioning"); `svcacct` stays the contract until a deliberate move to `mc admin accesskey` or to another server (#1277) |
| n8n community package load path | `n8nio/n8n:2.28.2` | Self-hosted n8n loads manually installed community packages from `/home/node/.n8n/nodes/node_modules`; the image ships Node 24 and `n8n-workflow` 2.28.1 | Atlas runs the same pinned n8n image as a pre-start init, installs a committed npm lock with scripts disabled into the shared user folder, and blocks n8n startup on failure; custom specs must be exact versions | Guarded by `bootstrapper/tests/test_shell_script_contracts.py`, Compose byte-equivalence, and live pinned-image startup validation |

## 2. 2026-07-14 Maintenance Pass

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| JupyterHub Python/PyG stack | Superseded PyTorch 2.11 / PyG 0.6 family | Official CPU and PyG wheel indexes resolved for Linux/Python 3.11 | Core pair, PyG wheel index, and extension set move together | Superseded 2026-08-02 by the Torch 2.13 / PyG 0.8 family below |
| Ray server/client protocol | Server images `2.56.0`; clients `>=2.56.0,<2.57` | Multi-architecture CPU/GPU image manifests available | Compose defaults, service manifest, generated env, Backend, and Jupyter clients share one minor line | Fragment, Ray configuration, env assembler, and security-floor tests pass |
| Airflow core/providers | Core image `3.3.2`; independently resolved current providers | Airflow 3.3.2 (PyPI, 2026-09-17) is the maintained release and publishes amd64/arm64 images; 3.3.1 carries PYSEC-2026-3988, -3989 and -3990, none of which PyPI reports against 3.3.2 | Official constraints install core in the base image; later provider install must not reapply core constraints | Linux/Python 3.13 locks match on both architectures with Spark 4.1.2. See [provider-graph evidence](#21-airflow-provider-graph-evidence) for the 3.3.2 refresh and remaining live check. |
| Parakeet GPU model stack | NeMo `>=2.7.3,<3`, ONNX `>=1.22`, Protobuf `>=5.29.6,<5.30`, Transformers `4.57.x` | NeMo 2.7.x still requires Transformers below 4.58 | Patched NeMo/model-file/protobuf floors resolve; remaining Transformers advisories require an operator-selected malicious model artifact | Request payloads cannot select `PARAKEET_MODEL`; compatibility exception is documented in requirements and `SECURITY.md` |
| Python package security floors | Click 8.4.2, Pillow 12.3.0, Requests 2.34.2, python-multipart 0.0.31/0.0.32 | OSV/PyPI audit run against every shipped requirements surface and both committed uv lockfiles | Patchable findings are upgraded; residual findings are classified by concrete code path and platform | Bootstrapper audit is clean; service exceptions are recorded in `SECURITY.md` and guarded by focused tests |
| DiskCache transitive advisory | `diskcache==5.6.3` via Ragas and Instructor | CVE-2025-69872 / GHSA-w8v5-vhqr-4h9v has no patched release as of 2026-07-14 | Backend Ragas code imports no disk-cache adapter and creates no attacker-writable cache directory; the vulnerable deserialization path is unreachable | Retain only as a documented exception; `test_ragas_advisory_surface_remains_unreachable` guards the code-path assumption |
| vLLM Metal managed host installer | Plugin `0.3.0.dev20260713103604`; core `0.24.0` | Latest signed GitHub release on 2026-07-13; upstream installer builds vLLM core 0.24.0 then installs the release wheel | Atlas now mirrors the upstream two-stage install using checksum-pinned release assets and verifies the API server import before recording success | Manager unit tests guard exact URLs, hashes, stale-environment reconciliation, and complete process-group shutdown |
| Docker Compose modular `include` floor | `>=2.20.3` | Docker's Compose file reference identifies 2.20.3 as the first release supporting top-level `include` | Startup must reject 2.20.0–2.20.2 before loading Atlas' modular Compose graph | `DockerManager` and preflight tests enforce 2.20.3; operator docs use the same floor |
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

The final image's real `pip check` and vulnerability gate run in CI's final-image job. A live bring-up on 3.3.2 remains tracked in #1276.

On 2026-10-28 the overlay also began upgrading the base's Snowflake connector. The 3.3.2 constraints ship `snowflake-connector-python` 4.7.3, which requires `filelock<4,>=3.5`.

The overlay now requires Snowflake 4.8.1 (`<5`), which accepts `filelock<5,>=3.12`. Its former `filelock<4` cap therefore became `filelock>=4.2.1,<5`. The lock pins Snowflake 4.8.1 and filelock 4.2.1.

virtualenv 21.14.10 requires `filelock>=3.24.2,<5`. Snowflake provider 6.17.0 requires connector 3.17.0 or later. `test_airflow_snowflake_repair_retains_reviewed_base_compatibility` guards these ranges.

The overlay also moves OpenTelemetry to 1.45.1 and its beta packages to 0.66b1. The base constraints ship `opentelemetry-exporter-prometheus` 0.65b0, which rejects that family. The overlay therefore pins exporter 0.66b1.

These ranges come from PyPI metadata and the `constraints-3.3.2` file, rechecked on 2026-10-29. The final image's `pip check` and a live bring-up remain tracked in #1276.

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
| n8n workflow/package lifecycle | `n8nio/n8n:2.28.2`, local image ID `sha256:d609c3f5…71abe` | `import:workflow` accepts `--input` and `--activeState=fromJson`; `publish:workflow` accepts `--id`. Community packages are installed with committed `npm ci --omit=dev --ignore-scripts`; Atlas does not consume an n8n package-management REST endpoint. | Executed both pinned-image CLI help paths on 2026-08-03. Existing executable workflow/import tests and the committed npm lock guard the remaining contract. |
| Airflow initialization | `apache/airflow:3.3.1`, local image ID `sha256:c16ea5f5…03afe` | `airflow db migrate`; `airflow users create` with username/name/role/email/password; and `airflow connections add` with the connection ID and typed connection flags all exist in the pinned image | The 3.3.1 CLI help paths were executed; the 3.3.2 change was checked statically. See [initialization evidence](#43-airflow-initialization-evidence) and the remaining live check in #1276. |
| Docker Compose orchestration | Docker Compose `5.1.4`; Atlas minimum `2.20.3` | Top-level `include`, `--env-file`, `config -q`, JSON config/ps output, explicit service targets, and project-name placement | The local exact binary passed the full Compose configuration/source-permutation suite; startup separately enforces the documented minimum before using modular includes. |
| Local Deep Researcher aiohttp floor | `aiohttp>=3.14.3` in the combined lock inputs; resolved `3.14.3` | CVE-2026-59881 and CVE-2026-69243 are fixed in 3.14.2; CVE-2026-69244 is fixed in 3.14.3 | The lock refresher now applies the patched floor over the manifest-pinned upstream graph; byte-equivalence and the repository-wide vulnerability audit guard it. |

### 4.1. MinIO client provisioning evidence

Each pinned-image `--help` path was executed on 2026-08-03. These match the commands and flags in `services/minio/init/scripts/init-minio.sh`; the previous migration deferral is closed.

On 2026-09-28, `quay.io/minio` had stopped serving anonymous pulls. The `mc` binary from the pinned `pgsty/mc` image ran the unmodified `init-minio.sh` twice against the pinned Silo server. The first run created 16 buckets and 13 scoped service accounts. The second edited all 13 accounts.

boto3 probes confirmed read-write, read-only and no-access scoping, multipart upload and presigned GET. A scoped key's attempt to create a service account was denied, covering the CVE-2025-62506 escalation. Live stack checks remain tracked in #1278.

On 2026-10-03 (#1288), `services/jenkins` and `services/backup` moved onto this same digest-pinned image. They left the abandoned official `minio/mc` release `RELEASE.2025-08-13T08-35-41Z` (Go 1.24.6, 43 HIGH/CRITICAL scanner rows).

Both Dockerfiles use `COPY --from` to copy statically linked `/usr/bin/mc` to `/usr/local/bin/mc`. The binary reports `mc version RELEASE.2026-09-16T00-00-00Z` and Go 1.27.1. SHA-256 values re-extracted from the index that day are `1e745aaf…86b8` on amd64 and `d89a6daf…48fa` on arm64.

The backup entrypoint checks the committed checksums and version at every start. It fails closed instead of downloading. A Trivy 0.74.0 scan of that binary on both platforms reports no HIGH/CRITICAL finding.

### 4.2. MinIO server evidence

MinIO stopped publishing community images in October 2025 and deleted `minio/minio` from Docker Hub on 2026-09-11. Since 2026-09-24, `quay.io/minio` has returned 401 to anonymous pulls. Silo was adopted as an actively released AGPL fork with dated multi-arch tags.

On 2026-09-28, probes used the binary extracted from the pinned amd64 image. Health, metrics and console each returned 200.

The CVE-2026-39414 fix is present in source: `internal/s3select/csv/reader.go` bounds each record at 1 MiB. Trivy's pseudo-version match is therefore excepted as a false positive.

An in-place upgrade of an existing `RELEASE.2025-09-07` volume remains tracked in #1278. The same issue tracks Grafana panel metrics that appear only after traffic. The long-term server choice remains tracked in #1277.

### 4.3. Airflow initialization evidence

All three pinned-image CLI help paths were re-executed against `3.3.1` on 2026-08-22. These were `db migrate`, `users create` with all six flags, and `connections add` with all eight typed flags. Init-script tests guard the exact flags and idempotent delete/add sequence.

The 2026-09-27 move to `apache/airflow:3.3.2` was checked statically, without Docker. Its platform manifests were `linux/amd64@sha256:6f870ad5…` and `linux/arm64@sha256:0a56a5db…`.

A wheel diff of `apache-airflow-core` 3.3.1 against 3.3.2 leaves the relevant arguments in `cli/cli_config.py` unchanged. Those arguments belong to `db migrate` and `connections add`. The `users create` command comes from `apache-airflow-providers-fab==3.7.3`, which the lock keeps.

The `connections add` command now prints success as a table with redacted extras instead of a URI. The init script does not parse that output.

Re-executing the help paths against the pulled 3.3.2 image remains tracked in #1276. That check must also record its local image ID.

## 5. Reviewed Deferrals

| Gap | Interim controls | Owner | Review trigger / deadline |
|---|---|---|---|
| Four final-image builds outside the hosted scanner profile | Manifest bases and remote Dockerfile/base-index drift are checked; excluded final contents are not fully scanned. See [residual coverage](#51-final-image-exclusion-coverage). | Atlas maintainers | Resolve or renew the residual final-image gap with evidence by 2026-11-30 (renewed from 2026-09-15 with both rationales re-verified; the `expired_at` dates in `.container-scan-exclusions.yml` and CHANGELOG.md record it); re-open immediately if any excluded build becomes a published Atlas artifact. |
| Parakeet GPU fsspec and Hydra advisories under time-boxed exceptions | `services/parakeet/provider/gpu/requirements-locked.txt` keeps fsspec 2024.12.0 (PYSEC-2026-4217 / GHSA-27vj-qcqg-25rc, fixed in 2026.6.0). It also keeps hydra-core 1.3.2 (PYSEC-2026-4221 / GHSA-c3wx-c55w-pxjq, fixed in 1.3.6). NeMo 3.0.0 admits only `hydra-core>1.3,<=1.3.2` and `lightning>2.2.1,<=2.4.0`. Lightning 2.4.0 requires `fsspec[http]<2026.0`. Atlas restores only the operator-selected `PARAKEET_MODEL`; this is not a reachability review. The owner approved time-boxed exceptions for both IDs on 2026-10-29, in `scripts/audit_runtime_locks.py`. | Atlas maintainers | Re-review by 2026-11-27, and at each NeMo release; 3.0.0 was the newest on 2026-10-29. Remove the exceptions on an upstream cap lift. |
| n8n community-node Zod advisory under a time-boxed exception | `services/n8n/init/config` resolves zod 3.25.76 (GHSA-mrhx-c7xc-m3w5, fixed in 4.6.6). `n8n-workflow` 2.45.1 and `@n8n/expression-runtime` 0.36.1 pin zod 3.25.76 exactly. `@n8n/expression-runtime` 0.36.2, the newest on 2026-10-29, still pins it. Reachability through the community nodes is not proven. The owner approved a time-boxed exception on 2026-10-29. | Atlas maintainers | Re-review by 2026-11-28, and at each `n8n-workflow` bump. Remove the exception on an upstream move to Zod 4. |

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
| Parakeet startup supervised restart | `services/parakeet/provider/startup.py` `ModelStartup` | Reviewed 2026-08-05 against the documented "fatal timeouts return 504 before the provider process exits for supervised restart" contract | The generic-exception branch previously left the container alive returning 503 on transient loader failures; it now mirrors the deadline branch and terminates for supervised restart | Pinned by `test_startup_loader_failure_is_generic_unhealthy_and_terminates`; see CHANGELOG §1.147 |
| Bounded-subprocess parent-loss + deadline | `bootstrapper/core/process_runner.py` | Reviewed 2026-08-05; the reaper now bounds its post-SIGKILL wait and the cleanup contextmanager intercepts SIGHUP | A D-state leader can no longer wedge the deadline path, and `start_new_session=True` children are reaped on terminal close / SSH disconnect | Full `test_process_runner.py` + `test_bounded_subprocess.py` (153 tests) pass; see CHANGELOG §1.147 |

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
| LLM Graph Builder remote base images | Upstream source commit `4a412f4688cf4096976045c019edc0a7f6ddcb6b`; `python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159`; `node:20@sha256:8f693eaa7e0a8e71560c9a82b55fd54c2ae920a2ba5d2cde28bac7d1c01c9ba5`; `nginx:alpine@sha256:785835ea65db4bba6f021acc0f3be91b0fef76a53fe41f8588c6698d2aed0606` | The pinned Dockerfiles and all index moves were reviewed. See [dated base-image evidence](#72-llm-graph-builder-base-image-evidence) for content changes, unchanged platforms and the package-closure limitation. | The reviewed baseline must exactly equal the three literal bases discovered from the two pinned Dockerfiles; every nightly check resolves their current multi-platform index digests and fails closed on added, removed, or changed bases. | `.container-scan-exclusions.yml` records only the reviewed registry indexes. `bootstrapper/tests/test_container_security.py` pins the reviewed fixture and ledger evidence, while `scripts.upstream_drift_watch` owns bounded live source and registry verification. |

### 7.1. Container image vulnerability gate evidence

The reviewed inventory contains:

- All 72 manifest-owned image defaults.
- 2 Dockerfile-only build inputs.
- 3 digest-qualified bases from commit-pinned remote build contexts.

Inputs are attempted on both `linux/amd64` and `linux/arm64` unless the manifest declares an explicit single architecture. This is currently producing 147 isolated image-platform scans. Scheduled scan failures expose unsupported registry variants instead of silently omitting them.

Required CI fetches and parses the pinned remote Dockerfiles, verifies their reviewed base digests, and scans those bases when remote inputs change. It also builds and scans every hosted-runner-supported local runtime image, then immediately removes it.

Local final-image scans fail every fixable High/Critical finding. Vendor-unfixed findings remain visible in the scheduled complete scan and automatically become gating when a fix appears.

A fixable finding may be deferred only by an exact package/path exception. The exception needs a substantive reason and a review horizon of at most 90 days.

The executable inventory, remote-source contract and reviewed final-build exclusions are provided by:

- `.github/workflows/container-security.yml` and `.github/workflows/services-lint.yml`.
- `.trivyignore.yaml` and `.container-scan-exclusions.yml`.
- `scripts.container_security` and `scripts.upstream_drift_watch`.

The former multi-architecture deferral is closed by explicit dual-platform attempts, not registry discovery. The scheduled matrix is capped at four concurrent jobs. Required builds reclaim each loaded image and its BuildKit cache before proceeding, bounding runner disk use.

### 7.2. LLM Graph Builder base-image evidence

The pinned column above retains the reviewed source commit and three base-image indexes. The following record preserves each dated review; it adds no new registry or vulnerability observation.

#### 7.2.1. 2026-09-02 — Python index and Nginx content

Both commit-pinned upstream Dockerfiles were fetched from `neo4j-labs/llm-graph-builder`. Their literal `FROM` instructions were re-read on 2026-09-02 UTC.

Docker Hub index inspection initially found only the Python channel had moved. Its index identified Python `3.12.14-slim-trixie` from `docker-library/python@688a0b86bb44289df16a363e9f41d90514c1a5f9` on the hosted scan architectures. The reviewed platform manifests were:

- `linux/amd64@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`.
- `linux/arm64/v8@sha256:950206c37262dd86c55659797f6ee418fee30535072f65a82ed470d985f5cda5`.

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

- `linux/amd64`: `sha256:2fe5997d...` to `sha256:44ff437b...`.
- `linux/arm64/v8`: `sha256:3949e427...` to `sha256:950206c3...`.

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

## 8. 2026-09-20 TrueForge Adoption (#1159)

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| TrueForge agent-runtime image | `tfy.jfrog.io/tfy-images/trueforge:0.2.0-8b1d98e` (index digest `sha256:db9de64e1ff354a858dc646f23cf16c70e300c457acb86527e4a0b1cd556f573`, single-platform `linux/amd64` OCI image manifest) | `docker buildx imagetools inspect` on 2026-09-20; upstream GitHub releases re-checked the same day — `@truefoundry/trueforge@0.2.0` (2026-09-18) is the newest release, so the pin is current at adoption | The adoption review found 25 unique HIGH findings and no CRITICAL findings. See [adoption evidence](#81-trueforge-adoption-evidence) for platform, reachability and expiry qualifications. | `services/trueforge/service.yml` (pin + platform), `.trivyignore.yaml` (25 purl-scoped entries with per-cluster reachability statements), `scripts.container_security` schema/expiry/duplicate gates, and the required "Scan changed manifest-owned remote images" CI step. Controller and init containers reuse this same image (no additional remote contract). |


### 8.1. TrueForge adoption evidence

Anonymous pull from `tfy.jfrog.io` succeeds without credentials. The tag embeds the build hash, making it effectively immutable. The recorded digest is the review reference for the next bump.

The image is amd64-only; the manifest declares `platform: "linux/amd64"`. The required remote-image scan therefore attempts exactly one platform, following the Chatterbox/Docling precedent. It runs emulated on Apple Silicon.

A pinned Trivy 0.74.0 scan reports 25 unique HIGH findings and 0 CRITICAL findings. The Debian 12 base findings comprise:

- Five util-linux advisories across eight base packages.
- Seven perl-base advisories.
- zlib1g's MiniZip CVE, ncurses, systemd libs, gzip and libacl1.

Those findings have no fixed Debian 12 package. Three additional libpcre2 findings have a Debian fix (`10.42-1+deb12u1`) but await an upstream image rebuild (the #1002 wave).

Three upstream-bundled node_modules packages need fixes from the next TrueForge release: brace-expansion 5.0.7, ip-address 10.2.0 and node-tar 7.5.19.

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
On 2026-10-29 the registered online audit ran against the regenerated lock.
It covered all 115 projected package/version pairs for both container architectures and reported no advisory.
This is dated evidence, not a future advisory guarantee.
No advisory exceptions were added.
