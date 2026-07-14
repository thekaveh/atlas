# External Dependency Contract Ledger

Durable ledger for maintenance checks against consumed external contracts, as
required by the overnight maintenance spec's external-dependency contract
section. Each row records what was checked, the pinned or configured version,
the current/latest status observed during the pass, and the evidence source.

## 1. 2026-07-02 Maintenance Pass

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| Docker Compose `include:` support | Minimum v2.20.0, recommended v2.26.0 | Existing `DockerManager.check_compose_version()` parses `docker compose version --short` | Compose version floor is now enforced during startup preflight before the modular `include:` compose file is used | Guarded by `bootstrapper/tests/test_start_dependency_preflight.py` |
| scikit-learn requirement in backend | `scikit-learn>=1.9.0` | PyPI reports 1.9.0 as the latest release, released 2026-06-02 | Requirement is valid for the Python 3.12 backend image, but host Python 3.10 cannot collect it | Do not lower the requirement for host-tool compatibility; run audits with the target Python version |
| JupyterHub Python requirements | `torch==2.4.1`, `nltk` unpinned | `pip-audit 2.10.1` under host Python 3.10 collected the requirements and reported 22 known vulnerabilities across `torch 2.4.1` and `nltk 3.9.4` | Vulnerability status only; no automatic upgrade attempted because PyTorch/PyG wheels are image-coupled | Resolved 2026-07-14 by the coordinated PyTorch 2.11/PyG migration and NLTK 3.10 floor documented below |
| MinIO client service-account commands | `minio/mc:RELEASE.2025-08-13T08-35-41Z` | MinIO current docs center `mc admin accesskey`; legacy `mc admin user svcacct` remains a replacement-bound contract to verify before future client bumps | `services/minio/init/scripts/init-minio.sh` still uses `mc admin user svcacct` create/edit/list | Deferred pending live MinIO init validation or deliberate migration to `mc admin accesskey` |
| n8n community package load path | `n8nio/n8n:2.28.2` | Self-hosted n8n loads manually installed community packages from `/home/node/.n8n/nodes/node_modules`; the image ships Node 24 and `n8n-workflow` 2.28.1 | Atlas runs the same pinned n8n image as a pre-start init, installs a committed npm lock with scripts disabled into the shared user folder, and blocks n8n startup on failure; custom specs must be exact versions | Guarded by `bootstrapper/tests/test_shell_script_contracts.py`, Compose byte-equivalence, and live pinned-image startup validation |

## 2. 2026-07-14 Maintenance Pass

| Integration point | Pinned/configured version | Latest/status check | Contract checked | Evidence / disposition |
|---|---:|---|---|---|
| JupyterHub Python/PyG stack | PyTorch `2.11.0`, torchvision `0.26.0`, torchaudio `2.11.0`, `pyg_lib==0.7.0` | Official CPU and PyG wheel indexes resolve for Linux/Python 3.11 | Core trio, PyG wheel index, and extension set move together; obsolete `torch-spline-conv` is removed | Requirements compile and `test_security_dependency_floors.py` guard the coupled versions |
| Ray server/client protocol | Server images `2.56.0`; clients `>=2.56.0,<2.57` | Multi-architecture CPU/GPU image manifests available | Compose defaults, service manifest, generated env, Backend, and Jupyter clients share one minor line | Fragment, Ray configuration, env assembler, and security-floor tests pass |
| Airflow core/providers | Core image `3.3.0`; independently resolved current providers | Airflow 3.3.0 is the maintained release and publishes amd64/arm64 images | Official constraints install core in the base image; later provider install must not reapply core constraints | Linux/Python 3.12 provider graph resolves with Spark 4.1.2 and audits without known vulnerabilities |
| Parakeet GPU model stack | NeMo `>=2.7.3,<3`, ONNX `>=1.22`, Protobuf `>=5.29.6,<5.30`, Transformers `4.57.x` | NeMo 2.7.x still requires Transformers below 4.58 | Patched NeMo/model-file/protobuf floors resolve; remaining Transformers advisories require an operator-selected malicious model artifact | Request payloads cannot select `PARAKEET_MODEL`; compatibility exception is documented in requirements and `SECURITY.md` |
| Python package security floors | Click 8.4.2, Pillow 12.3.0, Requests 2.34.2, python-multipart 0.0.31/0.0.32 | OSV/PyPI audit run against every shipped requirements surface and both committed uv lockfiles | Patchable findings are upgraded; residual findings are classified by concrete code path and platform | Bootstrapper audit is clean; service exceptions are recorded in `SECURITY.md` and guarded by focused tests |
| DiskCache transitive advisory | `diskcache==5.6.3` via Ragas and Instructor | CVE-2025-69872 / GHSA-w8v5-vhqr-4h9v has no patched release as of 2026-07-14 | Backend Ragas code imports no disk-cache adapter and creates no attacker-writable cache directory; the vulnerable deserialization path is unreachable | Retain only as a documented exception; `test_ragas_advisory_surface_remains_unreachable` guards the code-path assumption |
| vLLM Metal managed host installer | Plugin `0.3.0.dev20260713103604`; core `0.24.0` | Latest signed GitHub release on 2026-07-13; upstream installer builds vLLM core 0.24.0 then installs the release wheel | Atlas now mirrors the upstream two-stage install using checksum-pinned release assets and verifies the API server import before recording success | Manager unit tests guard exact URLs, hashes, stale-environment reconciliation, and complete process-group shutdown |

## 3. Open Ledger Gaps

- Add a CI-supported dependency vulnerability audit that can run under each
  target Python version and produce an allowlisted report.
- Record exact external CLI contracts for MinIO `mc`, n8n community package
  REST endpoints, Airflow CLI commands, and Docker Compose as those paths are
  live-smoked or upgraded.
- Add image vulnerability scanning or a documented exception process for
  intentionally rolling image tags.
