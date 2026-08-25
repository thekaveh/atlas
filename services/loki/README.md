# 5.2.30. Loki

## 1. Overview

Loki is Atlas' disabled by default, Grafana-native log store. In this first slice it provides a local development store and Grafana datasource, but Atlas application log shipping is intentionally not claimed yet.

Loki is internal-only, has no Kong route, and should be queried through Grafana. The default retention is short for local development.

## 2. Access

- SOURCE: `LOKI_SOURCE=disabled` by default.
- Internal endpoint: `http://loki:3100`.
- Direct host URL: none in the first slice.
- Kong URL: none; no Kong route is generated.
- Grafana surface: the `Loki` datasource is provisioned when Grafana starts.

## 3. Configuration

The service reads `./config/loki.yaml`, mounted to `/etc/loki/loki.yaml`. `LOKI_RETENTION_PERIOD` defaults to `24h` and is used by the compactor retention settings.

## 4. Architecture & Wiring

Grafana queries Loki directly. OpenTelemetry Collector does not export Atlas application logs to Loki yet; that remains a follow-up after trace ingestion is validated.

## 5. Dependencies & Integrations

### 5.1. Current — Upstream (this service calls)

_No upstream calls._

### 5.2. Current — Downstream (services that call this)

| Service | Category |
|---|---|
| grafana | infra |

### 5.3. Architecture diagram

![loki architecture](./architecture.svg)

[Open the full-size diagram](./architecture.html) for a full-screen view.

### 5.4. Future — Missing pair integrations

_No high-confidence opportunities identified._

### 5.5. Future — Candidate new services

_No high-confidence opportunities identified._

### 5.6. Future — Unused features in this service

_No high-confidence opportunities identified._

## 6. Troubleshooting

- If Grafana cannot query logs, confirm `LOKI_SOURCE=container` and `LOKI_ENDPOINT=http://loki:3100`.
- If storage grows unexpectedly, check `LOKI_RETENTION_PERIOD` and compactor logs.
- Roll back by setting `LOKI_SOURCE=disabled`.

## 7. Capabilities & limitations

| Capability | Status | Verification | Notes |
|---|---|---|---|
| Local Grafana-queryable log storage | supported | tested | Atlas provisions a single local Loki store and a Grafana datasource for querying logs that an operator sends to its internal endpoint. |
| Automatic Atlas application log collection | not-supported | documented | No Promtail, Docker logging driver, or OpenTelemetry-to-Loki exporter is wired, so stock Atlas application logs are not ingested automatically. |
| Highly available long-term log retention | not-supported | documented | The bundled filesystem-backed single replica is intended for short local retention and has no replicated object-store deployment. |
