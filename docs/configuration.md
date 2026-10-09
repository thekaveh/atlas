# 6.1. Configuration

## 1. Environment Files

`.env.example` is generated from service manifests and topology defaults. `.env` stores the local runtime choices.

## 2. SOURCE Overrides

Every source-configurable service's SOURCE value can be selected through the wizard or passed as a CLI flag such as `--weaviate-source localhost`. Always-on and init SOURCE variables (`SUPABASE_*_SOURCE`, `KONG_API_GATEWAY_SOURCE`, `LITELLM_SOURCE`, `*_INIT_SOURCE`) have no flag or wizard step.

## 3. Ports

Ports are derived from `BASE_PORT` and service-specific slots. Change the base with `./start.sh --base-port 64000`.
