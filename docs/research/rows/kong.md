---
service: kong
category: infra
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - services/kong/service.yml
  - services/kong/README.md
  - bootstrapper/utils/kong_config_generator.py
  - https://docs.konghq.com/hub/kong-inc/prometheus/
  - https://docs.konghq.com/hub/kong-inc/opentelemetry/
  - https://docs.konghq.com/hub/kong-inc/jwt/
  - https://docs.konghq.com/hub/kong-inc/ai-proxy/
  - https://docs.konghq.com/hub/kong-inc/ai-prompt-guard/
  - https://openbao.org/docs/concepts/seal/
---

# kong — Integration Research

## 1. Missing-pair integrations

- **kong ↔ multi2vec-clip**
  - Why valuable: Today CLIP is reachable only through Weaviate's `nearImage`/`nearText` paths. Exposing the raw `/vectors` endpoint via Kong lets `backend`, `n8n` flows, and `jupyterhub` notebooks compute embeddings without round-tripping a Weaviate query — useful for re-ranking, similarity probes, and offline batch jobs.
  - Mechanism sketch: alias `clip.localhost` → `http://multi2vec-clip:8080/vectors` (the inference container's native endpoint) gated by `MULTI2VEC_CLIP_SOURCE != disabled`. CORS plugin only; no auth (parity with `weaviate.localhost`).
  - Effort: small (one route block in `kong_config_generator.py`, one entry in `services/kong/README.md`).
  - Risks / open questions:
    - CLIP container has no rate limiting; busy notebooks could starve Weaviate's vectorizer pipeline. Add `rate-limiting` plugin to be safe.
    - Confirm `MULTI2VEC_CLIP_SOURCE` is wired into the bootstrapper (`services/multi2vec-clip/README.md` lists it as "optional" — manifest may not exist yet).
  - Confidence: low (no `services/multi2vec-clip/service.yml` exists; topology entry is doc-only).

## 2. Candidate new services

- **Prometheus** → `../candidates/prometheus.md`
  - Headline: Native scrape target for Kong's bundled `prometheus` plugin plus Redis/Postgres/n8n exporters.
  - Other consumers in stack: redis, supabase, n8n, ollama, litellm, backend

- **Keycloak** → `../candidates/keycloak.md`
  - Headline: OIDC provider that lets Kong front jupyterhub, open-webui, n8n, minio, neo4j-browser, and hermes with a single SSO instead of per-service basic-auth.
  - Other consumers in stack: jupyterhub, open-webui, n8n, minio, neo4j, hermes, openclaw

- **Grafana Loki** → `../candidates/grafana-loki.md`
  - Headline: Sink for Kong's `http-log` plugin (and a single log store for backend/n8n/hermes) so operators can correlate request traces across services.
  - Other consumers in stack: backend, n8n, hermes, litellm, comfyui

- **OpenBao** → `../candidates/openbao.md`
  - Headline: Watchlisted Vault-lineage secrets manager for future dynamic secrets, PKI, and unseal-aware operator workflows.
  - Other consumers in stack: infisical migration path, trading, mcp-servers, model-download credentials, backup.

## 3. Per-service feature gaps

- **`prometheus` plugin** — Why pursue: Kong 3.9 OSS bundles it; enabling it on every route gives free p50/p95/error-rate per upstream with zero code changes. Pairs with the Prometheus candidate above. Effort: small.
- **`opentelemetry` plugin** — Why pursue: emit OTLP spans for every gateway hop so requests through Kong → LiteLLM → Ollama can be stitched into a single trace. Bundled in 3.9 OSS. Effort: small (config) + medium (collector + storage).
- **`jwt` plugin (replacing per-route basic-auth)** — Why pursue: today only Supabase routes use `key-auth` and the Kong dashboard uses basic-auth; everything else is unauthenticated on the LAN. A JWT plugin validated against Supabase's GoTrue keys (already in `.env`) would secure jupyter/n8n/openclaw/hermes without a new identity service. Effort: medium.
- **`request-size-limiting` plugin** — Why pursue: ComfyUI and Docling routes accept arbitrarily large multipart uploads; a 100 MB cap at the gateway prevents accidental OOM on the host. Effort: small.
- **`correlation-id` plugin** — Why pursue: inject `X-Request-ID` on ingress so backend/litellm/hermes logs become joinable. Effort: small.
- **`ai-proxy` plugin** — Why pursue: Kong's AI Gateway can normalize OpenAI/Anthropic/Ollama request shapes at the edge, overlapping (and potentially simplifying) parts of LiteLLM's role. Worth evaluating as a comparison, not necessarily a replacement. Effort: large (architectural overlap with litellm).
- **`ai-prompt-guard` plugin** — Why pursue: regex/allow-deny on prompt content at the gateway gives a defense-in-depth layer before LiteLLM. Effort: medium.
- **Health-check active probing** — Why pursue: current generator runs a one-shot TCP probe at startup; switching to Kong's `healthchecks.active` block would auto-recover when localhost services bounce. Effort: small.
- **Admin API on a private port** — Why pursue: Kong's admin API (8001) is currently disabled (declarative-only); selectively exposing read-only `/status` on an internal port would unblock health dashboards. Effort: small.
