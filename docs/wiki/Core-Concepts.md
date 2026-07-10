# Core Concepts

## 1. SOURCE Values

SOURCE variables choose how Atlas obtains a service. Common values include `container`, `localhost`, `disabled`, and service-specific variants such as CPU/GPU modes or cloud-provider enablement.

SOURCE choices are stored in `.env`, surfaced through the setup wizard, and consumed by the bootstrapper when it synthesizes Compose configuration.

Typical SOURCE behavior:

- `container` runs the service inside the Atlas Compose project.
- `localhost` connects Atlas to a host-managed service.
- `disabled` excludes the service from the active Compose graph.
- `none` is used where a local provider is intentionally absent.
- Cloud providers use provider-specific enablement flags and API keys.

## 2. Tracks

Tracks select workflow-oriented groups of services. Out-of-track services are force-disabled unless the user explicitly overrides them with a SOURCE flag.

The track system keeps first launch manageable. A RAG user should not have to answer every data-engineering service prompt, and a data-engineering user should not have to enable creative-AI services.

## 3. Manifests

Each manifest owns service metadata, environment variables, SOURCE choices, dependencies, runtime slices, adaptive-service behavior, and data-flow calls.

Manifest fields feed generated `.env.example`, the docs site, wiki tables, route references, and CI validation.

## 4. Topology

The topology registry defines category, ports, aliases, display names, descriptions, and dependency shape used by the wizard and generated references.

Topology is where service categories, port assignment, and Kong alias visibility become consistent across the UI, docs, and generated routes.

## 5. Gateway Routing

Kong exposes the main local entrypoint and generated service aliases. Direct ports remain available for selected service UIs and APIs.

The root dashboard is the preferred entrypoint for humans. Direct service ports are still documented because they matter for smoke tests, local development, and troubleshooting.

## 6. Hosted Media Gateway

The backend exposes `POST /media/generate` and `GET /media/operations/{operation_id}` as the provider-neutral hosted media surface. Requests dispatch by `provider`, `modality`, and `model`; the initial registry supports `provider=fal` with `modality=image`.

Provider API keys stay in the backend environment, and responses normalize status, artifacts, cost, license, and provenance for downstream consumers.

## 7. Adaptive Services

Backend and Open WebUI adapt to whichever upstream services are enabled. This keeps the stack useful when a user chooses a smaller track or disables optional services.

Adaptive behavior prevents broken integrations from appearing when their upstream service is disabled.

## 8. Generated Documentation

Service READMEs remain the service-owned source of truth. The `.io` site and wiki are generated publishing layers that keep navigation, tables, and references synchronized.

The docs generator reads the same model used by tests, so docs drift becomes visible before merge.

## 9. Init Companions

Some services use init containers or first-run scaffolding for schema setup, bucket creation, workflow import, model pulls, or catalog bootstrapping.

Init companions should be documented with the service they prepare and represented in dependency/topology notes when they affect startup order.

## 10. Service Categories

Service categories describe the role of the service family in Atlas. They also influence wizard grouping, generated references, and visual grouping in the docs.

Current categories include infra, data, llm, media, agents, apps, and aggregate/doc-only surfaces.
