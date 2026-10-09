# 7.7. Using atlas as a Git Submodule

This guide covers Atlas as a git submodule of your project: submodule mechanics, the legacy `services/_user/` layout, environment files, integration patterns and troubleshooting.

> **New here?** Start with [Reusing Atlas as Infrastructure](reusing-atlas.md). It compares the reuse methods (standalone shared network, submodule, fork), states what is ready, and walks a complete consumer from scratch ([§4.1](reusing-atlas.md#41-stand-up-a-consumer-from-scratch-the-ordered-walkthrough)). That page owns instance identity, endpoints and CI gates. The [Consumer Manifest Reference](../reference/consumer-manifest.md) owns the per-key manifest contract.

> **Which integration style? Prefer the manifest.** A new submodule consumer commits an **`atlas.consumer.yml`** and passes it with `./infra/start.sh --consumer <path>`. One validated file holds branding, env, compose overlays, backend plugins, storage buckets and model and route registration ([Reusing Atlas §6.1](reusing-atlas.md#61-registering-a-parent-project-with-atlasconsumeryml)). The **`services/_user/` symlink + `.env.user` + wrapper-flags** layout in [§4.2](#42-parent-repo-consumer-reference-layout) is the **legacy tier**. It stays fully supported for existing integrations; [§4.2.1](#421-migrating-to-atlasconsumeryml) shows how to migrate.

## 1. Table of Contents

- [Quick Start](#2-quick-start)
- [Why Use as a Submodule?](#3-why-use-as-a-submodule)
- [Project Structure](#4-project-structure)
- [Configuration](#5-configuration)
- [Integration Patterns](#6-integration-patterns)
- [Contributing Back](#7-contributing-back)
- [Troubleshooting](#8-troubleshooting)
- [Advanced Topics](#9-advanced-topics)
- [Best Practices](#10-best-practices)
- [Additional Resources](#11-additional-resources)

## 2. Quick Start

### 2.1. Add atlas as a Submodule

In your project root, add Atlas as a submodule in `infra/` and pin it:

```bash
# Add Atlas as the submodule (use your fork's URL if you maintain one)
git submodule add https://github.com/thekaveh/atlas.git infra

# Pin a fixed point, not the moving main branch
git -C infra checkout <tag-or-reviewed-main-sha>
git add .gitmodules infra && git commit -m "infra: vendor Atlas at <tag-or-sha>"
```

`git submodule add` clones and initializes the submodule. The only release tag, `v0.1.0`, predates the consumer manifest, so pin a reviewed `main` commit until a newer tag exists ([Releasing §2](releasing.md#2-pinning-from-a-submodule-consumer)).

### 2.2. Configure the Project

Commit the instance identity in a consumer manifest at the parent repository root:

```yaml
# atlas.consumer.yml
project_name: myproject
env:
  values:
    BASE_PORT: auto      # or a fixed non-default block, e.g. "64000"
```

Do not set identity by editing `infra/.env`. `./start.sh` creates `.env` from `.env.example`, and a cold start regenerates it and resets a non-default `BASE_PORT` to `63000`. The manifest is re-applied on every start ([Reusing Atlas §7.2](reusing-atlas.md#72-pin-instance-identity-in-the-manifest-not-just-env)).

### 2.3. Start the Infrastructure

```bash
# From your project root
./infra/start.sh --consumer ./atlas.consumer.yml
```

### 2.4. Access Services

Each host port is `BASE_PORT` plus a fixed offset. The startup output prints the full mapping. With `BASE_PORT: auto`, Atlas allocates a block other than `63000`:

- **Kong API Gateway**: `http://localhost:<BASE_PORT>` (base + 0)
- **Supabase PostgreSQL**: `psql -h localhost -p <BASE_PORT+12> -U supabase_admin -d postgres`. The direct port is loopback-only and requires `SUPABASE_DB_PASSWORD` over scram-sha-256.
- **Supabase Studio**: `http://supabase-studio.localhost:<BASE_PORT>` (Kong route; Studio's own port is not published)
- **LiteLLM Gateway** (LLM front door): `http://localhost:<BASE_PORT+40>`
- **N8N**: `http://localhost:<BASE_PORT+75>`

## 3. Why Use as a Submodule?

Using atlas as a git submodule provides these capabilities:

- Separation of infrastructure code from application code
- Ability to pull upstream improvements while maintaining local configurations
- Project-specific environment settings tracked in parent repository
- Standard git workflow for contributing improvements back to atlas
- Multiple independent instances with isolated Docker resources (networks, volumes, containers)
- Infrastructure version pinning to specific commits or tags

## 4. Project Structure

### 4.1. Recommended Directory Layout

```
myproject/
├── .git/
├── .gitmodules              # Git submodule configuration
├── atlas.consumer.yml       # Consumer manifest (identity, env, overlays)
├── src/                     # Your application code
│   ├── backend/
│   ├── frontend/
│   └── ...
├── infra/                   # atlas submodule
│   ├── .git                 # file: gitdir: ../.git/modules/infra
│   ├── .env                 # Generated configuration (gitignored)
│   ├── .env.example
│   ├── docker-compose.yml
│   ├── start.sh
│   ├── stop.sh
│   ├── bootstrapper/        # Python orchestration + wizard
│   └── services/            # Per-service manifests, compose fragments, READMEs
│       ├── backend/         # Backend FastAPI service
│       ├── supabase/        # Supabase ecosystem
│       ├── n8n/             # n8n workflow automation
│       ├── jupyterhub/      # Notebook environment
│       └── ...              # Every other service folder
├── scripts/
│   ├── start-all.sh         # Start infra + your app
│   └── stop-all.sh
├── docker-compose.yml       # Optional: Your app services
└── README.md
```

### 4.2. Parent-repo consumer reference layout

> **Legacy tier.** The `services/_user/` symlink + `.env.user` + wrapper-flag
> layout stays fully supported for existing consumers. New consumers use a
> committed `atlas.consumer.yml` (`--consumer`). To migrate, see
> [§4.2.1](#421-migrating-to-atlasconsumeryml).

The parent repository owns application code, overlay fragments, branding,
wrapper scripts and secret references. `infra/` stays a pinned Atlas checkout,
so Atlas stays upgradeable and project wiring stays visible in the parent.

```
myproject/
├── .gitmodules
├── atlas.env.user.example
├── compose/
│   └── myproject-overlay.yml
├── infra/                         # Atlas submodule
│   ├── .env                       # generated or local, gitignored by Atlas
│   ├── .env.user                  # optional local overlay, gitignored
│   ├── services/
│   │   ├── _user/
│   │   │   └── myproject/
│   │   │       └── compose.yml -> ../../../../compose/myproject-overlay.yml
│   │   └── supabase/db/_user/     # optional SQL slot, normally gitignored
│   └── volumes/                   # runtime state, gitignored
├── scripts/
│   ├── setup-overlay.sh
│   ├── start-infra.sh
│   └── stop-infra.sh
├── src/
└── README.md
```

Two worked patterns use this shape:

- **RAG-showcase-style** consumers add parent-owned n8n, backend, plugin or
  app-service overlays to a RAG-oriented track, plus explicit services needed
  outside that track.
- **DayDreams-style** consumers add parent-owned app or media overlays and
  brand the wizard and dashboard from the parent wrapper. They enable or disable
  the services that differ from the creative track.

`infra/services/_user/<name>/compose.yml` is only the discovery slot. Keep the
real overlay file, `compose/<name>-overlay.yml`, in the parent repository and
symlink it into the slot:

```bash
#!/usr/bin/env bash
# scripts/setup-overlay.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SLOT="$ROOT/infra/services/_user/myproject"
OVERLAY="$ROOT/compose/myproject-overlay.yml"

mkdir -p "$SLOT"
ln -sfn "../../../../compose/myproject-overlay.yml" "$SLOT/compose.yml"
test -f "$OVERLAY"
```

Keep the wrapper idempotent, so a fresh clone, a CI checkout or an updated
submodule can run it before every start.

Parent-owned start scripts force-set project wiring instead of setting it only
when absent:

```bash
#!/usr/bin/env bash
# scripts/start-infra.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/setup-overlay.sh"

export ATLAS_ENV_USER_FILE="$ROOT/atlas.env.user"

set_env() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "$ROOT/infra/.env" 2>/dev/null; then
    perl -0pi -e "s/^${key}=.*$/${key}=${value}/m" "$ROOT/infra/.env"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ROOT/infra/.env"
  fi
}

[ -f "$ROOT/infra/.env" ] || cp "$ROOT/infra/.env.example" "$ROOT/infra/.env"
set_env PROJECT_NAME myproject
set_env BRAND_NAME "My Project"
set_env BRAND_TAGLINE "Project-owned Atlas infrastructure"
set_env N8N_SOURCE container
set_env MINIO_SOURCE container

"$ROOT/infra/start.sh" \
  --track gen-ai-rag \
  --n8n-source container \
  --minio-source container
```

Do not use a `set_env_default` helper for project-critical source choices.
`.env.example` ships defaults for many `*_SOURCE` keys, so "set only if absent"
often does nothing. Force-set the value in the wrapper or pass the matching CLI
flag.

Explicit `--<service>-source` flags override the selected `--track`. A consumer
can start from a broad track and add one service outside it, or disable a
service that the track would prompt for.

| Area | Parent repository owns | `infra/` submodule owns |
|------|------------------------|-------------------------|
| Atlas version | The submodule pointer to a reviewed Atlas commit or tag | The checked-out Atlas source at that pointer |
| Service overlays | `compose/<name>-overlay.yml`, app images, plugin mounts, wrapper-owned ports | `services/_user/<name>/compose.yml` symlink discovery slot |
| Environment | Committed templates such as `atlas.env.user.example`, CI secret references, wrapper force-set values | Local `.env`, optional local `.env.user`, generated backfills |
| Branding | `PROJECT_NAME`, `BRAND_*`, and project-specific start/stop scripts | Wizard/dashboard code that consumes those values |
| Data and secrets | Secret names or references in the parent deployment system | Runtime volumes, generated credentials, local `.env` values |
| Object storage extension | `MINIO_EXTRA_CONSUMERS` plus referenced parent-owned bucket/access/secret vars | Generic `minio-init` hook that provisions declared buckets and scoped service accounts |
| Database extension | Parent-reviewed SQL templates or migration source | Optional `services/supabase/db/_user/*.sql` execution slot |

Validation checklist before committing a parent consumer update:

- `git -C infra status --short` is clean after `scripts/start-infra.sh` runs.
  Ignored `.env`, `.env.user`, `_user` slots and runtime volumes do not count.
- The parent commit pins `infra/` to a specific Atlas commit or release tag. It
  does not track a moving branch.
- `infra/services/_user/<name>/compose.yml` is a symlink or generated pointer to
  a parent-owned overlay.
- Parent-owned buckets use `MINIO_EXTRA_CONSUMERS` in the overlay. The bucket,
  access and secret variables live in `.env.user` or `ATLAS_ENV_USER_FILE`.
- The wrapper force-sets project-critical `*_SOURCE`, `PROJECT_NAME` and
  `BRAND_*` values, or passes them as explicit CLI flags.
- The wrapper documents the chosen `--track` and every source override that
  differs from it.

#### 4.2.1. Migrating to atlas.consumer.yml

The manifest covers everything this layout does: the force-set
`PROJECT_NAME`/`BRAND_*` values, the `*_SOURCE` overrides, the
`services/_user/<name>/compose.yml` overlay, backend plugin mounts and
`MINIO_EXTRA_CONSUMERS` buckets. After you migrate, delete the symlink,
`setup-overlay.sh` and `.env.user`. The launcher becomes
`./infra/start.sh --consumer ./atlas.consumer.yml`; the manifest's
`project_name` sets `PROJECT_NAME`. For every manifest key, see the
[Consumer Manifest Reference](../reference/consumer-manifest.md).

### 4.3. Parent .gitignore Configuration

A parent repository's `.gitignore` has no effect on paths inside the
submodule, so you do not need entries for `infra/`. Atlas's own `.gitignore`
ignores `.env`, `.env.user`, the generated files under `volumes/`, `data/` and
the files in `services/supabase/db/_user/`.

For downstream-only environment keys that must survive `.env` regeneration, use
`infra/.env.user` or a parent-owned external overlay. Prefer the external
overlay: it lives in the parent repository, where you can commit or template it.

```bash
# myproject/atlas.env.user
PROJECT_NAME=myproject
BRAND_NAME=My Project
OLLAMA_CUSTOM_MODELS=llama3.1:8b
WEAVIATE_MEMORY_LIMIT=2g

# From myproject/
ATLAS_ENV_USER_FILE="$PWD/atlas.env.user" ./infra/start.sh
```

On every start, including `--cold`, Atlas applies overlays in this order:
`infra/.env.user`, then `ATLAS_ENV_USER_FILE`, then the consumer manifest's
`env.values`, then CLI flags such as `--project`. It then backfills missing keys
from `.env.example`. A missing or unreadable `ATLAS_ENV_USER_FILE` gives a
warning and is skipped.

`start.sh` resolves a relative `ATLAS_ENV_USER_FILE` against the directory that
called the wrapper. A direct Python invocation resolves it against its current
working directory.

For downstream-owned Supabase SQL, use `infra/services/supabase/db/_user/`.
`supabase-db-init` runs these files in lexical order after
`infra/services/supabase/db/scripts/*.sql`. Write them idempotently, because a
database volume can be reused across starts. Atlas's
`services/supabase/db/_user/.gitignore` keeps the SQL from dirtying the
submodule; version the migrations in the parent repository.

## 5. Configuration

### 5.1. PROJECT_NAME: The Key to Isolation

`PROJECT_NAME` prefixes every Docker resource, so several stacks do not
conflict:

- **Networks**: `${PROJECT_NAME}-network`
- **Containers**: `${PROJECT_NAME}-supabase-db`, `${PROJECT_NAME}-ollama`, etc.
- **Volumes**: `${PROJECT_NAME}-supabase-db-data`, `${PROJECT_NAME}-redis-data`, etc.

For `PROJECT_NAME=myproject`, the network is `myproject-network`, a container is
`myproject-supabase-db`, and a volume is `myproject-supabase-db-data`.
Host-published ports are not prefixed; a second stack also needs its own
`BASE_PORT` ([Reusing Atlas §7.4](reusing-atlas.md#74-run-multiple-atlas-instances-on-one-host)).

**start and stop both honor it.** `./start.sh` and `./stop.sh` read
`PROJECT_NAME` from `.env` and pass it as `docker compose -p <name>`. A bare
`./infra/stop.sh` therefore stops exactly the stack that `./infra/start.sh`
launched, not a base Atlas stack.

Set the name with the manifest's `project_name` (§2.2) or with `--project`. Both
persist to `.env`, so a later bare start or stop uses the same name:

```bash
./infra/start.sh --project myproject     # or -p myproject
./infra/stop.sh                          # reads PROJECT_NAME=myproject from .env
./infra/stop.sh --project myproject      # or be explicit
```

The name is lower-cased and must match Docker Compose's project-name rules
(`[a-z0-9][a-z0-9_-]*`). Atlas rejects an invalid name before it starts. The
interactive wizard's **Project name** step also writes it to `.env`; its default
is the current value.

### 5.2. Custom Environment File Location (Advanced)

To keep the infrastructure configuration in the parent project, set
`ATLAS_ENV_FILE`. The legacy name `GENAI_ENV_FILE` still works as a deprecated
alias and prints a one-time warning on stderr.

```bash
# Parent project structure
myproject/
├── config/
│   ├── dev.env      # Development infrastructure config
│   ├── prod.env     # Production infrastructure config
│   └── test.env
└── infra/           # atlas submodule

# Start with custom config location
ATLAS_ENV_FILE=../config/prod.env ./infra/start.sh
```

A relative `ATLAS_ENV_FILE` resolves against the Atlas checkout (`infra/`), not
against the caller's directory. `./stop.sh` reads the same variable, so export
it for `./infra/stop.sh` too.

Uses:
- Centralized configuration management
- CI/CD pipelines with secret injection
- Running multiple instances with different configurations

### 5.3. Port Configuration

By default, services use the block that starts at port 63000. To move it,
set `BASE_PORT` in the manifest's `env.values` (§2.2). A `--base-port` flag
writes only to `.env`, and a cold start resets it
([Reusing Atlas §7.2](reusing-atlas.md#72-pin-instance-identity-in-the-manifest-not-just-env)).

## 6. Integration Patterns

### 6.1. Pattern 1: Docker Network Integration

Connect your application services to the Atlas network.

**Parent docker-compose.yml:**

```yaml
networks:
  # Connect to atlas network
  infra-network:
    external: true
    name: myproject-network  # Must match PROJECT_NAME

services:
  my-app:
    build: ./src/backend
    networks:
      - infra-network
    environment:
      # Reach Atlas services by their compose service name
      DATABASE_URL: postgresql://${SUPABASE_DB_USER}:${SUPABASE_DB_PASSWORD}@supabase-db:5432/postgres
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379
      LITELLM_BASE_URL: http://litellm:4000
      LITELLM_API_KEY: ${LITELLM_MASTER_KEY}
      KONG_URL: http://kong-api-gateway:8000
    ports:
      - "8080:8080"
```

The parent Compose project does not read `infra/.env`. Export
`SUPABASE_DB_USER`, `SUPABASE_DB_PASSWORD`, `REDIS_PASSWORD` and
`LITELLM_MASTER_KEY` to it from your secret store. Service names are stable and
do not depend on `PROJECT_NAME`
([Reusing Atlas §3.3](reusing-atlas.md#33-service-addresses-inside-the-shared-network)).

The Atlas services belong to a separate Compose project, so they cannot appear
in this file's `depends_on`. Start Atlas first, as shown below. If the
application needs a stronger guarantee, make its entrypoint wait on the Atlas
health endpoint it consumes.

**Start both stacks:**

```bash
# Start infrastructure first; --detach returns after the health gates pass
./infra/start.sh --consumer ./atlas.consumer.yml --no-tui --detach

# Start your application
docker compose up -d
```

### 6.2. Pattern 2: Kong Gateway for Routed Services

Use Kong (`BASE_PORT` + 0) for services whose manifests declare Kong routes.
Database, queue and other TCP integrations stay direct; use
`./start.sh endpoints export` for their canonical endpoint contracts. See
[Ports and Routes](./ports-and-routes.md) for the routed and direct inventory.

```python
# Python example
import os
import requests

KONG_BASE = "http://localhost:63000"  # default BASE_PORT + 0

# Access Supabase REST through Kong (path-routed)
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")  # from infra/.env
response = requests.get(f"{KONG_BASE}/rest/v1/your-table",
                        headers={"apikey": SUPABASE_ANON_KEY})

# Other services are HOST-routed through Kong, not path-routed:
n8n_url = "http://n8n.localhost:63000"        # needs --setup-hosts entries
```

```javascript
// JavaScript example
const KONG_BASE = "http://localhost:63000";  // default BASE_PORT + 0

// Supabase REST/auth are path-routed on the Kong root:
const supabaseRest = `${KONG_BASE}/rest/v1/`;
// Other Kong-enabled services are HOST-routed (requires *.localhost hosts entries):
const n8nUrl = "http://n8n.localhost:63000";
const jupyterUrl = "http://jupyter.localhost:63000";
```

### 6.3. Pattern 3: Direct Port Access

Access services directly via their exposed ports:

```python
import os

# Development configuration
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:63040")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY")  # equals LITELLM_MASTER_KEY
SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:63000")  # Kong gateway; clients add /rest/v1
# Redis always requires a password. Atlas's own REDIS_URL uses the in-network
# host `redis`, so build the host URL from REDIS_PASSWORD and REDIS_PORT.
REDIS_URL = f"redis://:{os.getenv('REDIS_PASSWORD')}@localhost:{os.getenv('REDIS_PORT', '63025')}/0"
```

### 6.4. Pattern 4: Service Extension

> To **co-launch a service inside the Atlas stack**, use a manifest-declared overlay. Such a service starts and stops with `./start.sh` / `./stop.sh` and joins the network. For existing integrations, the back-compatible `services/_user/` slot also works ([Reusing Atlas §6.1.1](reusing-atlas.md#611-back-compatible-services_user-overlay-slot)). Use the parent-compose pattern below when your own Compose project manages the service.

Extend infrastructure services with custom functionality:

```yaml
# Parent docker-compose.yml
services:
  custom-processor:
    build: ./src/processor
    networks:
      - infra-network
    environment:
      # Process data from Weaviate
      WEAVIATE_URL: http://weaviate:8080
      # Store results in Supabase (REST is path-routed on Kong's root)
      SUPABASE_URL: http://kong-api-gateway:8000
    volumes:
      - ./data:/data
```

### 6.5. Complete Integration Example

**scripts/start-all.sh:**

```bash
#!/bin/bash
set -e

echo "Starting infrastructure..."
# --detach exits 0 only after Atlas's health gates pass
./infra/start.sh --consumer ./atlas.consumer.yml --no-tui --detach

echo "Starting application services..."
docker compose up -d

echo "All services started!"
echo "Infrastructure: Kong on BASE_PORT (see the startup output)"
echo "Application: http://localhost:8080"
```

**scripts/stop-all.sh:**

```bash
#!/bin/bash

echo "Stopping application services..."
docker compose down

echo "Stopping infrastructure..."
./infra/stop.sh

echo "All services stopped!"
```

For `--detach`, `--json` and the CI preflight, see
[Reusing Atlas §6.1.3](reusing-atlas.md#613-scripted-bring-up-for-automation).

## 7. Contributing Back

`infra/` is a normal git checkout. To contribute, fork Atlas, commit on a branch
inside `infra/`, push to your fork, and open a pull request against `develop`
([Contributing guide](../../CONTRIBUTING.md)).
After the change reaches `main` in a release, bump the submodule pointer in the
parent repository. Keep `.env` and other project settings local.

To carry private changes, keep them on a branch and rebase it onto `main` after
each upgrade. GitHub documents the
[fork and pull-request mechanics](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests-and-forks).

## 8. Troubleshooting

### 8.1. Issue: Port Conflicts

**Symptom**: Services fail to start due to port already in use.

**Solution 1**: Move the port block. Set `BASE_PORT: auto` (or a fixed
non-default block) in the manifest's `env.values` (§2.2).

**Solution 2**: Stop conflicting services
```bash
# Find what's using the port
lsof -i :63000

# Stop the conflicting service
```

### 8.2. Issue: Docker Network Already Exists

**Symptom**: Error creating network `${PROJECT_NAME}-network`.

**Solution**: Give each stack a unique `project_name` in its manifest, for
example `myproject-dev`.

### 8.3. Issue: Submodule Not Updating

**Symptom**: Changes from upstream don't appear in your submodule.

**Solution**: Move the pin explicitly to a new tag or reviewed `main` commit:
```bash
git -C infra fetch --tags origin
git -C infra checkout <tag-or-reviewed-main-sha>
git add infra && git commit -m "infra: bump Atlas to <tag-or-sha>"
```

### 8.4. Issue: Can't Access Services from Application

**Symptom**: Application can't connect to infrastructure services.

**Solution 1**: Verify network connection
```bash
# Check if networks are shared
docker network inspect myproject-network

# Ensure your app service is on the same network
```

**Solution 2**: Use correct hostnames
```bash
# From within Docker: use the compose service name and container port
DATABASE_URL=postgresql://user:pass@supabase-db:5432/postgres

# From host machine: use localhost and the published port (BASE_PORT + 12)
DATABASE_URL=postgresql://user:pass@localhost:63012/postgres
```

### 8.5. Issue: .env Changes Not Taking Effect

**Symptom**: Updated `.env` values don't apply to running services.

**Solution**: Run a normal start. Every `./start.sh` recreates the containers
(`--force-recreate`) with the current `.env`. A cold start would instead rebuild
`.env` from `.env.example`, which loses the edit, and delete the project
volumes.
```bash
./infra/start.sh
```

### 8.6. Issue: Permission Denied for Volumes

**Symptom**: Permission errors when services try to write to volumes.

**Solution**: Check the ownership of the directory named in the error. If your
user created it, give it back to your user and group:
```bash
sudo chown -R "$(id -u):$(id -g)" ./infra/volumes/<service-dir>
```

### 8.7. Issue: Submodule Shows Modifications

**Symptom**: `git status` shows infra/ as modified even though you didn't change it.

**Solution**: The parent records one submodule commit, and `infra/` is checked
out at a different one.
```bash
# See what changed
git -C infra status

# If you want to keep current version
git add infra
git commit -m "Update submodule reference"

# If you want to reset to committed version
git submodule update --init
```

**The launcher never moves the pin.** After each start and stop, Atlas makes a
read-only check. `infra/` HEAD must match the parent's gitlink, and no pointer
change may be staged. On a mismatch it prints both commits and the re-pin command,
then continues. It never checks out, pulls or stages anything. The `--no-tui`
flow runs the check after a successful start only.

**A normal `./start.sh` never dirties the Atlas checkout.** Every file it writes
inside the tree is gitignored. These are `.env`, `.env.backup.*` and the
generated files under `volumes/`: Kong routes, LiteLLM configs, consumer
overlays and ComfyUI manifests. If `git -C infra status` shows tracked-file changes after a start,
file an Atlas bug. If an update fails on a locally modified file, run
`git -C infra checkout -- <path>` and retry.

## 9. Advanced Topics

### 9.1. Running Multiple Infrastructure Stacks

Run several Atlas stacks on one host by giving each its own manifest identity:

```yaml
# ~/project1/atlas.consumer.yml
project_name: project1
env:
  values:
    BASE_PORT: auto

# ~/project2/atlas.consumer.yml
project_name: project2
env:
  values:
    BASE_PORT: auto
```

Each stack gets its own networks, volumes, container names and port block.
`BASE_PORT: auto` reserves a free block per consumer and keeps it across
restarts. Do not set `PROJECT_NAME` as a shell-env prefix; the bootstrapper
does not read it, and the two stacks collide. See
[Reusing Atlas §7.4](reusing-atlas.md#74-run-multiple-atlas-instances-on-one-host).

### 9.2. CI/CD Integration

**GitHub Actions example:**

```yaml
name: Test with Infrastructure

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          submodules: recursive  # Important!

      - name: Start Infrastructure
        # --detach exits non-zero unless the health gates pass
        run: ./infra/start.sh --consumer ./atlas.consumer.yml --project "ci-test-${{ github.run_id }}" --no-tui --detach

      - name: Run Tests
        run: |
          npm test

      - name: Stop Infrastructure
        if: always()
        run: ./infra/stop.sh
```

For the consumer doctor and endpoint checks in CI, see
[Reusing Atlas §6.1.4](reusing-atlas.md#614-preflight-and-ci-gates).

### 9.3. Selecting services with `*_SOURCE`

Each service's `*_SOURCE` value selects how it runs or disables it. Set the
values in the manifest's `env.values`:

```yaml
# atlas.consumer.yml
env:
  values:
    # LiteLLM is always on; choose what it forwards to.
    LLM_PROVIDER_SOURCE: ollama-container-cpu  # or 'none' for no Ollama upstream
    CLOUD_OPENAI_SOURCE: disabled
    CLOUD_ANTHROPIC_SOURCE: disabled
    CLOUD_OPENROUTER_SOURCE: disabled
    # Disable unused services
    COMFYUI_SOURCE: disabled
    DOC_PROCESSOR_SOURCE: disabled
```

For every service and value, see [Source Configuration](source-configuration.md).

## 10. Best Practices

1. **Pin Submodule Versions**: Pin to a tested tag or reviewed `main` commit (§8.3).

2. **Document Your Configuration**: Add a README in the parent project that explains the infra setup.

3. **Commit Settings, Not .env**: Commit the non-secret settings in the parent
   repository, not a copy of `infra/.env`. The parent cannot add files inside
   the submodule, and `infra/.env` holds generated secrets. Put them in the
   `env.values` block of `atlas.consumer.yml`
   ([Consumer Manifest Reference §3](../reference/consumer-manifest.md#3-env)),
   which Atlas re-applies on every start.

4. **Use PROJECT_NAME Consistently**: Use the same project name in the manifest, the parent Compose network name and your scripts.

5. **Test Updates in Branches**: Move the pin on a branch and test before merging:
   ```bash
   git checkout -b update-infra
   git -C infra fetch --tags origin
   git -C infra checkout <tag-or-reviewed-main-sha>
   # Test everything
   git add infra
   git commit -m "infra: bump Atlas to <tag-or-sha>"
   ```

## 11. Additional Resources

- [Reusing Atlas as Infrastructure](reusing-atlas.md)
- [Source Configuration](source-configuration.md)
- [Git Submodules Documentation](https://git-scm.com/book/en/v2/Git-Tools-Submodules)
- Container logs: run `docker compose -p <PROJECT_NAME> logs` from `infra/`, or `docker logs <PROJECT_NAME>-<service>`. A bare `docker compose logs` in `infra/` uses the folder name, `infra`, as the project.
