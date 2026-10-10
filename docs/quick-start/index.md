# 2.1. Launch Atlas

## 1. Requirements and launch

Install these before the first launch:

- Docker with Docker Compose **v2.20.3 or newer** (v2.26+ recommended). The top-level `docker-compose.yml` merges the per-service fragments with Compose's `include:` directive, which older releases do not support. `./start.sh` checks the version and stops if it is too old.
- `uv`, or Python 3.10 or newer. `./start.sh` prefers `uv run` and falls back to the system `python3`.
- Git.

Clone the repository and run every command from its root (`atlas/`):

```bash
git clone https://github.com/thekaveh/atlas && cd atlas
./start.sh
```

The setup wizard asks for track and profile, base port and project name, service SOURCE choices and host aliases. It then shows the launch summary.

## 2. Common Paths

```bash
./start.sh
./start.sh --track gen-ai-rag
./start.sh --track data-eng
./start.sh --base-port 64000
./start.sh --setup-hosts
```

Flags such as `--base-port`, `--setup-hosts`, `--detach` or any `--*-source`
skip the wizard. Atlas then launches what `.env` holds (the `.env.example`
defaults on a fresh clone), not a track's subset. Add `--track <key>` to apply one.

## 3. First Services To Visit

After the launch, open the Atlas root dashboard at `http://localhost:<BASE_PORT>`. The default is `http://localhost:63000`; after `./start.sh --base-port 64000` it is `http://localhost:64000`. `KONG_HTTP_PORT` in `.env` holds the dashboard port, which is derived from `BASE_PORT`. Direct URLs and Kong aliases are in the [service catalog](../services.md) and the [ports and routes reference](../reference/ports-routes.md).

There is no single login. Most dashboards open with an administrator password that Atlas writes to `.env` on first start:

- Open WebUI: `OPEN_WEB_UI_ADMIN_EMAIL` / `OPEN_WEB_UI_ADMIN_PASSWORD`.
- LiteLLM: `admin` / `LITELLM_MASTER_KEY`.
- Grafana: `admin` / `GRAFANA_ADMIN_PASSWORD`.
- Supabase Studio and several other dashboards: the Kong pair `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`.

n8n asks you to create its owner account. JupyterHub prints its token in the container log. [Access and Credentials](../operations/access-and-credentials.md) lists every surface and the credential that opens it.
