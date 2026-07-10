# Operations

## 1. Runtime Commands

```bash
./start.sh
./start.sh --no-tui --detach
./start.sh --no-tui --detach --json
./stop.sh
./stop.sh --cold
./stop.sh --clean-hosts
```

## 2. Automation

Use `./start.sh --no-tui --detach` for scripted bring-up. The alias
`--no-follow` is equivalent. Atlas runs the normal start pipeline, waits for
Compose health gates, prints a per-service status summary, and exits instead of
following logs. Add `--json` for machine-readable status in CI or parent-repo
wrappers.

## 3. Launch Flow

The Textual UI handles the wizard, service summary, launch confirmation, and streamed Compose logs. Non-TTY shells use the linear fallback.

## 4. Verification Commands

```bash
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check_doc_links.py
```

## 5. Reset Behavior

Use `./stop.sh --cold` when a service needs a fresh volume state. Use the normal stop path when preserving local state matters.

## 6. Gateway Behavior

Kong aliases depend on hosts setup and generated route configuration. Direct ports remain useful for local smoke tests and troubleshooting.
