# Supabase (db, auth, api, storage, realtime, studio, meta)

## 1. Overview

`supabase` is an Atlas service family in the `data` category. Its implementation and service-owned documentation live under `services/supabase/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `data`
- Kind: `container`
- Tracks: `all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading`

## 4. Access

- Kong aliases: `supabase-studio.localhost`
- Port variables: `SUPABASE_DB_PORT, POSTGRES_EXPORTER_PORT, SUPABASE_META_PORT, SUPABASE_STORAGE_PORT, SUPABASE_AUTH_PORT, SUPABASE_API_PORT, SUPABASE_REALTIME_PORT, SUPABASE_STUDIO_PORT`

## 5. Configuration

- SOURCE variable: `SUPABASE_DB_SOURCE`
- Default SOURCE: `container`
- Available SOURCE values: `container`

## 6. Dependencies And Topology

- Required dependencies: `-`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| SUPABASE_DB_SOURCE | container | container |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/supabase/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/supabase/architecture.svg)
- Diagram HTML: [`services/supabase/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/supabase/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/supabase/README.md](https://github.com/thekaveh/atlas/blob/main/services/supabase/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
