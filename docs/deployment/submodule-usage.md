# Using atlas as a Git Submodule

This guide explains how to use Atlas as a git submodule in your project, allowing you to build on top of it as an infrastructure foundation while maintaining the ability to contribute back to the project.

> **New here?** Start with [Reusing Atlas as Infrastructure](reusing-atlas.md) — it compares all the reuse methods (standalone shared-network vs submodule vs fork), states what's ready, and walks a concrete consumer example. This page is the deep-dive for the **submodule** method specifically.

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
- [Getting Help](#12-getting-help)

## 2. Quick Start

### 2.1 Add atlas as a Submodule

In your project root, add atlas as a submodule in an `infra/` directory:

```bash
# Add Atlas as the submodule (use your fork's URL if you maintain one)
git submodule add https://github.com/thekaveh/atlas.git infra

# Initialize and update the submodule
git submodule init
git submodule update
```

### 2.2 Configure the Environment

```bash
cd infra

# Copy the example configuration
cp .env.example .env

# Edit .env and customize PROJECT_NAME
# IMPORTANT: Set PROJECT_NAME to match your project name
vim .env
```

**Critical Configuration:**
```bash
# In infra/.env
PROJECT_NAME=myproject  # Change from 'atlas' to your project name
```

### 2.3 Start the Infrastructure

```bash
# From the infra directory
./start.sh

# Or from your project root
(cd infra && ./start.sh)
```

### 2.4 Access Services

Services are accessible on ports starting from 63000 (base port):
- **Supabase DB**: http://localhost:63012 (base + 12)
- **Supabase Studio**: http://localhost:63019 (base + 19)
- **Kong API Gateway**: http://localhost:63000 (base + 0)
- **N8N**: http://localhost:63075 (base + 75)
- **LiteLLM Gateway** (LLM front door): http://localhost:63040 (base + 40)

See the startup output for the complete port mapping of all services.

## 3. Why Use as a Submodule?

Using atlas as a git submodule provides these capabilities:

- Separation of infrastructure code from application code
- Ability to pull upstream improvements while maintaining local configurations
- Project-specific environment settings tracked in parent repository
- Standard git workflow for contributing improvements back to atlas
- Multiple independent instances with isolated Docker resources (networks, volumes, containers)
- Infrastructure version pinning to specific commits or tags

## 4. Project Structure

### 4.1 Recommended Directory Layout

```
myproject/
├── .git/
├── .gitmodules              # Git submodule configuration
├── src/                     # Your application code
│   ├── backend/
│   ├── frontend/
│   └── ...
├── infra/                   # atlas submodule
│   ├── .git -> ../.git/modules/infra
│   ├── .env                 # Your custom configuration (gitignored)
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

### 4.2 Parent .gitignore Configuration

Add these entries to your parent project's `.gitignore`:

```
# Infrastructure environment and data
infra/.env
infra/.env.user
infra/services/supabase/db/_user/*.sql
infra/volumes/
infra/data/

# Keep .env.example for documentation
!infra/.env.example
```

Use either `infra/.env.user` or a parent-owned external overlay for downstream-only environment keys that should survive Atlas `.env` regeneration without being added to upstream `.env.example`. The external overlay is usually better for submodule consumers because it lives in the parent repository and can be committed or templated there:

```bash
# myproject/atlas.env.user
PROJECT_NAME=myproject
BRAND_NAME=My Project
OLLAMA_CUSTOM_MODELS=llama3.1:8b
WEAVIATE_MEMORY_LIMIT=2g

# From myproject/
ATLAS_ENV_USER_FILE="$PWD/atlas.env.user" ./infra/start.sh
```

During setup, Atlas copies `.env.example` when needed, merges sibling `infra/.env.user`, then merges `ATLAS_ENV_USER_FILE`, and then applies explicit CLI flags such as `--project` last. Both overlays are applied on every start, including `--cold`, before Atlas backfills missing keys from `.env.example`. If `ATLAS_ENV_USER_FILE` is relative, `start.sh` resolves it against the parent directory that invoked the wrapper; direct Python invocations resolve it against their current working directory. Missing or unreadable external overlay files produce a warning rather than aborting startup.

Use `infra/services/supabase/db/_user/` for downstream-owned Supabase SQL that should run after Atlas-owned database initialization. Files are executed by `supabase-db-init` in lexical order after `infra/services/supabase/db/scripts/*.sql`; write them idempotently because the same database volume may be reused across starts. The parent `.gitignore` entry above keeps local SQL from making the Atlas submodule look dirty unless your project intentionally versions those migrations through its own overlay strategy.

## 5. Configuration

### 5.1 PROJECT_NAME: The Key to Isolation

The `PROJECT_NAME` environment variable is critical for submodule usage. It prefixes all Docker resources to prevent conflicts:

**Docker Resources Prefixed with PROJECT_NAME:**
- **Networks**: `${PROJECT_NAME}-network`
- **Containers**: `${PROJECT_NAME}-supabase-db`, `${PROJECT_NAME}-ollama`, etc.
- **Volumes**: `${PROJECT_NAME}-supabase-db-data`, `${PROJECT_NAME}-redis-data`, etc.

**Example:**

```bash
# In infra/.env
PROJECT_NAME=myproject
```

Results in:
- Network: `myproject-network`
- Container: `myproject-supabase-db`
- Volume: `myproject-supabase-db-data`

This allows multiple projects to use atlas simultaneously without conflicts.

**start and stop both honor it.** `./start.sh` and `./stop.sh` both read
`PROJECT_NAME` from `.env` and pass it as `docker compose -p <name>`, so a bare
`./infra/stop.sh` tears down **exactly** the family `./infra/start.sh` launched —
not a base Atlas stack. As a submodule consumer you only need to set
`PROJECT_NAME` once in `infra/.env`.

You can also pass it explicitly (it persists back to `.env`, so the next bare
start/stop keeps agreeing):

```bash
./infra/start.sh --project myproject     # or -p myproject
./infra/stop.sh                          # reads PROJECT_NAME=myproject from .env
./infra/stop.sh --project myproject      # or be explicit
```

The name is lower-cased and must match Docker Compose's project-name rules
(`[a-z0-9][a-z0-9_-]*`); an invalid name is rejected up front. The interactive
wizard also has a **Project name** step (defaults to the current value) that
writes it to `.env`.

### 5.2 Custom Environment File Location (Advanced)

If you prefer to manage your infrastructure configuration from the parent project, you can use the `ATLAS_ENV_FILE` environment variable (the legacy name `GENAI_ENV_FILE` is still honored as a deprecated alias with a one-shot stderr warning):

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

This is useful for:
- Centralized configuration management
- CI/CD pipelines with secret injection
- Running multiple instances with different configurations

### 5.3 Port Configuration

By default, services start at port 63000. If these ports conflict with your application:

```bash
# Use custom base port
./start.sh --base-port 64000

# Or set in .env
BASE_PORT=64000
```

## 6. Integration Patterns

### 6.1 Pattern 1: Docker Network Integration

Connect your application services to the Atlas network.

**Parent docker-compose.yml:**

```yaml
networks:
  # Connect to atlas network
  infra-network:
    external: true
    name: myproject-network  # Must match PROJECT_NAME in infra/.env

services:
  my-app:
    build: ./src/backend
    networks:
      - infra-network
    environment:
      # Access infrastructure services by container name
      DATABASE_URL: postgresql://postgres:password@myproject-supabase-db:5432/postgres
      REDIS_URL: redis://:password@myproject-redis:6379
      LITELLM_BASE_URL: http://myproject-litellm:4000
      LITELLM_API_KEY: ${LITELLM_MASTER_KEY}
      KONG_URL: http://myproject-kong-api-gateway:8000
    ports:
      - "8080:8080"
    depends_on:
      - myproject-supabase-db  # Ensure infra is running
```

**Start both stacks:**

```bash
# Start infrastructure first
cd infra && ./start.sh && cd ..

# Start your application
docker compose up -d
```

### 6.2 Pattern 2: Kong Gateway as Single Entry Point

Use Kong (port 63000) to access all infrastructure services from your application:

```python
# Python example
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
// Everything else is HOST-routed (requires the *.localhost hosts entries):
const n8nUrl = "http://n8n.localhost:63000";
const jupyterUrl = "http://jupyter.localhost:63000";
```

### 6.3 Pattern 3: Direct Port Access

Access services directly via their exposed ports:

```python
import os

# Development configuration
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:63040")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY")  # equals LITELLM_MASTER_KEY
SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:63017")  # SUPABASE_API_PORT
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:63025")
```

### 6.4 Pattern 4: Service Extension

> For a service that should **co-launch inside the Atlas stack** (start/stop with `./start.sh` / `./stop.sh`, share the network automatically), prefer the `services/_user/` overlay — drop `services/_user/<name>/compose.yml` and the bootstrapper auto-merges it. See [reusing-atlas.md §6.1](reusing-atlas.md#61-extending-the-stack-via-services_user). The parent-compose pattern below is the alternative when you want your service managed by your *own* Compose project rather than Atlas's.

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
      WEAVIATE_URL: http://myproject-weaviate:8080
      # Store results in Supabase (REST is path-routed on Kong's root)
      SUPABASE_URL: http://myproject-kong-api-gateway:8000
    volumes:
      - ./data:/data
```

### 6.5 Complete Integration Example

**scripts/start-all.sh:**

```bash
#!/bin/bash
set -e

echo "Starting infrastructure..."
cd infra && ./start.sh && cd ..

echo "Waiting for services to be ready..."
sleep 10

echo "Starting application services..."
docker compose up -d

echo "All services started!"
echo "Infrastructure: http://localhost:63000"
echo "Application: http://localhost:8080"
```

**scripts/stop-all.sh:**

```bash
#!/bin/bash

echo "Stopping application services..."
docker compose down

echo "Stopping infrastructure..."
cd infra && ./stop.sh && cd ..

echo "All services stopped!"
```

## 7. Contributing Back

When using atlas as a submodule, you can contribute improvements back to the project using the standard git workflow.

### 7.1 Contribution Workflow

#### 7.1.1 Create a Fork

```bash
# Fork the Atlas repository to your account on GitHub
# Then add your fork as a remote (replace with your fork URL)

cd infra
git remote add fork <your-fork-url>
```

#### 7.1.2 Create a Feature Branch

```bash
cd infra
git checkout -b feature/my-improvement
```

#### 7.1.3 Make Your Changes

```bash
# Edit files in the infra/ directory
vim bootstrapper/start.py

# Test your changes
./start.sh

# Commit your changes
git add .
git commit -m "Add feature: improved startup validation"
```

#### 7.1.4 Push to Your Fork

```bash
git push fork feature/my-improvement
```

#### 7.1.5 Create Pull Request

- Go to GitHub and create a PR from your fork's branch
- Target the original atlas repository's `main` branch
- Describe your changes and their benefits

#### 7.1.6 Update Submodule After Merge

Once your PR is merged:

```bash
cd infra
git checkout main
git pull origin main

# Update parent repository to track new commit
cd ..
git add infra
git commit -m "Update atlas submodule to latest version"
```

### 7.2 Local Customizations vs. Contributions

**Keep as local changes (don't contribute):**
- `.env` configuration
- Project-specific customizations
- Temporary debugging changes

**Consider contributing back:**
- Bug fixes
- New service integrations
- Performance improvements
- Documentation enhancements
- Error handling improvements

### 7.3 Maintaining Local Customizations

If you need to maintain local customizations while staying up-to-date:

```bash
cd infra

# Create a local customization branch
git checkout -b local-customizations

# Make your custom changes
vim services/custom-service/docker-compose.yml

git add .
git commit -m "Local: Add company-specific service"

# When upstream updates are available
git fetch origin
git rebase origin/main

# Resolve conflicts if any
```

## 8. Troubleshooting

### 8.1 Issue: Port Conflicts

**Symptom**: Services fail to start due to port already in use.

**Solution 1**: Use custom base port
```bash
./infra/start.sh --base-port 64000
```

**Solution 2**: Stop conflicting services
```bash
# Find what's using the port
lsof -i :63000

# Stop the conflicting service
```

### 8.2 Issue: Docker Network Already Exists

**Symptom**: Error creating network `${PROJECT_NAME}-network`.

**Solution**: Ensure PROJECT_NAME is unique across your system
```bash
# In infra/.env
PROJECT_NAME=myproject-dev  # Make it unique
```

### 8.3 Issue: Submodule Not Updating

**Symptom**: Changes from upstream don't appear in your submodule.

**Solution**: Update the submodule explicitly
```bash
cd infra
git checkout main
git pull origin main

cd ..
git add infra
git commit -m "Update submodule"
```

### 8.4 Issue: Can't Access Services from Application

**Symptom**: Application can't connect to infrastructure services.

**Solution 1**: Verify network connection
```bash
# Check if networks are shared
docker network inspect myproject-network

# Ensure your app service is on the same network
```

**Solution 2**: Use correct hostnames
```bash
# From within Docker: use container names
DATABASE_URL=postgresql://user:pass@myproject-supabase-db:5432/db

# From host machine: use localhost
DATABASE_URL=postgresql://user:pass@localhost:63012/db
```

### 8.5 Issue: .env Changes Not Taking Effect

**Symptom**: Updated `.env` values don't apply to running services.

**Solution**: Restart with cold start
```bash
./infra/stop.sh
./infra/start.sh --cold
```

### 8.6 Issue: Permission Denied for Volumes

**Symptom**: Permission errors when services try to write to volumes.

**Solution**: Check volume ownership
```bash
# Fix permissions
sudo chown -R $USER:$USER ./infra/volumes/
```

### 8.7 Issue: Submodule Shows Modifications

**Symptom**: `git status` shows infra/ as modified even though you didn't change it.

**Solution**: This is normal - the submodule tracks a specific commit
```bash
# See what changed
cd infra
git status

# If you want to keep current version
cd ..
git add infra
git commit -m "Update submodule reference"

# If you want to reset to committed version
git submodule update --init
```

## 9. Advanced Topics

### 9.1 Running Multiple Infrastructure Stacks

You can run multiple instances of atlas for different projects:

```bash
# Project 1 — set PROJECT_NAME in the infra/.env (a shell-env prefix is NOT
# read by the bootstrapper: compose would keep project name `atlas` while
# fragment interpolation used the shell value, colliding the two stacks)
cd ~/project1/infra
echo "PROJECT_NAME=project1" >> .env
./start.sh --base-port 63000

# Project 2
cd ~/project2/infra
echo "PROJECT_NAME=project2" >> .env
./start.sh --base-port 64000
```

Each will have isolated:
- Docker networks
- Docker volumes
- Container names
- Exposed ports

### 9.2 CI/CD Integration

**GitHub Actions example:**

```yaml
name: Test with Infrastructure

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
        with:
          submodules: recursive  # Important!

      - name: Start Infrastructure
        run: |
          cd infra
          cp .env.example .env
          echo "PROJECT_NAME=ci-test-${{ github.run_id }}" >> .env
          ./start.sh

      - name: Wait for Services
        run: sleep 30

      - name: Run Tests
        run: |
          npm test

      - name: Stop Infrastructure
        if: always()
        run: cd infra && ./stop.sh
```

### 9.3 Using with Docker Compose Profiles

Optimize which services start based on your needs:

```bash
# In infra/.env, choose your LLM upstreams. LiteLLM is always-on; you only
# pick what it forwards to.
LLM_PROVIDER_SOURCE=ollama-container-cpu  # or 'none' for cloud-only
CLOUD_OPENAI_SOURCE=disabled
CLOUD_ANTHROPIC_SOURCE=disabled
CLOUD_OPENROUTER_SOURCE=disabled

# Disable unused services
COMFYUI_SOURCE=disabled
DOC_PROCESSOR_SOURCE=disabled
```

## 10. Best Practices

1. **Pin Submodule Versions**: In production, lock to specific tested commits or tags
   ```bash
   cd infra
   git checkout <commit-hash-or-tag>
   cd ..
   git add infra
   git commit -m "Lock infrastructure to tested version"
   ```

2. **Document Your Configuration**: Add README in parent project explaining infra setup

3. **Backup Your .env**: Keep template with comments for new team members
   ```bash
   # Create template
   cp infra/.env infra/.env.template
   # Add to git (with secrets removed)
   git add infra/.env.template
   ```

4. **Use PROJECT_NAME Consistently**: Match your project name across all configurations

5. **Test Updates in Branches**: Before updating submodule, test in a branch
   ```bash
   git checkout -b update-infra
   cd infra && git pull origin main && cd ..
   # Test everything
   git add infra
   git commit -m "Update infrastructure"
   ```

## 11. Additional Resources

- [Main atlas README](https://github.com/thekaveh/atlas/blob/main/README.md)
- [Source Configuration](source-configuration.md)
- [Git Submodules Documentation](https://git-scm.com/book/en/v2/Git-Tools-Submodules)

## 12. Getting Help

If you encounter issues:

1. Check the [troubleshooting section](#8-troubleshooting) above
2. Review container logs: `cd infra && docker compose logs`
3. Check the main README and other documentation in `docs/`

---

*This guide is part of the Atlas documentation. For updates and improvements, please contribute back to the project!*
