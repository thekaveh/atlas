# 5.2.16. Globals (project + branding)

## 1. Overview

`globals` is an Atlas service family in the `infra` category. Its implementation and service-owned documentation live under `services/globals/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `infra`
- Kind: `virtual`
- Tracks: `all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading`

## 4. Access

- Kong aliases: `-`
- Port variables: `BASE_PORT`

## 5. Configuration

- SOURCE variables: `-`
- Default SOURCE values: `-`
- Available SOURCE values: `-`

## 6. Dependencies And Topology

- Required dependencies: `-`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| none | none | - |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 10. Related Configuration

- Service manifest: `services/globals/service.yml`
- Generated environment template: `.env.example`

## 11. Dependencies & Integrations

### 11.1 Current — Upstream (this service calls)

_No upstream calls._

### 11.2 Current — Downstream (services that call this)

_No downstream consumers._

### 11.3 Architecture diagram

![globals architecture](./architecture.svg)

[Open the interactive HTML diagram](./architecture.html) for a full-screen view.

### 11.4 Future — Missing pair integrations

_No high-confidence opportunities identified._

### 11.5 Future — Candidate new services

_No high-confidence opportunities identified._

### 11.6 Future — Unused features in this service

_No high-confidence opportunities identified._
