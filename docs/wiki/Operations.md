# Operations

## 1. Runtime Commands

```bash
./start.sh
./stop.sh
./stop.sh --cold
./stop.sh --clean-hosts
```

## 2. Launch Flow

The Textual UI handles the wizard, service summary, launch confirmation, and streamed Compose logs. Non-TTY shells use the linear fallback.

## 3. Verification Commands

```bash
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check_doc_links.py
```

## 4. Reset Behavior

Use `./stop.sh --cold` when a service needs a fresh volume state. Use the normal stop path when preserving local state matters.

## 5. Gateway Behavior

Kong aliases depend on hosts setup and generated route configuration. Direct ports remain useful for local smoke tests and troubleshooting.
