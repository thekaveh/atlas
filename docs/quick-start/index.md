# 2.1. Quick Start

## 1. Launch Atlas

You need Docker with Docker Compose **v2.20.3 or newer** — the top-level `docker-compose.yml` merges the per-service fragments through Compose's native `include:` directive, which older releases do not support. v2.26+ is recommended. `./start.sh` checks this and stops with the detected version if it is too old.

Run `./start.sh` from the repository root. The setup wizard walks through track and profile selection, base port and project name, service SOURCE choices, host aliases, and the launch summary. It needs `uv` or Python 3.10+ (`./start.sh` prefers `uv run` and falls back to the system `python3`).

## 2. Common Paths

```bash
./start.sh
./start.sh --track gen-ai-rag
./start.sh --track data-eng
./start.sh --base-port 64000
./start.sh --setup-hosts
```

Any stack flag (such as `--base-port` or `--setup-hosts`) skips the wizard and,
without `--track`, launches the full `.env` default set rather than a track's
subset; combine it with `--track <key>` on a fresh clone.

## 3. First Services To Visit

Use the Atlas root dashboard at `http://localhost:63000` after launch. Direct service URLs and Kong aliases are listed in the generated service catalog and ports reference.

There is no single login. Most dashboards open with an administrator password Atlas generated into `.env` on first start (Open WebUI: `OPEN_WEB_UI_ADMIN_EMAIL` / `OPEN_WEB_UI_ADMIN_PASSWORD`; LiteLLM: `admin` / `LITELLM_MASTER_KEY`; Grafana: `admin` / `GRAFANA_ADMIN_PASSWORD`; Supabase Studio and several other dashboards: the Kong pair `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`). n8n asks you to create its owner account, and JupyterHub prints its token in the container log. [Access and Credentials](../operations/access-and-credentials.md) lists every surface and the credential that opens it.
