# MinIO (S3-compatible object storage)

## 1. Overview

`minio` is an Atlas service family in the `data` category. Its implementation and service-owned documentation live under `services/minio/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `data`
- Kind: `container`
- Tracks: `all, data-eng, ml-eng, trading`

## 4. Access

- Kong aliases: `minio.localhost, s3.minio.localhost`
- Port variables: `MINIO_PORT, MINIO_CONSOLE_PORT`

## 5. Configuration

- SOURCE variables: `MINIO_SOURCE`
- Default SOURCE values: `container`
- Available SOURCE values: `container, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| MINIO_SOURCE | container | container, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/minio/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/minio/architecture.svg)
- Diagram HTML: [`services/minio/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/minio/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/minio/README.md](https://github.com/thekaveh/atlas/blob/main/services/minio/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
