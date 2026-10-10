# 3. Core Concepts

Sections 1–5 explain how Atlas selects and configures services. Sections 6–8 describe shared Backend APIs for application developers.

## 1. SOURCE Values

Each configurable service has a SOURCE variable. It selects one mode: run in Docker, connect to a localhost instance, disable, or a service-specific mode. [SOURCE Configuration](operations/source-configuration.md) lists the values for each service.

## 2. Tracks

Tracks select the subset of services needed for a workflow and force-disable out-of-track services. An explicit `--<svc>-source` flag or a SOURCE value in a consumer manifest's `env.values` overrides the track for that service. [Tracks](tracks.md) lists the services in each track.

## 3. Manifests

Each manifest owns service metadata, env vars, source options, dependencies, runtime slices, and data-flow calls.

## 4. Gateway Access

Kong provides the main local entrypoint and generated aliases. Direct ports remain available for services that expose their own UI or API.

## 5. User Overlays

Atlas starts from `.env.example`, writes or preserves the active `.env`, then merges user-owned overlays before backfilling missing keys and applying CLI flags. The sibling `.env.user` file is useful for local checkout-owned values. `ATLAS_ENV_USER_FILE` points at a parent-owned overlay outside the Atlas checkout and is the preferred submodule-consumer pattern.

Overlay precedence is `.env.example` baseline, generated or existing `.env`, sibling `.env.user`, `ATLAS_ENV_USER_FILE`, consumer manifest `env` values, then explicit flags such as `--project` and `--<svc>-source`. Both overlays are merged on every start, including `--cold`. Relative `ATLAS_ENV_USER_FILE` values resolve against the directory that invoked `start.sh`.

## 6. Hosted Media Gateway

The backend's provider-neutral media API is `POST /media/generate`, `GET /media/operations/{operation_id}` and `POST /media/operations/{operation_id}/cancel`. Requests choose `provider`, `modality` and `model`:

- `provider=fal`: `modality=image` and `modality=image_to_3d` (verified TRELLIS, Hunyuan3D, Tripo and Rodin endpoints).
- `provider=comfyui`: `modality=image`, on the managed or local ComfyUI host.

Provider API keys stay in the backend. Responses normalize status, artifacts, cost, license and provenance. The `artifact_url` depends on the provider. For `fal` it is absolute (a hosted CDN URL). For `comfyui` it is gateway-relative: `/media/operations/{operation_id}/artifacts/{index}`, an owner-checked backend path. Consumers MUST resolve a relative `artifact_url` (one that starts with `/` and has no `http(s)://` scheme) against their gateway or backend base URL.

Cancellation keeps reserved spend until provider polling proves a terminal outcome. If FAL times out before it returns a request id, the operation becomes `submission_unknown` with its reservation held. After checking provider billing, an operator with `BACKEND_INTERNAL_API_TOKEN` calls `POST /media/operations/{operation_id}/reconcile` with `outcome=commit|release`. This call is safe to retry after a transient ledger failure.

`MEDIA_BUDGET_STORE` also backs recovery when the operation record could not be written, even with budget enforcement off. Keep the default `postgres`, because `memory` is lost when the process restarts.

## 7. RAG Chunking Gateway

The backend exposes `POST /api/chunk` as the shared Chonkie-powered text-splitting surface for RAG ingestion clients. The endpoint supports token, recursive and semantic strategies. It returns stable character offsets and strategy metadata, so n8n workflows, notebooks and future ingestion services share one chunking contract.

JupyterHub also installs Chonkie for exploratory notebook work, including `13_chonkie_chunking.ipynb`. Production workflows should still call the Backend endpoint instead of each service adding its own Chonkie dependency.

## 8. RAG Evaluation Gateway

The backend exposes `POST /api/rag/evaluate` as the shared Ragas-powered quality-evaluation surface for supplied RAG question, answer, context, and optional reference records. The endpoint supports faithfulness, answer relevancy, context precision, and context recall metrics while routing evaluator calls through Atlas LiteLLM configuration.

JupyterHub also installs Ragas for exploratory evaluation work, including `14_ragas_evaluation.ipynb`. Production workflows should call the Backend endpoint. Then n8n, notebooks and future ingestion jobs share one metric contract, and no service carries its own evaluator package.
