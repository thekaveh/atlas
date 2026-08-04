# Provider Hardening and LightRAG–Docling Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require fail-closed authentication and bounded, killable work at the Docling and Parakeet provider boundaries while restoring the exact asynchronous Docling API expected by pinned LightRAG v1.5.4 through an isolated adapter.

**Architecture:** Both provider applications install a small ASGI boundary before FastAPI parses multipart bodies. The boundary authenticates, reserves non-blocking capacity for expensive routes, and keeps the permit through cleanup; provider handlers run model work under a finite deadline and terminate the process only after returning a generic 504 when native work cannot be cancelled. A lightweight, isolated adapter implements LightRAG's submit/poll/result protocol, delegates one authenticated conversion to Docling's internal bundle route, and bounds queued uploads plus retained results.

**Tech Stack:** Python 3.10+, FastAPI/Starlette, httpx2, Docling 2.102.1, NVIDIA NeMo / parakeet-mlx, Docker Compose, Atlas manifest synthesis, pytest, uv, MkDocs and Atlas documentation generators.

## 1. Global Constraints

- Preserve existing synchronous Docling and OpenAI-compatible Parakeet endpoint paths and successful response bodies.
- Keep `/health` public. Require bearer authentication on every other provider route unless the corresponding `*_AUTH_MODE=disabled` is explicitly configured.
- Reserve expensive-route capacity before `call_next(request)` and therefore before multipart parsing, upload spooling, model loading, or inference.
- Do not claim that cancelling an asyncio future kills native work. A deadline response must schedule the fatal process exit after the response is sent.
- Never log provider tokens, filenames, paths, uploaded content, upstream response bodies, or raw exception text.
- Preserve the current model pins, output quality settings, UI output, and concurrency defaults.
- Do not hand-edit generated dependency sections, SVG/HTML diagrams, `.env.example`, MkDocs output, wiki output, or PNG derivatives. Change source manifests/docs and run the repository generators.
- Use `apply_patch` for source edits and make the smallest coherent commit after each green task.
- Run every task's focused failing test before implementation and record the expected failure in the work log.
- Keep all work on `codex/overnight-maintenance-2026-08-02`; do not create or switch branches or worktrees.

## 2. File and Responsibility Map

### 2.1. New provider boundary modules

- `services/docling/provider/provider_boundary.py`: Docling copy of the shared boundary contract, importable by both container and localhost applications.
- `services/parakeet/provider/provider_boundary.py`: Parakeet copy of the same contract; the byte-equivalence test prevents drift across separate Docker build contexts.
- `bootstrapper/tests/test_provider_boundary.py`: executable ASGI contracts using a fake downstream app and a body-read sentinel.
- `bootstrapper/tests/test_provider_boundary_source_parity.py`: exact-byte parity test for the two service-local modules.

The module must expose these stable interfaces:

```python
@dataclass(frozen=True)
class BoundarySettings:
    service_name: str
    token: str
    auth_mode: Literal["required", "disabled"]
    capacity: int
    expensive_paths: frozenset[str]
    cors_origins: tuple[str, ...]

def load_boundary_settings(prefix: str, expensive_paths: Collection[str]) -> BoundarySettings: ...
def install_provider_boundary(app: FastAPI, settings: BoundarySettings) -> None: ...
def parse_timeout_seconds(prefix: str, *, default: int = 900) -> int: ...
def fatal_timeout_response(prefix: str, terminate: Callable[[int], None] = os._exit) -> Response: ...
async def run_with_deadline(prefix: str, operation: Callable[[], T]) -> T: ...
```

`install_provider_boundary` owns a `threading.BoundedSemaphore(settings.capacity)`. For an expensive `POST`, it calls `acquire(blocking=False)` before the downstream ASGI application, returns 429 plus `Retry-After: 1` when full, and releases exactly once in `finally`. Authentication happens before admission. CORS is added only for the parsed explicit allowlist, with `allow_credentials=False`; wildcard is invalid in required mode.

### 2.2. Docling conversion and bundle modules

- `services/docling/provider/gpu/processor.py`: expose conversion and rendering separately so one `ConversionResult` can produce the existing response or a LightRAG bundle.
- `services/docling/provider/localhost/processor.py`: mirror the same public processor interfaces for native mode.
- `services/docling/provider/lightrag_bundle.py`: canonical name selection, referenced Markdown/JSON export, and traversal-safe zip construction shared by the GPU build context and native server.
- `services/docling/provider/shared/api_server.py`: boundary installation, deadline use, and authenticated bundle route.
- `services/docling/provider/localhost/server.py`: equivalent native API behavior and configurable loopback bind host.
- `bootstrapper/tests/test_docling_lightrag_bundle.py`: fake-Docling unit tests for one conversion and safe zip contents.
- Existing `bootstrapper/tests/test_docling_pipeline_contract.py`, `test_provider_upload_limits.py`, and `test_provider_public_errors.py`: extend source contracts where runtime dependencies are impractical.

Required processor seam:

```python
async def convert_document_once(file_path: str, *, use_ocr: str, table_mode: str) -> Any: ...
def render_conversion(result: Any, *, output_format: str, enable_chunking: bool,
                      chunk_size: int, chunk_overlap: int) -> ConversionResponse: ...
def build_lightrag_bundle(result: Any, *, upload_name: str) -> bytes: ...
```

The bundle route is `POST /internal/lightrag/bundle`. It is not exempt from provider authentication and is included in Docling's expensive paths. It converts once, exports `<canonical-stem>.json`, `<canonical-stem>.md`, and referenced assets, and returns `application/zip`.

### 2.3. Parakeet lifecycle modules

- `services/parakeet/provider/shared/api_server.py`: boundary, deadline, readiness, and non-blocking lifespan startup.
- `services/parakeet/provider/gpu/transcribe.py`: remove import-time preload and expose an idempotent loader callable.
- `services/parakeet/provider/mlx/api_server.py`: same boundary/deadline/lifespan behavior for native MLX.
- `services/parakeet/provider/mlx/api_server.py`: use the same loopback bind and boundary contract; this is the native Parakeet entry point.
- `bootstrapper/tests/test_parakeet_startup_deadline.py`: fake loader and injectable terminator contracts.
- Existing `bootstrapper/tests/test_parakeet_mlx_contract.py`: update source-level invariants.

Required lifecycle states are `loading`, `healthy`, and `unhealthy`. Startup schedules loading without blocking socket startup; `/health` returns 503 until healthy. The watchdog uses the same validated inference timeout and calls the fatal terminator if loading does not settle.

### 2.4. LightRAG adapter

- `services/docling/provider/adapter/app.py`: FastAPI routes and application wiring.
- `services/docling/provider/adapter/jobs.py`: bounded job registry, background execution, TTL cleanup, and artifact ownership.
- `services/docling/provider/adapter/upstream.py`: authenticated Docling call with bounded 429 retry and generic errors.
- `services/docling/provider/adapter/requirements.txt`: pinned minimal runtime dependencies.
- `services/docling/provider/adapter/Dockerfile`: minimal pinned Python image, non-root runtime user, curl health probe dependency only if required.
- `services/docling/provider/adapter/__init__.py`: package marker.
- `bootstrapper/tests/test_docling_lightrag_adapter.py`: exact submit/poll/result protocol with an ASGI fake upstream.
- `bootstrapper/tests/test_docling_adapter_cleanup.py`: job-capacity, download, failure, and TTL cleanup.

The route responses must match pinned LightRAG v1.5.4:

```text
POST /v1/convert/file/async       -> {"task_id": "<random>"}
GET  /v1/status/poll/{task_id}    -> {"task_id": "...", "task_status": "pending|started|success|failure"}
GET  /v1/result/{task_id}         -> application/zip when successful
GET  /health                      -> public readiness
```

`JobRegistry.reserve()` must be callable before multipart parsing and non-blocking. Ownership transfers from the request middleware to a newly registered job only after successful spooling. Successful download, job failure, cancellation, and TTL expiry remove files and release the outstanding-job slot exactly once.

### 2.5. Configuration, consumers, and generated surfaces

- `bootstrapper/utils/key_generator.py` and `bootstrapper/tests/test_provider_token_generation.py`: stable provider-token generation.
- `services/docling/service.yml`, `services/docling/compose.yml`: provider and adapter vars, container, networks, source behavior, and data flow.
- `services/parakeet/service.yml`, `services/parakeet/compose.yml`: provider vars and loopback bind.
- `bootstrapper/services/service_config.py`: adapter scale/endpoint derivation and token propagation.
- `bootstrapper/tests/test_service_config.py` and source-permutation tests: adapter/container/localhost/disabled behavior.
- `services/backend/app/app/document_extraction.py` and its tests: Docling bearer header.
- `services/backend/compose.yml`, `services/backend/service.yml`: provider secrets available server-side.
- `services/open-webui/compose.yml`, `services/open-webui/service.yml`: Parakeet token as STT API key only for Parakeet modes.
- `services/hermes/init/` templates/scripts plus manifest/compose tests: render Parakeet token in the server-side STT block.
- `services/n8n/compose.yml`, `services/n8n/service.yml`, `services/jupyterhub/compose.yml`, `services/jupyterhub/service.yml`: server-side tokens.
- `bootstrapper/tests/fixtures/rendered_config_baseline.yml`: regenerate through its owning helper, never edit manually.
- Provider and consumer READMEs named in the approved design, service manifests' `data_flow.calls`, and generated diagrams/surfaces.

## 3. Task 1 — Add Executable Provider Boundary Contracts

- [ ] Add repo-managed test dependencies to `bootstrapper/pyproject.toml`: `fastapi==0.141.1`, `python-multipart==0.0.32`, and `httpx2>=2.6.0`; run `uv lock --project bootstrapper` so `bootstrapper/uv.lock` remains the source of truth. FastAPI 0.141.1 is the current Parakeet lock and exercises the Docling application's declared compatible API range.
- [ ] Write `bootstrapper/tests/test_provider_boundary.py` first. Use a tiny FastAPI application and an ASGI receive wrapper that records any body read.
- [ ] Cover public `/health`, missing/incorrect/non-ASCII bearer tokens, fail-closed empty token, explicit disabled mode, malformed auth mode, invalid capacity, invalid timeout (`0`, `3601`, non-integer, infinity-like text), and explicit CORS parsing.
- [ ] Hold the sole permit with one request, issue a second request whose receive callable raises if invoked, and assert 429 plus `Retry-After: 1` without body access.
- [ ] Cover exact permit release after success, 4xx validation, downstream exception, and cancellation/disconnect.
- [ ] Verify a fake timed-out operation produces generic 504 JSON and that its Starlette background task calls an injected terminator with the dedicated exit code only after the response is consumed.
- [ ] Run the failing contract:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_provider_boundary.py -q
```

Expected: collection fails because `services/docling/provider/provider_boundary.py` does not exist.

- [ ] Implement `services/docling/provider/provider_boundary.py` with no provider-specific imports and copy it byte-for-byte to `services/parakeet/provider/provider_boundary.py`.
- [ ] Add the parity test, including an assertion that both provider Docker build contexts copy the module into their runtime image.
- [ ] Run focused tests and confirm all pass:

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_provider_boundary.py \
  bootstrapper/tests/test_provider_boundary_source_parity.py -q
```

- [ ] Commit:

```bash
git add bootstrapper/pyproject.toml bootstrapper/uv.lock \
  bootstrapper/tests/test_provider_boundary.py \
  bootstrapper/tests/test_provider_boundary_source_parity.py \
  services/docling/provider/provider_boundary.py \
  services/parakeet/provider/provider_boundary.py
git commit -m "feat: add hardened provider request boundary"
```

## 4. Task 2 — Generate and Declare Stable Provider Credentials

- [ ] Add failing tests in `bootstrapper/tests/test_provider_token_generation.py` for `DOCLING_API_TOKEN` and `PARAKEET_API_TOKEN`: generated when absent, non-empty and URL-safe, different from each other, preserved on warm runs, never force-rotated, and written to a custom `ATLAS_ENV_FILE`.
- [ ] Add failing manifest/env-assembler assertions that both vars are `secret: true`, have empty `.env.example` placeholders, and never appear in diagnostic exports.
- [ ] Run:

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_provider_token_generation.py \
  bootstrapper/tests/test_env_assembler.py \
  bootstrapper/tests/test_manifests.py -q
```

Expected: provider token assertions fail because the generators and manifest declarations are absent.

- [ ] Add a generic `generate_provider_api_token(provider: str)` helper returning `sk-atlas-<provider>-<random>` and idempotent `generate_and_update_*` wrappers in `bootstrapper/utils/key_generator.py`.
- [ ] Call both wrappers unconditionally from `generate_missing_keys(force_regenerate=...)` with preservation semantics independent of whether the providers are currently enabled.
- [ ] Declare the secrets and configuration contract in the owning manifests:

```yaml
- name: DOCLING_API_TOKEN
  default: ""
  secret: true
- name: DOCLING_AUTH_MODE
  default: required
- name: DOCLING_CORS_ORIGINS
  default: ""
- name: DOCLING_INFERENCE_TIMEOUT_SECONDS
  default: 900
- name: DOCLING_LOCALHOST_BIND_HOST
  default: 127.0.0.1
```

Add the analogous Parakeet variables and adapter variables from the design.

- [ ] Regenerate `.env.example` with `uv run --project bootstrapper python -m services.env_assembler`; verify both token values remain blank.
- [ ] Re-run the focused tests and commit:

```bash
git add bootstrapper/utils/key_generator.py bootstrapper/tests/test_provider_token_generation.py \
  bootstrapper/tests/test_env_assembler.py bootstrapper/tests/test_manifests.py \
  services/docling/service.yml services/parakeet/service.yml .env.example
git commit -m "feat: generate provider API credentials"
```

## 5. Task 3 — Harden Docling and Produce a One-Conversion Bundle

- [ ] Extend `bootstrapper/tests/test_provider_upload_limits.py` and `test_provider_public_errors.py` so both Docling entry points must install the boundary before routes, list `/v1/document/convert` and `/internal/lightrag/bundle` as expensive, and wrap model work in `run_with_deadline`.
- [ ] Write `bootstrapper/tests/test_docling_lightrag_bundle.py` with a fake conversion result exposing `save_as_json` and `save_as_markdown`. Assert each is called once from one result, the archive contains canonical relative names, and names such as `../../secret`, absolute paths, NUL bytes, duplicate normalized paths, and symlink-like assets are rejected or safely rewritten.
- [ ] Add an API test using a fake `convert_document_once` to assert the existing conversion response is unchanged and the internal route returns a valid zip under bearer auth.
- [ ] Run the failing tests:

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_docling_lightrag_bundle.py \
  bootstrapper/tests/test_provider_upload_limits.py \
  bootstrapper/tests/test_provider_public_errors.py \
  bootstrapper/tests/test_docling_pipeline_contract.py -q
```

Expected: missing bundle module/route and missing boundary/deadline assertions fail.

- [ ] Refactor both Docling processors without changing output semantics: conversion happens in `convert_document_once`; existing formatting/chunking happens in `render_conversion`; the old `process_document` may remain as a compatibility wrapper during this task.
- [ ] Implement `services/docling/provider/lightrag_bundle.py` using a private temporary directory and Docling's referenced-image export. Build the zip from an explicit allowlisted file walk under that directory; never follow symlinks; sort entries for deterministic tests.
- [ ] Install the boundary in both API entry points, remove wildcard CORS, and execute both expensive routes through `run_with_deadline`.
- [ ] On `ProviderDeadlineExceeded`, return `fatal_timeout_response("DOCLING")`; do not release the outer permit early or emit exception text.
- [ ] Make native uvicorn bind to `DOCLING_LOCALHOST_BIND_HOST`, default `127.0.0.1`.
- [ ] Update `services/docling/provider/gpu/Dockerfile` to copy `provider_boundary.py` and `lightrag_bundle.py`; do not alter its base-image pin.
- [ ] Run focused tests and a no-model import/compile check:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docling_lightrag_bundle.py \
  bootstrapper/tests/test_provider_upload_limits.py \
  bootstrapper/tests/test_provider_public_errors.py \
  bootstrapper/tests/test_docling_pipeline_contract.py -q
python -m compileall -q services/docling/provider
```

- [ ] Commit:

```bash
git add services/docling/provider bootstrapper/tests/test_docling_lightrag_bundle.py \
  bootstrapper/tests/test_provider_upload_limits.py \
  bootstrapper/tests/test_provider_public_errors.py \
  bootstrapper/tests/test_docling_pipeline_contract.py
git commit -m "feat: harden Docling conversion boundaries"
```

## 6. Task 4 — Harden Parakeet and Bound Startup Loading

- [ ] Write `bootstrapper/tests/test_parakeet_startup_deadline.py` using fake loader/transcriber callables. Assert startup is non-blocking, readiness is 503 while loading, readiness becomes 200 after success, one load runs, loader failure stays generic/unhealthy, and the timeout watchdog invokes an injected terminator.
- [ ] Extend Parakeet API tests to assert both transcription paths are authenticated/admitted before parsing and run model work under the configured deadline.
- [ ] Add a regression assertion that importing `gpu/transcribe.py` never loads a model, even when `PRELOAD_MODEL=true`.
- [ ] Run:

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_parakeet_startup_deadline.py \
  bootstrapper/tests/test_parakeet_mlx_contract.py \
  bootstrapper/tests/test_provider_upload_limits.py \
  bootstrapper/tests/test_provider_public_errors.py -q
```

Expected: startup and boundary assertions fail against the current import-time preload and open routes.

- [ ] Replace GPU import-time loading with an idempotent `load_model()` callable guarded by the existing single-flight lock.
- [ ] Add a lifespan-owned background startup task and readiness state in shared GPU and MLX APIs. Run it under the validated Parakeet deadline and schedule the watchdog fatal exit if it hangs.
- [ ] Install the provider boundary, remove wildcard CORS, wrap both transcription variants in `run_with_deadline`, and use `fatal_timeout_response("PARAKEET")` for native deadline expiry.
- [ ] Default native bind host to `PARAKEET_LOCALHOST_BIND_HOST=127.0.0.1`.
- [ ] Run the focused suite and compile check:

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_parakeet_startup_deadline.py \
  bootstrapper/tests/test_parakeet_mlx_contract.py \
  bootstrapper/tests/test_provider_upload_limits.py \
  bootstrapper/tests/test_provider_public_errors.py -q
python -m compileall -q services/parakeet/provider
```

- [ ] Commit:

```bash
git add services/parakeet/provider bootstrapper/tests/test_parakeet_startup_deadline.py \
  bootstrapper/tests/test_parakeet_mlx_contract.py \
  bootstrapper/tests/test_provider_upload_limits.py \
  bootstrapper/tests/test_provider_public_errors.py
git commit -m "feat: bound Parakeet startup and inference"
```

## 7. Task 5 — Implement the Exact LightRAG Async Adapter

- [ ] Copy the pinned LightRAG v1.5.4 client fixtures into test-owned minimal request/response expectations; cite tag commit `9a45b64c` in a test comment but do not vendor upstream implementation.
- [ ] Write `test_docling_lightrag_adapter.py` first. Cover multipart submit, random task IDs, state progression, successful zip download, unknown task 404, result-before-success conflict, generic upstream failure, authenticated upstream header, and bounded retry for upstream 429.
- [ ] Write `test_docling_adapter_cleanup.py`. Use a fake clock/temp root and assert reservation occurs before body reads, `DOCLING_ADAPTER_MAX_JOBS` is enforced, partial upload cleanup occurs, slots release once on failure/download/TTL, terminal failure records do not consume capacity, and expired results return 404.
- [ ] Run:

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_docling_lightrag_adapter.py \
  bootstrapper/tests/test_docling_adapter_cleanup.py -q
```

Expected: adapter package imports fail.

- [ ] Implement the registry with an `asyncio.Lock` for metadata, a non-blocking reservation counter, unguessable `secrets.token_urlsafe(24)` IDs, explicit file ownership, and an injected clock.
- [ ] Implement the upstream client with `httpx.AsyncClient`, `Authorization: Bearer`, total job deadline, short bounded backoff for 429, response streaming to a private file, a response-size ceiling, and generic mapped failures.
- [ ] Implement only the four approved routes. Keep `/health` public; the dedicated network is the access boundary for LightRAG-facing routes. Reject excess submissions before multipart parsing.
- [ ] Add a minimal pinned requirements file and Dockerfile. Run as a non-root user and store artifacts only under a private runtime temp directory.
- [ ] Re-run focused tests, dependency compile/import checks, and build the adapter image:

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_docling_lightrag_adapter.py \
  bootstrapper/tests/test_docling_adapter_cleanup.py -q
python -m compileall -q services/docling/provider/adapter
docker build -f services/docling/provider/adapter/Dockerfile \
  services/docling/provider -t atlas-docling-adapter:test
```

- [ ] Commit:

```bash
git add services/docling/provider/adapter \
  bootstrapper/tests/test_docling_lightrag_adapter.py \
  bootstrapper/tests/test_docling_adapter_cleanup.py
git commit -m "feat: adapt Docling for pinned LightRAG"
```

## 8. Task 6 — Wire Compose Isolation and Source Permutations

- [ ] Add failing Compose/manifest tests proving:
  - Docling and Parakeet published ports render as loopback-only when `HOST_BIND_IP` is empty.
  - The adapter has no `ports`, no `backend-network`, and no provider token exposed to LightRAG.
  - Only `lightrag`, `docling-lightrag-adapter`, and container Docling join `docling-lightrag-network`.
  - The adapter receives `DOCLING_API_TOKEN` and provider endpoint; LightRAG receives only the adapter endpoint.
  - Adapter scale is one only when LightRAG and either Docling source are enabled.
  - Container Docling uses `http://docling-gpu:8000/internal/lightrag/bundle`; localhost uses `http://host.docker.internal:${DOCLING_LOCALHOST_PORT}/internal/lightrag/bundle`; disabled modes scale adapter to zero.
- [ ] Add permutation cases for Docling container/localhost/disabled crossed with LightRAG container/disabled.
- [ ] Run the narrow failures with `bootstrapper/tests/test_source_permutations.py`, `bootstrapper/tests/test_lightrag_manifest_imperative_parity.py`, and the new adapter wiring test.
- [ ] Add `docling-lightrag-adapter` to `services/docling/compose.yml`, including healthcheck, `restart: unless-stopped`, `read_only: true`, a size-limited `/tmp` `tmpfs`, `cap_drop: [ALL]`, and the dedicated network.
- [ ] Add Docling to the dedicated network while retaining `backend-network`. Attach LightRAG to the dedicated network in its owning compose fragment.
- [ ] Change only Docling/Parakeet provider port expressions to `${HOST_BIND_IP:-127.0.0.1:}${PORT}:8000`; leave unrelated service exposure unchanged.
- [ ] Extend manifest containers/images/env/runtime data and `bootstrapper/services/service_config.py` so adapter scale and endpoint are derived from both sources. Remove the broken direct LightRAG-to-provider endpoint assignment.
- [ ] Regenerate the rendered-config baseline using its test-owned update command and inspect the diff for secret leakage and unrelated churn.
- [ ] Verify rendered Compose under the full permutation matrix and commit:

```bash
uv run --project bootstrapper pytest bootstrapper/tests -k \
  'docling or parakeet or lightrag or compose or source_permutation' -q
docker compose config --quiet
git add services/docling services/parakeet services/lightrag \
  bootstrapper/services/service_config.py bootstrapper/tests
git commit -m "feat: isolate the LightRAG Docling adapter"
```

## 9. Task 7 — Propagate Provider Tokens to Trusted Consumers

- [ ] Add a Backend unit test around `document_extraction.py` asserting the synchronous Docling request includes exactly `Authorization: Bearer <DOCLING_API_TOKEN>` and never logs it. Add missing-token behavior consistent with fail-closed provider startup.
- [ ] Add manifest/Compose contract tests for Backend, Open WebUI, Hermes, n8n, and JupyterHub propagation. Assert LightRAG does not receive `DOCLING_API_TOKEN`.
- [ ] For Open WebUI, assert `AUDIO_STT_OPENAI_API_KEY=${PARAKEET_API_TOKEN}` only when Parakeet is selected; Speaches retains its compatible dummy/non-secret configuration.
- [ ] For Hermes, test the rendered server-side STT provider block and assert `api_key` comes from `PARAKEET_API_TOKEN` only for Parakeet endpoints.
- [ ] Run the focused failing tests:

```bash
uv run --project bootstrapper pytest bootstrapper/tests -k \
  'backend and docling or provider_token or hermes or open_webui or jupyter or n8n' -q
```

Expected: missing header/env propagation assertions fail.

- [ ] Pass both token vars into Backend, n8n, and JupyterHub server environments. Do not expose them through front-end build args, status APIs, generated endpoint exports, or notebook output cells.
- [ ] Add the Docling bearer header in Backend's existing httpx call.
- [ ] Render the Parakeet token into Open WebUI and Hermes according to active-source logic rather than blindly applying it to Speaches/whisper.cpp.
- [ ] Re-run focused bootstrapper and Backend tests:

```bash
uv run --project bootstrapper pytest bootstrapper/tests -k \
  'provider_token or hermes or open_webui or jupyter or n8n or service_config' -q
BACKEND_TEST_VENV=/tmp/atlas-backend-provider-tests
uv venv --python 3.12 "$BACKEND_TEST_VENV"
VIRTUAL_ENV="$BACKEND_TEST_VENV" uv pip install \
  -r services/backend/app/app/requirements.txt \
  -r services/backend/app/app/requirements-dev.txt \
  -c services/backend/app/app/requirements-locked.txt
"$BACKEND_TEST_VENV/bin/python" -m pytest services/backend/app/app/tests -q -W error
```

- [ ] Commit:

```bash
git add services/backend services/open-webui services/hermes services/n8n \
  services/jupyterhub bootstrapper/services/service_config.py bootstrapper/tests
git commit -m "feat: authenticate provider consumers"
```

## 10. Task 8 — Synchronize Documentation and Architecture Surfaces

- [ ] Invoke the `three-surface-docs` skill before changing documentation and load its reference because manifests, generated dependency blocks, diagrams, and cross-surface content all change.
- [ ] Invoke the `architecture-diagram` skill before materially changing diagram masters.
- [ ] Update authored source content for Docling/document-processor, Parakeet/STT-provider, LightRAG, Backend, Open WebUI, Hermes, n8n, and JupyterHub. Include:
  - generated-token and explicit auth-disable instructions;
  - public health versus protected route behavior;
  - loopback defaults and deliberate external-bind syntax;
  - admission and 429 behavior;
  - 900-second deadline bounds and the response-then-process-exit recovery model;
  - native service-manager restart requirement;
  - exact adapter route/network/data flow and cleanup/TTL behavior;
  - client examples with placeholder bearer values, never real secrets.
- [ ] Update `data_flow.calls` so LightRAG calls the adapter, the adapter calls Docling, and direct trusted consumers call authenticated provider endpoints.
- [ ] Run the service documentation generator for all impacted services rather than editing dependency sections/diagrams directly:

```bash
PYTHONPATH=bootstrapper uv run --project bootstrapper \
  python -m bootstrapper.docs.regen --all
```

- [ ] Run three-surface generation/check commands required by the loaded skill, inspect the generated site/wiki diff, and ensure all numbered headings remain coherent.
- [ ] Run focused docs gates:

```bash
PYTHONPATH=bootstrapper uv run --project bootstrapper \
  python -m bootstrapper.docs.regen --all --check
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_drift.py -q
uv run --project bootstrapper python scripts/check_doc_links.py
uv run --project bootstrapper python scripts/check-docs-drift.py
make docs-check
```

- [ ] Commit authored and generated documentation together:

```bash
git add services docs mkdocs.yml bootstrapper/tests
git commit -m "docs: explain authenticated provider boundaries"
```

## 11. Task 9 — Full Verification and Build Validation

- [ ] Invoke `superpowers:verification-before-completion` before claiming this implementation complete.
- [ ] Run the entire bootstrapper suite with the repository coverage floor:

```bash
uv run --project bootstrapper pytest bootstrapper/tests -q \
  --cov=bootstrapper --cov-config=bootstrapper/pyproject.toml --cov-branch \
  --cov-report=term --cov-fail-under=69
```

- [ ] Run the Backend suite in its supported Python 3.12 environment and confirm its coverage threshold remains green:

```bash
BACKEND_TEST_VENV=/tmp/atlas-backend-provider-tests
uv venv --python 3.12 "$BACKEND_TEST_VENV"
VIRTUAL_ENV="$BACKEND_TEST_VENV" uv pip install \
  -r services/backend/app/app/requirements.txt \
  -r services/backend/app/app/requirements-dev.txt \
  -c services/backend/app/app/requirements-locked.txt
"$BACKEND_TEST_VENV/bin/python" -m pytest services/backend/app/app/tests -q -W error \
  --cov=services/backend/app/app \
  --cov-config=services/backend/app/app/.coveragerc --cov-branch \
  --cov-report=term --cov-fail-under=79
```

- [ ] Run manifest, Compose, docs, shell, lock, and build-validation gates matching required CI:

```bash
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
uv run --project bootstrapper python scripts/check-compose-source-deps.py
uv run --project bootstrapper python scripts/check-kong-routes.py
uv run --project bootstrapper python scripts/check-track-membership.py
uv run --project bootstrapper python -m scripts.check_runtime_locks
uv run --project bootstrapper python -m scripts.notebook_reproducibility
uv run --project bootstrapper python scripts/check_doc_links.py
make docs-check
docker compose config --quiet
```

- [ ] Run the repository's exact ShellCheck and source-permutation commands:

```bash
git ls-files -z '*.sh' | xargs -0 shellcheck -x
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_fragment_equivalence.py \
  bootstrapper/tests/test_source_permutations.py -q
uv run --project bootstrapper python -m tools.validate_fragments
```

- [ ] Build Docling GPU, Parakeet GPU, and the adapter with their configured build args. If either external GPU base registry is unavailable, run the adapter Docker build plus `uv pip compile`/`pip install --dry-run` checks against both provider requirements and record the registry limitation explicitly; the adapter remains part of the required CI build-validation loop.
- [ ] Inspect for secrets and accidental artifacts:

```bash
git diff --check
git status --short
rg -n 'sk-atlas-(docling|parakeet)-' . \
  --glob '!docs/superpowers/plans/**' --glob '!.env'
find services/docling services/parakeet -type d -name __pycache__ -o -name '*.pyc'
```

Expected: no generated live token, no untracked runtime artifact, and no whitespace error.

- [ ] If verification reveals a defect, add a failing regression test before fixing it and make a narrowly scoped fix commit. Repeat the complete relevant gate after each fix.
- [ ] Push the current branch only after every required local gate is green:

```bash
git push origin codex/overnight-maintenance-2026-08-02
```

## 12. Completion Evidence

The task is complete only when the implementation report records all of the following with current command output:

- Both provider boundaries reject unauthenticated and over-capacity requests before reading bodies.
- Timeout tests prove post-response fatal termination without exiting the test runner.
- Parakeet startup loading is non-blocking and deadline-bounded.
- One Docling conversion creates the complete safe LightRAG archive.
- Pinned LightRAG submit/poll/result fixtures pass through the adapter.
- Job slots and files are released on every terminal path and TTL expiry.
- Compose proves loopback provider ports, no adapter host port, and the three-member isolated network.
- Tokens are generated idempotently, propagated only to trusted server-side consumers, and absent from LightRAG and generated public surfaces.
- Full bootstrapper, Backend, docs, Compose/source-permutation, ShellCheck, lock, and build-validation gates pass.
- The branch is clean and pushed to its existing origin upstream.
