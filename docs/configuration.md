# 6.1. Configuration Overview

## 1. Environment Files

`.env.example` is generated from service manifests and topology defaults. `.env` stores the local runtime choices. The first `./start.sh` creates `.env` from `.env.example`. Later starts keep your values and add keys that are new in `.env.example`; `./start.sh env backfill` does the same without a start.

## 2. SOURCE Overrides

Every source-configurable service's SOURCE value can be selected through the wizard or passed as a CLI flag such as `--weaviate-source localhost`. Always-on and init SOURCE variables (`SUPABASE_*_SOURCE`, `KONG_API_GATEWAY_SOURCE`, `LITELLM_SOURCE`, `REDIS_SOURCE`, `BACKEND_SOURCE`, `*_INIT_SOURCE`) have no flag or wizard step.

## 3. Ports

Ports are derived from `BASE_PORT` and service-specific slots. Change the base with `./start.sh --base-port 64000`. `--base-port auto` selects the first wholly free port block each time you pass it. A consumer manifest can commit `BASE_PORT: auto` instead. Atlas resolves it once, saves the block to `.env` and keeps it on later starts.

## 4. Overlays

On every start, Atlas merges `.env.user`, `ATLAS_ENV_USER_FILE` and consumer manifest `env.values` over `.env`. The precedence is in [Core Concepts §5](core-concepts.md#5-user-overlays).

## 5. Profiles

`--profile dev` (alias of `default`) or `--profile prod` applies a source and observability bundle from `bootstrapper/profiles.yml`. The wizard asks for it; see [Interactive Setup Wizard §7](quick-start/interactive-setup-wizard.md#7-stack-options).
