# Observability bundle: Prometheus + Grafana — design

**Date:** 2026-05-31
**Status:** Draft — pending implementation plan
**Scope:** First in a series of four bundles. Subsequent specs (deferred): Spark + Zeppelin, Airflow, Obsidian.
**Related:** `docs/CONTRIBUTING-services.md` (the six service-addition decisions), `docs/ROADMAP.md`, memory notes `project_service_addition_checklist`, `reference_kong_preserve_host`, `feedback_localhost_url_override_symmetry`.

---

## 1. Summary

Add a paired observability bundle to the stack:

- `services/prometheus/` — metrics scraper + TSDB, with bundled `node-exporter` (host metrics) and `cAdvisor` (container metrics) as co-lifecycled containers in the same manifest family.
- `services/grafana/` — dashboards + unified alerting UI on top of Prometheus, with 7 dashboards and the Prometheus datasource pre-provisioned.

Both services land in the `infra` category, both default to `disabled`, and both expose Kong-routed `*.localhost` aliases when enabled. Grafana ships with an auto-generated admin password (LiteLLM-pattern). Prometheus retention is user-configurable at wizard time with a default of 7 days.

The bundle is **fully instrumented**: scraping covers 14 targets across the stack — Kong, LiteLLM, Weaviate, n8n, JupyterHub, MinIO, Backend, Hermes, Prometheus + Grafana self, node-exporter, cAdvisor, and two new sidecar exporters (`postgres-exporter`, `redis-exporter`) embedded in the existing Supabase and Redis manifest families. Ollama is **deliberately excluded** because LiteLLM is its gateway and emits per-call request/token/cost metrics — direct Ollama scraping would duplicate that data.

As a precursor change, the spec also strips the `external` source variant from the three services that currently expose it (`comfyui`, `ollama`, `ray`). This is a breaking change. The rationale: `external` today is just a URL with no associated authentication design (API keys, bearer tokens, mTLS), and prematurely shipping `external` slots in new manifests perpetuates that gap. A future spec will reintroduce authenticated remote endpoints across the stack with a coherent auth model.

## 2. Goals

1. Give users a single command (`./start.sh --prometheus-source container --grafana-source container`) to spin up production-grade observability for the entire stack.
2. Cover every existing service that can be cheaply instrumented (native `/metrics` endpoints + two FastAPI shims + two sidecar exporters) without duplicating data already captured upstream.
3. Keep cold-start fast and zero-config friendly — both services are opt-in with `disabled` defaults; no penalty for users who don't want observability.
4. Match the stack's batteries-included feel: pre-provisioned datasource and 7 dashboards work out of the box; users don't need to touch Grafana to see meaningful data.
5. Establish the conventions (manifest layout, scrape config, dashboard provisioning, sidecar-exporter pattern) that future observability additions — Loki for logs, Tempo for traces, OpenTelemetry collector — can extend without re-litigating decisions.

## 3. Non-goals

- **Authenticated remote Prometheus / Grafana endpoints.** Deferred to a follow-on stack-wide spec.
- **Alertmanager as a separate container.** Grafana 9+ unified alerting subsumes the routing role. Alert rules and contact points live in Grafana.
- **Pre-provisioned alert rules or contact points.** The `provisioning/alerting/` directory is created empty; users add their own. Avoids shipping rules that page on environment-specific thresholds.
- **Logs and traces.** This spec is metrics-only. Loki + Tempo + OTel collector belong in a future bundle.
- **Postgres-backed Grafana.** SQLite on a named volume in v1. Postgres backend (via Supabase) is feasible but adds a horizontal-scaling assumption Grafana doesn't need yet.
- **Direct Ollama scraping.** Covered via LiteLLM as gateway; cAdvisor handles container-level resource metrics.
- **ComfyUI, Neo4j, SearXNG, OpenClaw, Local Deep Researcher, Parakeet, Speaches, Chatterbox, Docling, Ray native metrics.** Either no native `/metrics` exists (most), is Enterprise-only (Neo4j), or requires invasive auth setup (SearXNG). cAdvisor covers container-level resource use for all of these. Future spec can add custom exporters where call-path data is valuable.

## 4. Decisions made during brainstorming

Recorded here so future readers can see the path, not just the destination:

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Decomposition of the original 6-service request | Observability first; Spark+Zeppelin / Airflow / Obsidian each become separate specs | 6 services × ~25 touch points = unreviewable mega-spec. Observability is well-bounded and the smallest sensible first bundle. |
| 2 | Integration depth | Full instrumentation (scrape across stack + dashboards + sidecar exporters) | User explicitly chose; matches the stack's batteries-included feel. |
| 3 | Alerting model | Unified Grafana alerting (no separate Alertmanager) | One less container in the tight `infra` block; Grafana 9+ default; simpler topology. |
| 4 | Exposure + auth | Kong-aliased + admin auth on Grafana, no auth on Prometheus (Kong-gated, internal-only scrape paths) | Stack-consistent with LiteLLM UI; Prom data not sensitive when fronted by Kong. |
| 5 | Manifest packaging | Two new manifests: `prometheus` (3 co-lifecycled containers) + `grafana` (1 container); plus sidecar edits to `supabase` + `redis` | Balances wizard simplicity (2 new rows) with logical grouping (Prom + its scraping infrastructure stay together). |
| 6 | Category placement | Both in `infra` | `docs/CONTRIBUTING-services.md` explicitly lists observability under `infra`. Categories are role-based, not storage-tech-based. Splitting Prom→`data` and Grafana→`apps` would break bundle cohesion. |
| 7 | Block size policy | Stay at `infra` block_size = 10 (squeeze, don't bump) | Avoids cascading port re-numbering across every downstream category. Future Spark spec opens the bump conversation when it's needed. |
| 8 | Lock vs. optional | Optional, both default to `disabled` | Stack convention; cold-start speed; cAdvisor + node-exporter polling overhead nontrivial on laptops. |
| 9 | Retention default | `PROMETHEUS_RETENTION_DAYS=7`, user-configurable at wizard time via Ray-worker-count-style numeric prompt | Laptop-friendly default; user knows their workload best. |
| 10 | Scrape config format | Static `prometheus.yml` mounted into the container; targets stay listed even when their service is `disabled` (Prom shows DOWN) | Cleaner than templating via an init container. |
| 11 | Dashboard count | 7 | Stack Overview, LiteLLM, Kong, Postgres+Redis, Containers+Host, n8n, App-tier (Weaviate + MinIO + JupyterHub). |
| 12 | `external` source variant | Strip stack-wide (precursor; affects comfyui / ollama / ray) | No auth design today; shipping more `external` slots compounds the gap. |
| 13 | Backend + Hermes /metrics | Add `prometheus-fastapi-instrumentator` shim | Trivial 3-line middleware; both are FastAPI apps we own. |
| 14 | Ollama /metrics | Skipped — LiteLLM covers per-call data | Avoid duplicate scrape data; cAdvisor covers container resource. |
| 15 | Postgres / Redis exporters | Embedded as new containers in the existing `supabase` / `redis` manifest families | They have no life independent of their target service; embedding avoids two extra wizard rows. |
| 16 | Sidecar scale gating | `_generate_prometheus_config()` Python hook sets `POSTGRES_EXPORTER_SCALE` and `REDIS_EXPORTER_SCALE` to mirror `PROMETHEUS_SCALE` | Canonical cross-manifest scale arithmetic pattern (`_generate_stt_provider_config` reference). |

## 5. Architecture

```
              ┌──────────────────── browser ────────────────────────┐
              │ grafana.localhost  (admin auth, GF_AUTH_ANONYMOUS=false)
              │ prometheus.localhost  (no auth — Kong-gated, internal-only scrape paths)
              └───────────────────────────┬─────────────────────────┘
                                          │ Kong routes (preserve_host: true)
                                          ▼
   infra category ┌──────────────────────────────────────────────────────────┐
                  │  services/grafana/      services/prometheus/             │
                  │  ┌─────────────┐        ┌─────────────────────────────┐  │
                  │  │  grafana    │◄───────│ prometheus (TSDB, ret=7d)   │  │
                  │  │  (3000)     │ ds-prov│  + node-exporter (9100)     │  │
                  │  │  SQLite     │        │  + cadvisor (8080)          │  │
                  │  │  7 dashb.   │        │  scrape_interval: 30s       │  │
                  │  └─────────────┘        └─────────────┬───────────────┘  │
                  └──────────────────────────────────────┼───────────────────┘
                                                         │ scrape /metrics over backend-network
       ┌─────────────────────────────────────────────────┼─────────────────────────────────┐
       ▼                ▼              ▼                 ▼                ▼            ▼
   kong:8100     litellm:4000     hermes:8000     backend:8000      n8n:5678     weaviate:2112
   (plugin)      (prom callback)  (instrumentator)(instrumentator)  (N8N_METRICS) (PROMETHEUS_MONITORING)

       ▼                 ▼                     ▼                          ▼
   jupyterhub:8000   minio:9000           postgres-exporter:9187      redis-exporter:9121
   /hub/metrics      /minio/v2/metrics    (sidecar in supabase fam)   (sidecar in redis fam)
                     /cluster
```

### 5.1. New service families

| Family | Containers | Category | Manifest action |
|---|---|---|---|
| `services/prometheus/` | `prometheus`, `node-exporter`, `cadvisor` | infra | New manifest |
| `services/grafana/` | `grafana` | infra | New manifest |
| `services/supabase/` | adds `postgres-exporter` | data | Edit existing |
| `services/redis/` | adds `redis-exporter` | data | Edit existing |

### 5.2. Port allocation (`infra` block, size 10, base 63000)

| Offset | Port | Service | Manifest |
|---:|---:|---|---|
| 0 | 63000 | Kong proxy | kong |
| 1 | 63001 | Kong HTTPS | kong |
| 2 | 63002 | Ray dashboard | ray |
| 3 | 63003 | Ray GCS | ray |
| 4 | 63004 | Ray client | ray |
| 5 | 63005 | Prometheus | prometheus |
| 6 | 63006 | node-exporter | prometheus |
| 7 | 63007 | cAdvisor | prometheus |
| 8 | 63008 | Grafana | grafana |
| 9 | — | free | — |

> *Drafting note (updated post-implementation):* the original spec table miscounted
> Kong as a single-port service. Kong is actually two ports (proxy + HTTPS), so
> every slot shifts by one. The shipped layout matches the table above.

Topological-order driven by `depends_on.required` lists. Two non-obvious patches were
required to keep Kong at `63000` and Ray's dashboard at `63002`:
- `prometheus.depends_on.required: [supabase, redis, kong, ray]` — slot-ordering pins.
  Without `kong`/`ray` here, Prometheus becomes "ready" right after `supabase`
  (its only real boot dep) and grabs slot 0 ahead of Kong, which still has to
  wait on `redis`. The four entries match Kong's and Ray's own slot-pin pattern
  (where `redis` ties them at the same topological depth and alphabetical break
  decides — `k < p < r` here).
- `grafana.depends_on.required: [prometheus, supabase]` — prometheus is a
  genuine boot dep (datasource provisioning reads `PROMETHEUS_ENDPOINT`);
  supabase pins ordering. Transitively after Kong and Ray via the prometheus
  edge.

The `data` block (size 20, currently 12/20 used) absorbs the two new sidecar exporters at the next free offsets — `postgres-exporter` (~63022) and `redis-exporter` (~63023). Exact slot numbers are auto-resolved at every regen and should not be hardcoded.

## 6. Section A — Precursor: strip `external` source variants stack-wide

This section lands **before** the observability work in the implementation plan so the new manifests are introduced into a stack with consistent source semantics.

### 6.1. 6.A.1 Manifest edits

**`services/comfyui/service.yml`** — delete:
- `sources.options[].id: external` block (and its `requires: [COMFYUI_EXTERNAL_URL]`)
- `env.name: COMFYUI_EXTERNAL_URL`
- `runtime_sc.comfyui.external` slice

**`services/ollama/service.yml`** — delete:
- `sources.options[].id: ollama-external` block
- `env.name: LLM_PROVIDER_EXTERNAL_URL`
- `runtime_sc.ollama.ollama-external` slice

**`services/ray/service.yml`** — delete:
- `sources.options[].id: ray-external` block
- `env.name: RAY_EXTERNAL_ADDRESS`
- `runtime_sc.ray-head.ray-external` and `runtime_sc.ray-worker.ray-external` slices

**`bootstrapper/services/service_config.py::_generate_ray_config`** — remove the `ray-external` branch; the source set collapses to `{ray-container-cpu, ray-container-gpu, disabled}`.

### 6.2. 6.A.2 Test updates

| File | Action |
|---|---|
| `tests/test_ray_config.py` | Remove tests that exercise `ray-external` (the `RAY_EXTERNAL_ADDRESS`-conditioned cases). Worker-count and head-only tests stay. |
| `tests/test_wizard_ray_steps.py` | Remove ray-external wizard-flow assertions. |
| `tests/test_wizard_comfyui_options.py` | Remove external-option assertions. |
| `tests/test_source_permutations.py` | Auto-trims via dynamic enumeration; verify no hardcoded external-source skip list. |
| `tests/test_manifests.py` | Auto-trims via dynamic schema validation. |
| `tests/test_topology.py` | Audit for hardcoded external-source slot assumptions. |
| `tests/test_live_catalog_sync.py` | Audit for Ollama-external-specific catalog branches. |
| `tests/conftest.py` | Audit fixtures for external-source defaults. |
| `tests/fixtures/rendered_config_baseline.yml` | Regenerate; the baseline rendering loses external-slice content. |

### 6.3. 6.A.3 Doc updates

- `docs/deployment/source-configuration.md` — remove `external` rows from the SOURCE matrix; remove `### *_EXTERNAL_URL` / `### RAY_EXTERNAL_ADDRESS` subsections.
- `docs/quick-start/interactive-setup-wizard.md` — remove external-option mentions.
- `services/{comfyui,ollama,ray}/README.md` — remove the external-source paragraphs (the regen tool handles the auto-generated Dependencies & Integrations block).
- `README.md` (repo root) — remove external mentions in the localhost-source list and the service URL table.
- `docs/CHANGELOG.md` — under `[Unreleased]`, add a `### Removed (breaking)` block (text in Section 11 below).
- `docs/ROADMAP.md` — note "authenticated-remote endpoints" as a future item.

### 6.4. 6.A.4 Plumbing

- `bootstrapper/start.py` — the `--<svc>-source` Click options stay; their value-validation against manifest source lists will reject `*-external` automatically. Help text mentioning `external` is removed.
- `bootstrapper/utils/localhost_validator.py::SERVICE_CHECKS` — remove any external-URL validators (impl phase verifies; likely none today since the validator handles localhost specifically).

### 6.5. 6.A.5 User migration

Users with `RAY_SOURCE=ray-external`, `COMFYUI_SOURCE=external`, or `OLLAMA_SOURCE=ollama-external` in their `.env` will see a clear error on next bootstrap. `start.py` adds a one-shot detection: if it sees any of these literal values, it prints a pointer to the CHANGELOG entry and exits 2 with a non-confusing message.

## 7. Section B — `services/prometheus/` manifest

### 7.1. 7.B.1 `service.yml`

```yaml
name: prometheus
label: "Prometheus (metrics scraper + TSDB)"
category: infra
docs: services/prometheus/README.md

containers:
  - prometheus
  - node-exporter
  - cadvisor

images:
  - var: PROMETHEUS_IMAGE
    default: "prom/prometheus:v2.55.1"
    container: prometheus
  - var: NODE_EXPORTER_IMAGE
    default: "prom/node-exporter:v1.8.2"
    container: node-exporter
  - var: CADVISOR_IMAGE
    default: "gcr.io/cadvisor/cadvisor:v0.49.1"
    container: cadvisor

sources:
  var: PROMETHEUS_SOURCE
  default: disabled
  options:
    - id: container
      label: "Container (Prom + node-exporter + cAdvisor)"
    - id: disabled
      label: "Disabled"

env:
  - name: PROMETHEUS_SOURCE
    default: disabled
  - name: PROMETHEUS_PORT             # auto-assigned by topology slot allocator
  - name: NODE_EXPORTER_PORT          # auto-assigned
  - name: CADVISOR_PORT               # auto-assigned
  - name: PROMETHEUS_RETENTION_DAYS
    default: "7"
    description: "TSDB retention in days. User-configurable at wizard time. Default 7."
  - name: PROMETHEUS_ENDPOINT
    auto_managed: true
  - name: PROMETHEUS_SCALE
    auto_managed: true
  - name: NODE_EXPORTER_SCALE
    auto_managed: true
  - name: CADVISOR_SCALE
    auto_managed: true

depends_on:
  required:
    - supabase                       # display-order pin (matches Ray pattern)
  optional: []

exports:
  - name: PROMETHEUS_ENDPOINT
    consumers: [grafana, backend]

rows:
  - display_name: "Prometheus"
    source_var: PROMETHEUS_SOURCE
    port_var: PROMETHEUS_PORT
    scale_var: PROMETHEUS_SCALE
    alias: prometheus.localhost
    description: "Metrics scraper + TSDB with bundled node-exporter and cAdvisor."
    secondary_number:
      env_var: PROMETHEUS_RETENTION_DAYS
      label: "Retention (days)"
      default: "7"
      visible_when_source: ["container"]
      min: 1
      max: 365

runtime_sc:
  prometheus:
    container:
      scale: 1
      environment:
        PROMETHEUS_ENDPOINT: http://prometheus:9090
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment:
        PROMETHEUS_ENDPOINT: ''
      deploy: {}
      extra_hosts: []
  node-exporter:
    container: { scale: 1, environment: {}, deploy: {}, extra_hosts: [] }
    disabled:  { scale: 0, environment: {}, deploy: {}, extra_hosts: [] }
  cadvisor:
    container: { scale: 1, environment: {}, deploy: {}, extra_hosts: [] }
    disabled:  { scale: 0, environment: {}, deploy: {}, extra_hosts: [] }

data_flow:
  calls:
    - kong
    - litellm
    - hermes
    - backend
    - n8n
    - jupyterhub
    - weaviate
    - minio
    - supabase
    - redis
```

### 7.2. 7.B.2 `compose.yml`

```yaml
services:
  prometheus:
    image: ${PROMETHEUS_IMAGE}
    container_name: ${PROJECT_NAME}-prometheus
    restart: unless-stopped
    deploy:
      replicas: ${PROMETHEUS_SCALE:-0}
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=${PROMETHEUS_RETENTION_DAYS}d
      - --web.console.libraries=/usr/share/prometheus/console_libraries
      - --web.console.templates=/usr/share/prometheus/consoles
      - --web.enable-lifecycle
    ports:
      - "${PROMETHEUS_PORT}:9090"
    volumes:
      - ./services/prometheus/config/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./services/prometheus/config/rules:/etc/prometheus/rules:ro
      - prometheus-data:/prometheus
    healthcheck:
      test: ["CMD", "wget", "-q", "-O-", "http://localhost:9090/-/healthy"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    networks:
      - backend-network

  node-exporter:
    image: ${NODE_EXPORTER_IMAGE}
    container_name: ${PROJECT_NAME}-node-exporter
    restart: unless-stopped
    deploy:
      replicas: ${NODE_EXPORTER_SCALE:-0}
    command:
      - --path.procfs=/host/proc
      - --path.sysfs=/host/sys
      - --collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)
    ports:
      - "${NODE_EXPORTER_PORT}:9100"
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /:/rootfs:ro
    networks:
      - backend-network

  cadvisor:
    image: ${CADVISOR_IMAGE}
    container_name: ${PROJECT_NAME}-cadvisor
    restart: unless-stopped
    deploy:
      replicas: ${CADVISOR_SCALE:-0}
    ports:
      - "${CADVISOR_PORT}:8080"
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro
      - /dev/disk/:/dev/disk:ro
    devices:
      - /dev/kmsg
    networks:
      - backend-network

volumes:
  prometheus-data:
    name: ${PROJECT_NAME}-prometheus-data
    driver: local
```

### 7.3. 7.B.3 Subdirectory layout

```
services/prometheus/
├── service.yml
├── compose.yml
├── config/
│   ├── prometheus.yml                # static scrape config (Section 9)
│   └── rules/
│       └── stack-recording.yml       # placeholder for recording rules; empty in v1
├── README.md                          # numbered-section doc per project_service_doc_location
└── architecture.svg / .html           # auto-regen via bootstrapper.docs.regen prometheus
```

### 7.4. 7.B.4 Cross-manifest scale hook

`bootstrapper/services/service_config.py`:

```python
def _generate_prometheus_config(source_value: str, shared_env: dict) -> None:
    """Resolve scales for the prometheus family + cross-manifest exporter sidecars.

    PROMETHEUS_SCALE / NODE_EXPORTER_SCALE / CADVISOR_SCALE follow PROMETHEUS_SOURCE
    directly. POSTGRES_EXPORTER_SCALE and REDIS_EXPORTER_SCALE are written here too
    because the sidecars live in other manifests (supabase, redis) but are useless
    when nothing scrapes them. This is the canonical cross-manifest scale
    arithmetic pattern — see _generate_stt_provider_config.
    """
    on = "1" if source_value == "container" else "0"
    for var in ("PROMETHEUS_SCALE", "NODE_EXPORTER_SCALE", "CADVISOR_SCALE",
                "POSTGRES_EXPORTER_SCALE", "REDIS_EXPORTER_SCALE"):
        shared_env[var] = on
```

Wired into `generate_service_environment()` alongside `_generate_ray_config`.

## 8. Section C — `services/grafana/` manifest

### 8.1. 8.C.1 `service.yml`

```yaml
name: grafana
label: "Grafana (observability UI + alerting)"
category: infra
docs: services/grafana/README.md

containers:
  - grafana

images:
  - var: GRAFANA_IMAGE
    default: "grafana/grafana:11.3.0"
    container: grafana

sources:
  var: GRAFANA_SOURCE
  default: disabled
  options:
    - id: container
      label: "Container"
    - id: disabled
      label: "Disabled"

env:
  - name: GRAFANA_SOURCE
    default: disabled
  - name: GRAFANA_PORT                  # auto-assigned
  - name: GRAFANA_ADMIN_USERNAME
    default: "admin"
  - name: GRAFANA_ADMIN_PASSWORD
    default: ""
    secret: true
    description: "Auto-generated on first bootstrap by generate_grafana_admin_password(). Persisted to .env."
  - name: GRAFANA_ENDPOINT
    auto_managed: true
  - name: GRAFANA_SCALE
    auto_managed: true

depends_on:
  required:
    - prometheus                        # genuine boot dep — datasource reads PROMETHEUS_ENDPOINT
    - supabase                          # display-order pin
  optional: []

exports:
  - name: GRAFANA_ENDPOINT
    consumers: []

rows:
  - display_name: "Grafana"
    source_var: GRAFANA_SOURCE
    port_var: GRAFANA_PORT
    scale_var: GRAFANA_SCALE
    alias: grafana.localhost
    description: "Observability dashboards + unified alerting UI on top of Prometheus."

runtime_sc:
  grafana:
    container:
      scale: 1
      environment:
        GF_SECURITY_ADMIN_USER: ${GRAFANA_ADMIN_USERNAME}
        GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
        GF_SERVER_ROOT_URL: http://grafana.localhost
        GF_USERS_ALLOW_SIGN_UP: "false"
        GF_AUTH_ANONYMOUS_ENABLED: "false"
        GRAFANA_ENDPOINT: http://grafana:3000
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment:
        GRAFANA_ENDPOINT: ''
      deploy: {}
      extra_hosts: []

data_flow:
  calls:
    - prometheus
```

### 8.2. 8.C.2 `compose.yml`

```yaml
services:
  grafana:
    image: ${GRAFANA_IMAGE}
    container_name: ${PROJECT_NAME}-grafana
    restart: unless-stopped
    deploy:
      replicas: ${GRAFANA_SCALE:-0}
    environment:
      GF_SECURITY_ADMIN_USER: ${GRAFANA_ADMIN_USERNAME}
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_SERVER_ROOT_URL: http://grafana.localhost
      GF_USERS_ALLOW_SIGN_UP: "false"
      GF_AUTH_ANONYMOUS_ENABLED: "false"
      PROMETHEUS_ENDPOINT: ${PROMETHEUS_ENDPOINT}
    ports:
      - "${GRAFANA_PORT}:3000"
    volumes:
      - grafana-data:/var/lib/grafana
      - ./services/grafana/config/grafana.ini:/etc/grafana/grafana.ini:ro
      - ./services/grafana/config/provisioning:/etc/grafana/provisioning:ro
    depends_on:
      prometheus:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O- http://localhost:3000/api/health | grep -q ok"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    networks:
      - backend-network

volumes:
  grafana-data:
    name: ${PROJECT_NAME}-grafana-data
    driver: local
```

### 8.3. 8.C.3 Subdirectory layout

```
services/grafana/
├── service.yml
├── compose.yml
├── config/
│   ├── grafana.ini                                       # minimal overrides
│   └── provisioning/
│       ├── datasources/
│       │   └── prometheus.yml                            # uses ${PROMETHEUS_ENDPOINT}
│       ├── dashboards/
│       │   ├── dashboards.yml                            # provisioner config
│       │   ├── stack-overview.json
│       │   ├── litellm.json
│       │   ├── kong.json
│       │   ├── postgres-redis.json
│       │   ├── containers-and-host.json
│       │   ├── n8n.json
│       │   └── app-tier.json                             # weaviate + minio + jupyterhub
│       └── alerting/                                     # empty in v1; reserved for future
├── README.md
└── architecture.svg / .html
```

### 8.4. 8.C.4 Datasource provisioning

`services/grafana/config/provisioning/datasources/prometheus.yml`:

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: ${PROMETHEUS_ENDPOINT}
    isDefault: true
    editable: false
    jsonData:
      timeInterval: 30s
```

When `PROMETHEUS_SOURCE=disabled` and `GRAFANA_SOURCE=container`, the `PROMETHEUS_ENDPOINT` interpolates to empty and Grafana shows "datasource unreachable" — intentional UX signal that the user should enable Prom.

### 8.5. 8.C.5 Dashboard provisioning

`services/grafana/config/provisioning/dashboards/dashboards.yml`:

```yaml
apiVersion: 1
providers:
  - name: 'atlas'
    orgId: 1
    folder: 'Atlas'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
```

The 7 dashboard JSONs are checked into `services/grafana/config/provisioning/dashboards/`. Sourced from upstream Grafana dashboards.com IDs where one exists (LiteLLM dashboard, node-exporter-full, redis-exporter, postgres-exporter), and tailored for the stack's metric labels.

### 8.6. 8.C.6 Admin password generation

`bootstrapper/utils/key_generator.py`:

```python
def generate_grafana_admin_password() -> str:
    """32-char URL-safe random string. Mirrors generate_litellm_master_key."""
    return secrets.token_urlsafe(24)

def generate_and_update_grafana_admin_password(env_path: Path) -> None:
    """First-run only: fills GRAFANA_ADMIN_PASSWORD if empty in .env."""
    # mirrors generate_and_update_litellm_master_key
```

Wired into `generate_missing_keys()`.

## 9. Section D — Sidecar exporter edits

### 9.1. 9.D.1 `services/supabase/` (add `postgres-exporter`)

**`service.yml`** — append to `containers:`:

```yaml
containers:
  - supabase-db
  - supabase-meta
  - supabase-auth
  - supabase-rest
  - supabase-realtime
  - supabase-storage
  - supabase-studio
  - supabase-imgproxy
  - postgres-exporter                  # ← NEW
```

Append to `images:`:

```yaml
  - var: POSTGRES_EXPORTER_IMAGE
    default: "prometheuscommunity/postgres-exporter:v0.16.0"
    container: postgres-exporter
```

Append to `env:`:

```yaml
  - name: POSTGRES_EXPORTER_PORT       # auto-assigned by topology (data block)
  - name: POSTGRES_EXPORTER_SCALE
    auto_managed: true
```

Append to `runtime_sc`:

```yaml
  postgres-exporter:
    container:
      scale: 0                          # placeholder; _generate_prometheus_config overwrites
      environment:
        DATA_SOURCE_NAME: "postgresql://${SUPABASE_POSTGRES_USERNAME}:${SUPABASE_POSTGRES_PASSWORD}@supabase-db:5432/${SUPABASE_POSTGRES_DB}?sslmode=disable"
        PG_EXPORTER_AUTO_DISCOVER_DATABASES: "true"
      deploy: {}
      extra_hosts: []
```

**`compose.yml`** — append new service:

```yaml
  postgres-exporter:
    image: ${POSTGRES_EXPORTER_IMAGE}
    container_name: ${PROJECT_NAME}-postgres-exporter
    restart: unless-stopped
    deploy:
      replicas: ${POSTGRES_EXPORTER_SCALE:-0}
    ports:
      - "${POSTGRES_EXPORTER_PORT}:9187"
    environment:
      DATA_SOURCE_NAME: "postgresql://${SUPABASE_POSTGRES_USERNAME}:${SUPABASE_POSTGRES_PASSWORD}@supabase-db:5432/${SUPABASE_POSTGRES_DB}?sslmode=disable"
      PG_EXPORTER_AUTO_DISCOVER_DATABASES: "true"
    depends_on:
      supabase-db:
        condition: service_healthy
    networks:
      - backend-network
    healthcheck:
      test: ["CMD", "wget", "-q", "-O-", "http://localhost:9187/metrics"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
```

### 9.2. 9.D.2 `services/redis/` (add `redis-exporter`)

**`service.yml`** — append to `containers:`:

```yaml
containers:
  - redis
  - redis-exporter                     # ← NEW
```

Append to `images:`:

```yaml
  - var: REDIS_EXPORTER_IMAGE
    default: "oliver006/redis_exporter:v1.62.0"
    container: redis-exporter
```

Append to `env:`:

```yaml
  - name: REDIS_EXPORTER_PORT          # auto-assigned by topology (data block)
  - name: REDIS_EXPORTER_SCALE
    auto_managed: true
```

Append to `runtime_sc`:

```yaml
  redis-exporter:
    container:
      scale: 0                          # placeholder; _generate_prometheus_config overwrites
      environment:
        REDIS_ADDR: "redis://redis:6379"
        REDIS_PASSWORD: ${REDIS_PASSWORD}
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []
```

**`compose.yml`** — append new service (analogous to postgres-exporter above; uses image `${REDIS_EXPORTER_IMAGE}`, env `REDIS_ADDR` + `REDIS_PASSWORD`, healthcheck on `:9121/metrics`, `depends_on.redis: service_healthy`).

### 9.3. 9.D.3 Sidecar scale matrix

| PROMETHEUS_SOURCE | postgres-exporter scale | redis-exporter scale |
|---|:---:|:---:|
| container | 1 | 1 |
| disabled | 0 | 0 |

When PROMETHEUS_SOURCE=disabled the sidecars sit at scale=0 — no container started, no resource use, no connection to Postgres/Redis.

## 10. Section E — Cross-stack `/metrics` enablement

Concrete edits per service.

### 10.1. 10.E.1 Kong (`services/kong/`)

Add Prometheus plugin to `services/kong/config/kong.yml`:

```yaml
plugins:
  - name: prometheus
    config:
      status_code_metrics: true
      latency_metrics: true
      bandwidth_metrics: true
      upstream_health_metrics: true
      per_consumer: false
```

Expose port 8100 (Status API) on the network (Prom-only access, no host mapping):

```yaml
# services/kong/compose.yml
expose:
  - "8100"
```

**Scrape:** `http://kong:8100/metrics`

### 10.2. 10.E.2 LiteLLM (`services/litellm/`)

Edit `services/litellm/config/config.yaml`:

```yaml
litellm_settings:
  callbacks:
    - prometheus
```

Add to `runtime_sc.litellm.container.environment` in `service.yml`:

```yaml
PROMETHEUS_MULTIPROC_DIR: /tmp/litellm_metrics
```

Add to `services/litellm/compose.yml`:

```yaml
tmpfs:
  - /tmp/litellm_metrics:rw,size=64m
```

(Multi-worker Prom support per upstream — our LiteLLM runs 4 workers per memory `reference_litellm_quirks`.)

**Scrape:** `http://litellm:4000/metrics`

### 10.3. 10.E.3 Weaviate (`services/weaviate/`)

Add to `runtime_sc.weaviate.container.environment`:

```yaml
PROMETHEUS_MONITORING_ENABLED: "true"
```

Expose port 2112:

```yaml
# services/weaviate/compose.yml
expose:
  - "2112"
```

**Scrape:** `http://weaviate:2112/metrics`

### 10.4. 10.E.4 n8n (`services/n8n/`)

Add to `runtime_sc.n8n.container.environment`:

```yaml
N8N_METRICS: "true"
N8N_METRICS_PREFIX: "n8n_"
N8N_METRICS_INCLUDE_DEFAULT_METRICS: "true"
N8N_METRICS_INCLUDE_WORKFLOW_ID_LABEL: "true"
```

**Scrape:** `http://n8n:5678/metrics`

### 10.5. 10.E.5 JupyterHub (`services/jupyterhub/`)

Built-in `/hub/metrics`. No env-var or compose changes. **Verify in implementation phase** that the path is `/hub/metrics` on the version we run (high-confidence per upstream docs, but `/metrics` is also valid on some versions — `curl http://jupyterhub:8000/hub/metrics` against the running container is the definitive check).

**Scrape:** `http://jupyterhub:8000/hub/metrics`

### 10.6. 10.E.6 MinIO (`services/minio/`)

Add to `runtime_sc.minio.container.environment`:

```yaml
MINIO_PROMETHEUS_AUTH_TYPE: "public"
```

**Verify in implementation phase** that the MinIO Community release we run still supports the `public` auth type (the API has reorganized a few times). If newer Community editions changed the path, the scrape config adjusts.

**Scrape:** `http://minio:9000/minio/v2/metrics/cluster`

### 10.7. 10.E.7 Backend (`services/backend/`)

Add to `services/backend/app/pyproject.toml`:

```
prometheus-fastapi-instrumentator==7.0.0
```

Edit `services/backend/app/main.py` (3-line addition):

```python
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(...)
# ... existing app setup ...
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

**Scrape:** `http://backend:8000/metrics`

### 10.8. 10.E.8 Hermes Agent (`services/hermes/`)

Same instrumentator shim as Backend. **Implementation phase opens with a quick spike** against Hermes's actual source layout to confirm it's a FastAPI app with a single ASGI entrypoint we can decorate; if Hermes turns out to be a different shape (CLI wrapper around a non-FastAPI library), Hermes /metrics gets deferred to a follow-on.

**Scrape (assumed):** `http://hermes:8000/metrics`

### 10.9. 10.E.9 Static scrape config

`services/prometheus/config/prometheus.yml`:

```yaml
global:
  scrape_interval: 30s
  scrape_timeout: 10s
  external_labels:
    stack: atlas

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ['prometheus:9090']
  - job_name: grafana
    static_configs:
      - targets: ['grafana:3000']
  - job_name: node
    static_configs:
      - targets: ['node-exporter:9100']
  - job_name: cadvisor
    static_configs:
      - targets: ['cadvisor:8080']
  - job_name: kong
    static_configs:
      - targets: ['kong:8100']
  - job_name: litellm
    static_configs:
      - targets: ['litellm:4000']
  - job_name: weaviate
    static_configs:
      - targets: ['weaviate:2112']
  - job_name: n8n
    static_configs:
      - targets: ['n8n:5678']
  - job_name: jupyterhub
    static_configs:
      - targets: ['jupyterhub:8000']
    metrics_path: /hub/metrics
  - job_name: minio
    static_configs:
      - targets: ['minio:9000']
    metrics_path: /minio/v2/metrics/cluster
  - job_name: backend
    static_configs:
      - targets: ['backend:8000']
  - job_name: hermes
    static_configs:
      - targets: ['hermes:8000']
  - job_name: postgres-exporter
    static_configs:
      - targets: ['postgres-exporter:9187']
  - job_name: redis-exporter
    static_configs:
      - targets: ['redis-exporter:9121']
```

## 11. Section F — Bootstrapper plumbing + wizard + audit

### 11.1. 11.F.1 `bootstrapper/utils/source_override_manager.py`

```python
self.source_mapping = {
    # ... existing entries ...
    'prometheus_source': 'PROMETHEUS_SOURCE',
    'grafana_source':    'GRAFANA_SOURCE',
}
```

Add both keys to `test_wizard_app_discovery.py::test_source_mapping_includes_app_service_flags` assertion list.

### 11.2. 11.F.2 `bootstrapper/start.py`

```python
@click.option('--prometheus-source', type=click.Choice(['container', 'disabled']),
              help='Override PROMETHEUS_SOURCE — observability scraping stack.')
@click.option('--grafana-source', type=click.Choice(['container', 'disabled']),
              help='Override GRAFANA_SOURCE — observability dashboards.')
@click.option('--prometheus-retention-days', type=int,
              help='Override PROMETHEUS_RETENTION_DAYS (default 7).')
```

- Add parameters to `main()` signature.
- Add entries to `source_args` dict.
- Add `PROMETHEUS_PORT`, `NODE_EXPORTER_PORT`, `CADVISOR_PORT`, `GRAFANA_PORT`, `POSTGRES_EXPORTER_PORT`, `REDIS_EXPORTER_PORT` to the port-clear list (so `--base-port X` relocates them).
- Pre-bootstrap detection: if `.env` contains `RAY_SOURCE=ray-external` / `COMFYUI_SOURCE=external` / `OLLAMA_SOURCE=ollama-external`, print a CHANGELOG pointer and exit 2.

### 11.3. 11.F.3 Retention prompt mechanism

Per Ray's `RAY_WORKER_COUNT` pattern in `bootstrapper/ui/textual/integration.py`:

```python
elif key == "prometheus":
    raw_default = (env_vars.get("PROMETHEUS_RETENTION_DAYS") or "7").strip()
    # secondary numeric prompt, gated on source == container
    secondary = SecondaryNumber(
        env_var="PROMETHEUS_RETENTION_DAYS",
        label="Retention (days)",
        default=raw_default,
        min=1,
        max=365,
        visible_when_source=("container",),
    )
```

The `secondary_number` row schema is already extended in `service.yml::rows[0]` (Section 7.B.1). If `min`/`max`/`visible_when_source` are new fields, the schema in `bootstrapper/schemas/service.schema.json` needs the additive extensions plus a pinning-test update.

### 11.4. 11.F.4 `bootstrapper/utils/key_generator.py`

Add `generate_grafana_admin_password()` and `generate_and_update_grafana_admin_password()` per Section 8.C.6. Wire into `generate_missing_keys()`.

### 11.5. 11.F.5 `bootstrapper/utils/kong_config_generator.py`

```python
def generate_prometheus_service(env):
    if env.get('PROMETHEUS_SOURCE') == 'disabled':
        return None
    return {
        'name': 'prometheus',
        'url': 'http://prometheus:9090',
        'routes': [{'name': 'prometheus-host',
                    'hosts': ['prometheus.localhost'],
                    'preserve_host': True}],
    }

def generate_grafana_service(env):
    if env.get('GRAFANA_SOURCE') == 'disabled':
        return None
    return {
        'name': 'grafana',
        'url': 'http://grafana:3000',
        'routes': [{'name': 'grafana-host',
                    'hosts': ['grafana.localhost'],
                    'preserve_host': True}],
    }
```

Both registered in `get_all_services()`. `preserve_host: True` is mandatory for Grafana (SPA emits redirect URLs containing internal Docker hostname otherwise).

### 11.6. 11.F.6 `bootstrapper/utils/hosts_manager.py`

```python
GENAI_HOSTS.append("prometheus.localhost")
GENAI_HOSTS.append("grafana.localhost")
```

### 11.7. 11.F.7 `bootstrapper/services/service_config.py`

`generate_service_environment()` calls `_generate_prometheus_config(env.get('PROMETHEUS_SOURCE', 'disabled'), shared_env)` — the cross-manifest scale hook from Section 7.B.4.

### 11.8. 11.F.8 `bootstrapper/ui/textual/integration.py`

```python
_TAG_BY_KEY = {
    # ... existing entries ...
    "prometheus": "INFRA",
    "node-exporter": "INFRA",
    "cadvisor": "INFRA",
    "grafana": "INFRA",
    "postgres-exporter": "DATA",
    "redis-exporter": "DATA",
}
```

### 11.9. 11.F.9 `bootstrapper/wizard/service_discovery.py`

```python
DISPLAY_NAME_OVERRIDES['prometheus'] = 'Prometheus'
DISPLAY_NAME_OVERRIDES['grafana'] = 'Grafana'
SERVICE_DESCRIPTIONS['prometheus'] = (
    'Metrics scraper + TSDB. Bundles node-exporter (host metrics) and cAdvisor (container metrics). '
    'Auto-scrapes Kong, LiteLLM, n8n, Weaviate, JupyterHub, MinIO, Backend, Hermes, Postgres, Redis.'
)
SERVICE_DESCRIPTIONS['grafana'] = (
    'Observability dashboards + unified alerting on top of Prometheus. '
    '7 dashboards pre-provisioned.'
)
```

### 11.10. 11.F.10 `bootstrapper/ui/state_builder.py`

Add to `_SERVICES`:

```python
("prometheus", "PROMETHEUS_SOURCE", "PROMETHEUS_PORT", "PROMETHEUS_SCALE", "prometheus.localhost"),
("grafana",    "GRAFANA_SOURCE",    "GRAFANA_PORT",    "GRAFANA_SCALE",    "grafana.localhost"),
```

Add `_HOST_ALIAS` entries for both.

### 11.11. 11.F.11 Audit scripts

**`scripts/check-compose-source-deps.py::REQUIRED_DEPENDS_ON`** — add:

```python
("postgres-exporter", "supabase-db"),
("redis-exporter", "redis"),
```

**`scripts/check-kong-routes.py::EXPECTED_HOST_ROUTES`** — no entries needed; both new services default to `disabled` (per `docs/CONTRIBUTING-services.md` line 525).

### 11.12. 11.F.12 `.github/dependabot.yml`

Per memory `feedback_dependabot_scan_coverage`, audit the `directories:` list to ensure Backend / Hermes pyproject.toml paths are tracked. Add any missing paths.

## 12. Implementation phases

Sequencing — each phase is a green CI gate. No phase may merge with a red CI.

### 12.1. Phase 1 — Precursor: strip `external` source variants

- Manifest edits to comfyui, ollama, ray (Section 6.A.1).
- `_generate_ray_config` simplification (Section 6.A.1).
- Test updates (Section 6.A.2).
- Doc updates (Section 6.A.3).
- `start.py` migration-detection on startup (Section 11.F.2).
- CHANGELOG entry (Section 13).
- Regen `.env.example` + baseline rendering fixture.

Ships as a single PR. Breaking change CHANGELOG entry is the user-facing artifact.

### 12.2. Phase 2 — Prometheus family

- New `services/prometheus/` folder with manifest, compose, config/prometheus.yml, config/rules/, README, regen-time architecture diagram.
- Static scrape config covering all 14 targets (Section 10.E.9).
- `_generate_prometheus_config` hook (Section 7.B.4).
- Bootstrapper plumbing: source_mapping, CLI flags, port allocation, hosts manager, kong route generator, retention prompt, TUI tags, state builder, service discovery, key generator (for Grafana — wired here since the next phase needs it).
- Audit scripts (Section 11.F.11 — postgres-exporter / redis-exporter REQUIRED_DEPENDS_ON entries, but the actual sidecars come in Phase 4).
- Regen `.env.example` and per-service README.

CI must pass with `PROMETHEUS_SOURCE=disabled` (default) — full byte-equivalence baseline.

### 12.3. Phase 3 — Grafana

- New `services/grafana/` folder with manifest, compose, config/grafana.ini, datasource + dashboard provisioning, 7 dashboard JSONs, README, architecture diagram.
- Admin password key generation wired (already in Phase 2's plumbing).
- Kong route generator for grafana.localhost.
- Regen.

### 12.4. Phase 4 — Sidecar exporters

- Edits to `services/supabase/` (postgres-exporter container + env + runtime_sc).
- Edits to `services/redis/` (redis-exporter container + env + runtime_sc).
- Regen `.env.example` + baseline rendering.
- Audit scripts (REQUIRED_DEPENDS_ON entries activate).

### 12.5. Phase 5 — Cross-stack `/metrics` enablement

Five sub-PRs, parallelizable:

- Kong Prometheus plugin (Section 10.E.1).
- LiteLLM callbacks + tmpfs (Section 10.E.2).
- Weaviate / n8n / MinIO env-flag edits (Sections 10.E.3, 10.E.4, 10.E.6).
- Backend `prometheus-fastapi-instrumentator` shim (Section 10.E.7).
- Hermes shim (Section 10.E.8) — opens with a spike to confirm Hermes's app shape.

Each sub-PR is independently mergeable; missing scrape targets just show DOWN in Prom.

### 12.6. Phase 6 — Verification, documentation, CHANGELOG

- Run the full verification matrix (Section 14).
- Update `docs/README.md` services index.
- Update `docs/ROADMAP.md` (mark observability shipped).
- Update `docs/deployment/ports-and-routes.md` (new ports + Kong hosts).
- Update `docs/deployment/source-configuration.md` (new SOURCE matrix rows + dedicated subsections).
- Update `docs/quick-start/interactive-setup-wizard.md` (new wizard options).
- Update `services/kong/README.md` (curl examples).
- Update root `README.md` (5 places per `project_service_addition_checklist`).
- CHANGELOG `### Added (observability bundle)` block.

## 13. Breaking changes + CHANGELOG entries

Under `[Unreleased]`:

### 13.1. Removed (breaking)

Source variants `external` (ComfyUI), `ollama-external` (Ollama), and `ray-external` (Ray) and their associated env vars `COMFYUI_EXTERNAL_URL`, `LLM_PROVIDER_EXTERNAL_URL`, `RAY_EXTERNAL_ADDRESS` are removed pending a stack-wide authenticated-remote design. Users with these source values set in `.env` must switch to `container` (or `disabled`) before bootstrapping. The bootstrapper now detects these legacy values and exits with a pointer to this entry. Future spec will reintroduce authenticated remote endpoints across the stack.

### 13.2. Added (observability bundle)

- New services `prometheus` (with bundled `node-exporter` and `cAdvisor`) and `grafana` under the `infra` category. Both default to `disabled`; opt in with `--prometheus-source container --grafana-source container` or via the wizard.
- `PROMETHEUS_RETENTION_DAYS` (default `7`) — user-configurable at wizard time.
- `GRAFANA_ADMIN_USERNAME` (default `admin`) and `GRAFANA_ADMIN_PASSWORD` (auto-generated on first bootstrap, mirrors LiteLLM master-key pattern).
- Kong-routed aliases `prometheus.localhost` (no auth) and `grafana.localhost` (admin auth).
- Two new sidecar containers `postgres-exporter` (in `supabase` family) and `redis-exporter` (in `redis` family) — scale 1↔0 with `PROMETHEUS_SOURCE`.
- Native `/metrics` enabled on Kong (via Prometheus plugin), LiteLLM (via callbacks), Weaviate (`PROMETHEUS_MONITORING_ENABLED=true`), n8n (`N8N_METRICS=true`), MinIO (`MINIO_PROMETHEUS_AUTH_TYPE=public`).
- `prometheus-fastapi-instrumentator` middleware on Backend and Hermes (3-line addition + dependency).
- 7 Grafana dashboards pre-provisioned: Stack Overview, LiteLLM, Kong, Postgres+Redis, Containers+Host, n8n, App-tier.

## 14. Verification matrix

Run pre-PR. Per `docs/CONTRIBUTING-services.md` + memory `project_service_addition_checklist`:

```bash
# 1. compose warnings
docker compose --env-file .env -f docker-compose.yml config 2>&1 | grep -i warning   # zero output

# 2. dependency audit
python3 scripts/check-compose-source-deps.py                                          # PASS

# 3. kong route audit
python3 scripts/check-kong-routes.py                                                  # PASS

# 4. docs drift
python3 scripts/check-docs-drift.py                                                   # PASS
python3 scripts/check_doc_links.py                                                    # PASS

# 5. tests + byte-equivalence + permutation matrix
cd bootstrapper && uv run pytest -q                                                   # all green

# 6. regen all per-service docs
PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check

# 7. end-to-end source-permutation
for prom in container disabled; do
  for graf in container disabled; do
    cp .env.example .env
    sed -i '' "s/^PROMETHEUS_SOURCE=.*/PROMETHEUS_SOURCE=$prom/" .env
    sed -i '' "s/^GRAFANA_SOURCE=.*/GRAFANA_SOURCE=$graf/" .env
    cd bootstrapper && uv run python -c "from services.service_config import ServiceConfig; sc = ServiceConfig(); sc.generate_service_environment(); print('OK')"
  done
done

# 8. base-port relocation
./start.sh --base-port 64000 --prometheus-source container --grafana-source container
curl -fsS http://localhost:64007/api/health   # Grafana on infra offset 7
curl -fsS http://localhost:64004/-/healthy    # Prometheus on infra offset 4

# 9. functional smoke
curl -fsS http://prometheus.localhost/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'
# 14 targets — those whose source is `container` show health=up; those `disabled` show health=down
# Then visit http://grafana.localhost, log in with GRAFANA_ADMIN_USERNAME / GRAFANA_ADMIN_PASSWORD,
# verify Prometheus datasource is connected and 7 dashboards appear in the "Atlas" folder.
```

## 15. Risks + open questions

| # | Risk / question | Mitigation |
|---|---|---|
| 1 | **MinIO Community `/metrics` path may differ from `/minio/v2/metrics/cluster`** in newer releases | Implementation phase opens with `curl` against the running MinIO; adjust scrape config if needed. |
| 2 | **JupyterHub `/hub/metrics` vs `/metrics`** path | Same — verify against running container in impl phase. |
| 3 | **Hermes is not FastAPI** | Phase 5 sub-PR opens with a spike against Hermes's source. If not FastAPI, Hermes /metrics is deferred and removed from the scrape config; no other phase changes. |
| 4 | **LiteLLM `PROMETHEUS_MULTIPROC_DIR` tmpfs sizing** (64m) may be too small under load | Tunable env var; first noticed via scrape failures. v2 can bump. |
| 5 | **The `secondary_number` schema fields `min` / `max` / `visible_when_source`** may not exist today | Verify against `bootstrapper/schemas/service.schema.json`. If absent, additive schema extensions land in Phase 2 (small change). |
| 6 | **cAdvisor on macOS has limited host-metric access** (Linux-host specifics) | Document the limitation; cAdvisor mostly produces container-level metrics that are platform-agnostic. Host-level node-exporter coverage degrades on macOS where `/proc` isn't real Linux procfs. |
| 7 | **Postgres-exporter credentials** read `SUPABASE_POSTGRES_PASSWORD` directly — if Supabase ever rotates the password mid-stream, the sidecar needs a restart | Acceptable v1 behavior; documented in services/prometheus/README.md. |
| 8 | **`test_fragment_equivalence` baseline regen** for new sidecar containers may hit the local-vs-CI Compose-version drift gotcha | Per memory `project_baseline_regen_via_ci_artifact`, use the two-cycle CI-artifact dance if local drift exceeds extension of `_strip_volatile_defaults`. |
| 9 | **Grafana dashboard JSONs drift over time** as upstream metric labels change | Each PR that bumps a service image version should verify the corresponding dashboard still works. Future spec could add a dashboard-validation script. |
| 10 | **External-source strip migration affects existing user `.env` files** | `start.py` migration detection (Section 11.F.2) and CHANGELOG entry (Section 13) cover this. |

## 16. Out of scope / Future work

The following are explicitly deferred to subsequent specs:

- **Spark cluster + Zeppelin** bundle (planned next; multi-container head+workers using the Ray manifest template).
- **Airflow** standalone (most complex single-service candidate; webserver + scheduler + worker + triggerer).
- **Obsidian / Logseq / Memos** — variant selection needed before any design work.
- **Authenticated remote endpoints** stack-wide. The `external` source variant returns once each candidate service has an auth env var (`<SVC>_EXTERNAL_API_KEY`, `<SVC>_EXTERNAL_TOKEN`, or `<SVC>_EXTERNAL_BASIC_AUTH`) and a documented Kong-passthrough story.
- **Loki + Tempo + OpenTelemetry collector** — logs and traces. Same `infra`-category home; benefits from the conventions this spec establishes.
- **Alertmanager** as a separate container — only if Grafana unified alerting hits a real limitation (e.g., need for clustered HA alerting).
- **Postgres-backed Grafana** — if Grafana ever needs to scale horizontally (today it's single-replica).
- **Native /metrics for ComfyUI, Neo4j, SearXNG, OpenClaw, Ray, the STT/TTS engines** — add custom exporters where call-path observability is valuable.
- **Dashboard validation script** to catch drift between checked-in JSON and upstream metric label changes.
- **Per-tenant cost dashboards** (LiteLLM emits per-user/team metrics — a future spec can surface these).

---

**End of design.**
