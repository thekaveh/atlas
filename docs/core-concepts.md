# 3. Core Concepts

## 1. SOURCE Values

Each configurable service has a SOURCE variable that controls whether Atlas runs it in Docker, connects to a localhost instance, disables it, or uses a service-specific mode.

## 2. Tracks

Tracks select the subset of services needed for a workflow and force-disable out-of-track services unless the user explicitly overrides them.

## 3. Manifests

Each manifest owns service metadata, env vars, source options, dependencies, runtime slices, and data-flow calls.

## 4. Gateway Access

Kong provides the main local entrypoint and generated aliases. Direct ports remain available for services that expose their own UI or API.

## 5. User Overlays

Atlas starts from `.env.example`, writes or preserves the active `.env`, then merges user-owned overlays before backfilling missing keys and applying CLI flags. The sibling `.env.user` file is useful for local checkout-owned values. `ATLAS_ENV_USER_FILE` points at a parent-owned overlay outside the Atlas checkout and is the preferred submodule-consumer pattern.

Overlay precedence is `.env.example` baseline, generated or existing `.env`, sibling `.env.user`, `ATLAS_ENV_USER_FILE`, then explicit flags such as `--project` and `--<svc>-source`. Both overlays are merged on every start, including `--cold`. Relative `ATLAS_ENV_USER_FILE` values resolve against the directory that invoked `start.sh`.

## 6. Hosted Media Gateway

The backend exposes `POST /media/generate`, `GET /media/operations/{operation_id}`, and `POST /media/operations/{operation_id}/cancel` as the provider-neutral hosted media surface. Requests dispatch by `provider`, `modality`, and `model`; the registry supports `provider=fal` with `modality=image` and `modality=image_to_3d` (verified TRELLIS, Hunyuan3D, Tripo, and Rodin endpoints), and `provider=comfyui` with `modality=image` (the managed/local ComfyUI host, #519). Provider API keys stay in the backend environment, responses normalize status, artifacts, cost, license, and provenance, and cancellation retains reserved spend until provider polling proves a terminal outcome. The normalized `artifact_url` is **provider-dependent**: absolute (a hosted CDN URL) for `provider=fal`, but **gateway-relative** for `provider=comfyui` (`/comfyui/image/{filename}?…`, an in-network backend proxy path). Consumers MUST resolve a relative `artifact_url` (one beginning with `/`, with no `http(s)://` scheme) against their own gateway/backend base URL before fetching it (#678).

## 7. RAG Chunking Gateway

The backend exposes `POST /api/chunk` as the shared Chonkie-powered text-splitting surface for RAG ingestion clients. The endpoint supports token, recursive, and semantic strategies and returns stable character offsets plus strategy metadata so n8n workflows, notebooks, and future ingestion services can share one chunking contract.

JupyterHub also installs Chonkie for exploratory notebook work, including `13_chonkie_chunking.ipynb`. Production workflows should still call the Backend endpoint instead of each service adding its own Chonkie dependency.

## 8. RAG Evaluation Gateway

The backend exposes `POST /api/rag/evaluate` as the shared Ragas-powered quality-evaluation surface for supplied RAG question, answer, context, and optional reference records. The endpoint supports faithfulness, answer relevancy, context precision, and context recall metrics while routing evaluator calls through Atlas LiteLLM configuration.

JupyterHub also installs Ragas for exploratory evaluation work, including `14_ragas_evaluation.ipynb`. Production workflows should call the Backend endpoint so n8n, notebooks, and future ingestion jobs share one metric contract without each service carrying its own evaluator package.
