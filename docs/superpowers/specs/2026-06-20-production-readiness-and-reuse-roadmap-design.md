# Atlas — Production Readiness & Reuse Roadmap

**Date:** 2026-06-20
**Status:** Design / assessment (for review)
**Author:** Kaveh Razavi (with Claude Code)
**Type:** Strategy + roadmap (not a single-feature implementation spec)

---

## 1. Part 0 — Executive summary & anchor

Atlas today is an excellent **single-host, self-hosted developer stack**. It is *production-adjacent* but not production-ready for internet exposure, and it is already **more reuse-ready than it looks** (a 666-line submodule guide and a `services/_user/` overlay slot exist). This document assesses the current state, evaluates **where to deploy it**, compares **reuse strategies**, tells the honest **cross-OS** story, and lays out a **phased roadmap** anchored to a concrete goal.

**The anchor (drives all prioritization):**
- **Goal:** stand up one hardened Atlas instance as the personal backbone infrastructure for the author's own projects, **live on a real domain in ≤ 3 weeks.**
- **Not in scope now:** multi-tenant SaaS, multi-node HA, Kubernetes, native-Windows server support.
- **Budget:** ~$50–100/month all-in.
- **GPU:** optional, via **burst** (RunPod) or a **home GPU** reached through `*_SOURCE=external` — never always-on cloud GPU (out of budget).
- **Reuse:** "as my infra" means other repos will consume this instance, so reuse mechanics (submodule + `services/_user/` overlay + shared Docker network) are Phase 1, right behind go-live.

**The three biggest go-live blockers** (all currently unmet): no TLS, service ports bind to `0.0.0.0`, and default/placeholder secrets must be rotated and verified. Everything else is hardening depth or later-phase work.

---

## 2. Part 1 — Readiness assessment (current state)

Rating scale: **Solid** (production-acceptable) · **Partial** (works, gaps remain) · **Missing**. "Go-live severity" is relative to the Part 0 anchor (single internet-facing host).

| # | Area | Rating | Go-live severity | Evidence (file refs) |
|---|------|--------|------------------|----------------------|
| 1 | TLS / HTTPS | **Missing** | **Blocker** | Kong listens HTTP on `KONG_HTTP_PORT` (`services/kong/compose.yml`); `kong_config_generator.py` emits plain-HTTP upstreams; no ACME/cert handling anywhere. |
| 2 | Network exposure | **Partial** | **Blocker** | Every `services/*/compose.yml` maps `"${PORT}:CONTAINER"` with no `127.0.0.1:` prefix → Docker binds `0.0.0.0`. On a networked host, all 30+ services are reachable, not just Kong. |
| 3 | Secrets | **Partial** | **Blocker** | `bootstrapper/utils/key_generator.py` auto-generates + rotates placeholders (good), but `.env.example` ships literal placeholders (`DASHBOARD_PASSWORD=kong_password`, `OPEN_WEB_UI_ADMIN_PASSWORD=admin`, `REDIS_PASSWORD=redis_password`, `GRAPH_DB_PASSWORD=neo4j_password`); no external secret store (Vault/Infisical are roadmapped only). |
| 4 | AuthN on exposed UIs | **Partial** | **High** | Each UI (Open WebUI, n8n, Grafana, Supabase Studio, JupyterHub, LiteLLM, MinIO console, Airflow, Zeppelin) has its own auth; none are designed to be internet-exposed together. Needs a deliberate per-surface auth audit before exposure. |
| 5 | Container hardening | **Partial** | **Medium** | `restart: unless-stopped` + `healthcheck:` on all ~29 services (Solid). But resource limits only on a few (Hermes, LightRAG); **zero** `read_only`, `user:` (non-root), or `cap_drop`. Verified: those greps return empty. |
| 6 | Data persistence & backups | **Partial** | **High** | Named volumes everywhere (Solid). Only Neo4j has backup/restore (`services/neo4j/README.md`). No cross-service backup, no offsite, no schedule, no DR runbook for Postgres/Redis/Weaviate/MinIO. |
| 7 | Observability & logging | **Partial** | **Medium** | Prometheus + Grafana + 13 scrape targets + 7 dashboards exist but ship **disabled** (`PROMETHEUS_SOURCE=disabled`). No centralized logging, no log rotation (default json-file → unbounded disk), no tracing (Loki/Tempo roadmapped). |
| 8 | Environment separation | **Missing** | **Medium** | One config, one `.env`. No `compose.prod` override, no dev/staging/prod profiles. SOURCE controls *backends*, not *environments*. |
| 9 | Orchestration / scaling | **Missing** | **Low** (out of scope) | Single-host Compose only; Ray scales workers on one host. No k8s/Swarm/HA. Correctly deferred per anchor. |
| 10 | CI/CD & image supply chain | **Partial** | **Low** | Strong manifest/lint/test CI (`services-lint.yml`); images pinned mostly by tag. No image build/publish pipeline, no scanning (Trivy), no signing (cosign)/SBOM; a few `:latest` tags. |
| 11 | Reusability / embedding | **Solid-ish** | n/a (Phase 1) | `docs/deployment/submodule-usage.md` (666 lines), `services/_user/` overlay (gitignored, CONTRIBUTING §21), `PROJECT_NAME`/`BRAND_*`/`BASE_PORT`/`*_SOURCE`. One real gap: `_user/` manifests load, but the top-level `docker-compose.yml` `include:` list is hand-maintained, so user services need a wrapper to actually run. |
| 12 | Cross-OS | **Partial** | **Low** | Python core is OS-aware (`bootstrapper/utils/system.py`: `detect_os`, `is_elevated`, host-gateway, hosts-path). But `start.sh`/`stop.sh` are POSIX `sh` with `id -u`; no `.ps1`/`.cmd`. README overstates "works on all OS" — native Windows needs WSL/Git Bash. |

**Headline:** items 1–3 are the hard go-live gate; 4–7 are the "do it properly" tier; 8–12 are lower priority or deferred for this deployment.

---

## 3. Part 2 — Hosting / deployment-target fit

*All prices are mid-2026 snapshots from the cited sources and drift frequently — re-verify on the provider's own pricing page before committing. EUR→USD ≈ ×1.08.*

### 3.1. What this stack demands of a host

- **One Linux host, root + Docker**, single-host Docker Compose (`docker-compose.yml` is an `include:` shell).
- **RAM is the binding constraint.** ~30 containers including JVM-heavy (Spark, Airflow, Zeppelin) + graph/vector DBs (Neo4j, Weaviate) → **32 GB is the comfortable target; 16 GB is a cramped floor** that will swap once everything is up.
- **Disk:** 100–200 GB NVMe (volumes + model weights if local).
- **Optional GPU tier** (Ollama, ComfyUI, GPU TTS/STT) — separable from the main host.
- **The offload lever:** `*_SOURCE=external` lets stateful/heavy pieces move off-box — **MinIO→S3**, **Ollama→cloud LLM API**, and (partially) Postgres→managed. This reshapes provider fit.

### 3.2. Provider scorecard (best in-budget option per provider)

| Provider | Best fit ≤ $100/mo | RAM | vCPU | ~$/mo | Managed offload | Reliability | Verdict |
|----------|--------------------|-----|------|-------|-----------------|-------------|---------|
| **Hetzner CX53** | shared vCPU, cost-opt | **32 GB** | 16 | **~$26** | none | good | **Best raw value — recommended primary (no-GPU)** |
| **OVHcloud VPS-4** | flat pricing | 24 GB | 8 | **~$23** | none (separate product) | good, free daily backup | **Best reliability-adjusted value** |
| **Akamai/Linode** | High Memory 24 GB | 24 GB | low | ~$60 | managed PG + S3 | good | Good if you want US-first + managed offload |
| **Vultr** | High Frequency 16 GB | 16 GB | 4 | ~$96 | managed DB + object | good | Only 16 GB fits; global regions |
| **DigitalOcean** | Mem-Opt 16 GB | 16 GB | 2 | ~$84 | **best (Spaces $5, DB/Redis $15)** | good, best DX | Worst compute value; pick only if offloading heavily |
| **Contabo VPS 50** | 64 GB(!) | 64 GB | 16 | ~$32 | object only | **~93% 90-day uptime, oversubscribed** | Cheapest RAM/$ but **staging/dev only** |
| **Hostinger KVM 8** | dedicated KVM | 32 GB | 8 | ~$50 (renewal) | none | good, free weekly backup | Friendliest Docker UX; budget the $50 renewal not the $26 promo |
| **AWS EC2 t3.2xlarge** | 32 GB | 32 GB | 8 | ~$243 on-demand | full (RDS/S3/etc.) | excellent | **Over budget**; sensible only at scale or for specific managed pieces |

Sources: Hetzner June-2026 price change (Northflank, byteiota, wz-it); DigitalOcean/Vultr/Linode/Contabo/Hostinger/OVH official pricing pages + HostAdvice/Better Stack reviews; AWS us-east-1 on-demand. Contabo uptime: Contabo status page / StatusGator snapshot.

### 3.3. GPU options (budget reality)

**$50–100/mo cannot run an always-on cloud GPU** (AWS g4dn ≈ $384/mo, g5 ≈ $734/mo; a single H100 ≈ $1.7k+/mo). GPU within budget = **burst** or **own hardware**:

| Approach | $/hr (entry/mid) | Light-use $/mo | Persistence | How the stack reaches it | Notes |
|----------|------------------|----------------|-------------|--------------------------|-------|
| **RunPod on-demand** | A40 ~$0.44, 4090 ~$0.69 | **~$15–20** | network volume ~$0.07/GB-mo (weights survive teardown) | `*_SOURCE=external` → `https://<pod>-11434.proxy.runpod.net` (or TCP public IP for long jobs) | **Recommended cloud burst.** Add your own auth; 100s proxy timeout → use TCP for long ComfyUI jobs |
| **Vast.ai** | 3090 ~$0.13–0.20 | **< $15** | host-set, tied to instance (weaker) | mapped external port; self-signed cert | Cheapest; marketplace variance; pick verified on-demand hosts |
| **Home GPU** | $0 marginal (electricity) | ~$0 | permanent local | `*_SOURCE=external`/`localhost` over **Tailscale/WireGuard** | Cheapest if you own a 24 GB card; you are the SRE; never expose `:11434`/`:8188` raw |
| **Cloud LLM API** | per-token | single-digit–low-tens | n/a | `LLM_PROVIDER_SOURCE=api` via LiteLLM | No GPU at all; simplest; the budget-correct default for many cases |
| Lambda / Paperspace | A100 floor ~$2/hr / $39/mo gate | — | — | — | **Not suitable** for light bursts |

### 3.4. Recommendations

- **No-GPU path (simplest, fully in budget):** **Hetzner CX53 (~$26/mo)** running the full stack on CPU, LLMs via **cloud API** (`LLM_PROVIDER_SOURCE=api` → OpenAI/Anthropic/OpenRouter through LiteLLM), objects optionally on **S3-compatible storage**. If you want flat pricing + bundled daily backups and can accept 24 GB, **OVH VPS-4 (~$23/mo)** is the reliability-adjusted pick.
- **GPU/hybrid path:** Same VPS for the 30 containers; point Ollama/ComfyUI at **RunPod on-demand** (or a **home GPU over Tailscale**) via `*_SOURCE=external`. Budget impact: ~$15–20/mo added for light use.
- **Avoid as primary:** Contabo (reliability) except for staging; AWS/hyperscalers (cost/complexity) except to consume **S3** and **cloud LLM APIs** à la carte.
- **TLS / public edge:** see Part 5 (Cloudflare Tunnel → Kong primary; Caddy → Kong fallback).

### 3.5. Reference architecture (recommended single-host go-live)

```
                Internet
                   │  (HTTPS, TLS at edge)
            ┌──────▼───────┐
            │  Cloudflare  │   (free Universal SSL; DDoS; CDN)
            └──────┬───────┘
                   │  outbound-only tunnel (no inbound ports open)
        ┌──────────▼───────────┐   VPS (Hetzner CX53 / OVH VPS-4)
        │  cloudflared (tunnel)│   - firewall: deny all inbound except SSH(key)
        └──────────┬───────────┘   - all service ports bound to 127.0.0.1
                   │ http://kong:<base_port>
            ┌──────▼───────┐
            │     Kong     │  single gateway; per-service routes
            └──────┬───────┘
                   │  backend-network (internal)
   ┌───────────────┼────────────────────────────────┐
   │ Supabase  Open WebUI  n8n  Weaviate  Neo4j      │
   │ LiteLLM   Backend     Redis  Grafana/Prometheus │
   └───────────────┬────────────────────────────────┘
                   │  optional offload via *_SOURCE=external
        ┌──────────┼───────────────┬──────────────────┐
        ▼          ▼               ▼                  ▼
   S3 (objects)  Cloud LLM API   RunPod GPU      Home GPU
   (MinIO swap)  (Ollama swap)   (burst)         (Tailscale)
```

*(An optional polished SVG of this can be generated later via the `architecture-diagram` skill.)*

---

## 4. Part 3 — Reuse / embedding strategy

### 4.1. What exists today (verified)

- **Submodule guide:** `docs/deployment/submodule-usage.md` (666 lines) — network sharing, Kong as single entry, service extension, CI/CD, troubleshooting.
- **`services/_user/` overlay slot:** gitignored upstream, tracked downstream (CONTRIBUTING §21); the manifest loader skips `_`-prefixed dirs so a consumer's services don't leak into upstream PRs.
- **Parameterization without forking:** `PROJECT_NAME` (resource prefix), `BRAND_*` (rebrand), `BASE_PORT` (entire port layout), per-service `*_SOURCE`.
- **Minimal root-dir coupling:** bootstrapper uses `Path(__file__).resolve().parent.parent`, not hardcoded paths.

### 4.2. Strategy comparison (for "Atlas as my multi-project infra")

| Strategy | Fit today | Pros | Cons |
|----------|-----------|------|------|
| **Git submodule** (`infra/`) + shared network | **Best — documented** | Versioned, isolated via `PROJECT_NAME`, official guide; consuming repos join `${PROJECT_NAME}-network` and call services by DNS | Must pin to a tag; `_user/` services need a wrapper (see gap) |
| **Shared-network compose-merge** (Atlas runs; your app `network_mode`/external network) | Good | Decouples your app repo from Atlas internals; your app is just another compose project on the same network | Soft start-order dependency; your services not managed by the wizard |
| **Template repo / fork** | Good | Full control | Manual upstream-diff merges forever |
| **Published images / use-as-dependency** | **Not designed** | (would enable `pip install`/image pull) | Bootstrapper assumes repo layout; no package/registry artifact today |

**Recommendation:** one **always-on Atlas instance** (submodule pinned to a tag, or its own deploy repo) + each project repo consumes it via the **shared `${PROJECT_NAME}-network`** and, where a project needs a co-located service, the **`services/_user/` overlay**. This is the lowest-friction model and is already 90% documented.

### 4.3. Documentation verdict

**Mostly sufficient, three gaps:**
1. **`_user/` → `docker compose` include gap** — the overlay loads into the bootstrapper's manifest model, but the hand-maintained `include:` list in `docker-compose.yml` means user services don't actually start without a wrapper or an auto-include/glob. **This is the one real engineering fix for reuse** (Phase 1).
2. **No "consume Atlas from a sibling project repo" quickstart** — the submodule guide covers embedding Atlas *inside* a parent repo, but not the "Atlas runs standalone; my other repos talk to it over the shared network" pattern (the likely real usage). Add a short doc.
3. **No release/tagging discipline** for downstream pinning — adopt semver tags so submodule consumers can pin and upgrade deliberately.

---

## 5. Part 4 — Cross-OS honesty (short)

- **Production host = Linux** → native-Windows server support is irrelevant to the go-live. macOS (Intel/ARM) and Linux (Docker/Podman) dev hosts are fully supported.
- **Native Windows dev = WSL or Git Bash only** (`start.sh` is POSIX `sh` with `id -u`; no `.ps1`). The Python core is OS-aware, so a future `start.ps1` is feasible but **not worth building now**.
- **Action:** correct the README's overstated "works on all OS / Windows, macOS, Linux" claim to state WSL/Git Bash for Windows. **Low effort, Low priority** — do it opportunistically.

---

## 6. Part 5 — Phased roadmap

Each item: **Severity** (Blocker/High/Med/Low for go-live) · **Effort** (S < 0.5d, M 0.5–2d, L > 2d) · dependencies.

### 6.1. Phase 0 — Go-live blockers (the ≤ 3-week critical path)

| ID | Item | Sev | Eff | Notes / dependencies |
|----|------|-----|-----|----------------------|
| P0-1 | **Choose + provision host** | Blocker | S | Hetzner CX53 (no-GPU) or OVH VPS-4; create, SSH-key-only, base hardening (fail2ban, unattended-upgrades) |
| P0-2 | **TLS + public edge** | Blocker | M | **Cloudflare Tunnel → Kong** (no inbound ports, free) primary; **Caddy → Kong** fallback (open 80/443, ~3-line Caddyfile, persist `/data`). Do NOT double-terminate. |
| P0-3 | **Lock network exposure** | Blocker | M | Bind all service host ports to `127.0.0.1` (or rely on the tunnel + a `compose.prod` override that drops host port maps for everything except Kong); host firewall deny-all-inbound except SSH. |
| P0-4 | **Secrets rotation + hygiene** | Blocker | S | Verify `key_generator` rotated every placeholder; confirm `.env` gitignored + never committed; set strong passwords for Grafana/Open WebUI/n8n/Neo4j/Redis/Dashboard/MinIO. |
| P0-5 | **Auth audit on every exposed surface** | High | M | Decide which UIs are public vs tunnel-private (Cloudflare Access can gate them). Ensure no unauthenticated admin UI is reachable. |
| P0-6 | **Backups + offsite** | High | M | Postgres `pg_dump` + named-volume snapshots + Neo4j `backup.sh`; push to S3/Spaces/B2; cron schedule; test one restore. |
| P0-7 | **`compose.prod` override** | High | M | Resource limits (mem/cpus) on heavy hitters (Spark/Airflow/Zeppelin/Neo4j/Weaviate/ComfyUI) to prevent OOM; enable Prometheus+Grafana with auth; set Docker log rotation (`max-size`/`max-file`). |
| P0-8 | **GPU/LLM decision wired** | High | S | Pick: cloud LLM API (`LLM_PROVIDER_SOURCE=api`), RunPod burst, or home GPU via Tailscale (`*_SOURCE=external`); configure + smoke-test. |
| P0-9 | **README cross-OS fix** | Low | S | Correct the overstated claim (opportunistic). |

### 6.2. Phase 1 — Reuse mechanics (right after go-live)

| ID | Item | Sev | Eff | Notes |
|----|------|-----|-----|-------|
| P1-1 | **`_user/` → compose include** | — | M | Auto-generate the `include:` list (or glob) so `services/_user/<svc>` actually runs without editing core; add a regression test. |
| P1-2 | **"Consume Atlas from a sibling repo" doc** | — | S | Shared-network pattern, DNS names, env wiring, start-order. |
| P1-3 | **Release tagging for pinning** | — | S | Semver tags so submodule consumers pin/upgrade deliberately. |

### 6.3. Phase 2+ — Deferred (documented, sequenced)

| ID | Item | Notes |
|----|------|-------|
| P2-1 | Secrets manager (Infisical, then OpenBao) | Already roadmapped; removes plaintext `.env` dependency. |
| P2-2 | Centralized logging + tracing (Loki/Tempo) + Langfuse | The observability triangle; LLM cost/eval. |
| P2-3 | Image scanning (Trivy) + signing (cosign) + SBOM in CI | Supply-chain integrity; pin remaining `:latest`. |
| P2-4 | Deeper container hardening | `user:` non-root, `read_only`, `cap_drop: [ALL]` per service. |
| P2-5 | Env separation (staging) | A real staging profile/instance. |
| P2-6 | Managed-offload migration | S3 for MinIO (clean), cloud LLM APIs (native) — adopt early if convenient; full DB offload only if scale demands. |
| P2-7 | Multi-tenant / k8s / HA / native Windows | Only if the use case changes. |

---

## 7. Part 6 — Appendices

### 7.1. Consolidated gap register

See Part 1 table (assessment) + Part 5 (actions). Blockers: TLS (P0-2), network exposure (P0-3), secrets (P0-4). High: auth audit (P0-5), backups (P0-6), prod override (P0-7).

### 7.2. Provider comparison

See Part 2.2 (CPU hosts), 2.3 (GPU). Primary recommendation: **Hetzner CX53** (value) or **OVH VPS-4** (reliability-adjusted), + **Cloudflare Tunnel** for TLS, + **cloud LLM API** or **RunPod burst** for inference.

### 7.3. References (research, mid-2026; verify before committing)

- Hetzner June-2026 pricing: northflank.com, byteiota.com, wz-it.com; official hetzner.com/cloud.
- DigitalOcean/Vultr/Linode/Contabo/Hostinger/OVH/IONOS: official pricing pages + HostAdvice / Better Stack reviews; Contabo uptime via status page / StatusGator; Contabo Nuremberg RCA (Sept 2024).
- RunPod/Vast.ai/Lambda/Paperspace: official pricing + docs (RunPod expose-ports, network volumes; Vast pricing docs; Lambda billing).
- AWS: us-east-1 on-demand (EC2 t3, EBS gp3, S3, RDS, ElastiCache, g4dn/g5); Cloudflare Tunnel docs + 100 MB proxy upload limit; Caddy/Traefik/Kong ACME docs.
- Codebase evidence: `services/kong/`, `services/*/compose.yml`, `bootstrapper/utils/key_generator.py`, `bootstrapper/utils/system.py`, `docs/deployment/submodule-usage.md`, `docs/CONTRIBUTING-services.md` §21, `docs/ROADMAP.md`.

### 7.4. Open questions for the author

1. **GPU stance for go-live:** cloud LLM API only (simplest), RunPod burst, or home GPU via Tailscale?
2. **TLS preference:** Cloudflare Tunnel (zero inbound ports, 100 MB upload cap) vs Caddy (open 80/443, no upload cap)?
3. **Object storage:** keep MinIO on-box, or adopt S3-compatible offload at go-live?
4. **Provider:** Hetzner (value) vs OVH (reliability) vs other?
5. Should Phase 1 reuse work be folded into the same ≤3-week push, or strictly after go-live?
