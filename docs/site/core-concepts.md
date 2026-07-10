# Core Concepts

## 1. SOURCE Values

Each configurable service has a SOURCE variable that controls whether Atlas runs it in Docker, connects to a localhost instance, disables it, or uses a service-specific mode.

## 2. Tracks

Tracks select the subset of services needed for a workflow and force-disable out-of-track services unless the user explicitly overrides them.

## 3. Manifests

Each manifest owns service metadata, env vars, source options, dependencies, runtime slices, and data-flow calls.

## 4. Gateway Access

Kong provides the main local entrypoint and generated aliases. Direct ports remain available for services that expose their own UI or API.

## 5. Hosted Media Gateway

The backend exposes `POST /media/generate` and `GET /media/operations/{operation_id}` as the provider-neutral hosted media surface. Requests dispatch by `provider`, `modality`, and `model`; the initial registry supports `provider=fal` with `modality=image`. Provider API keys stay in the backend environment, and responses normalize status, artifacts, cost, license, and provenance for downstream consumers.
