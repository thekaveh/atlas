# Operations

## 1. Runtime Commands

```bash
./start.sh
./start.sh --consumer ./atlas.consumer.yml
./start.sh env backfill
./start.sh compose validate
./start.sh --consumer ./atlas.consumer.yml compose validate
./start.sh doctor
./start.sh doctor --format json
./start.sh --consumer ./atlas.consumer.yml doctor --format json
./start.sh endpoints export --format env
./start.sh endpoints export --format json
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

## 3. Headless Validation

Use `./start.sh env backfill` after updating an Atlas submodule pin. It
preserves existing values, appends newly introduced `.env.example` keys, fills
blank values only when the new example carries a non-blank default, and reports
the affected keys by source section. Then run `./start.sh --consumer
./atlas.consumer.yml compose validate` to validate the assembled stack,
including manifest-declared external overlays and back-compatible
`services/_user/<name>/compose.yml` overlays. Exit code `0` means the env
backfill or Compose validation succeeded; `compose validate` returns Compose's
failing status code when validation fails.

## 4. Consumer Doctor

Use `./start.sh --consumer ./atlas.consumer.yml doctor` for consumer CI
preflight before starting containers. The doctor runs an extensible check
registry for consumer manifest validation, Compose validation, `_user` overlay
env references, plugin directories, model sidecars, endpoint reporting, and
tracked-file cleanliness. Docker-dependent checks are marked skipped when Docker
is unavailable; Docker-free checks still run. Use `--format json` for CI
parsing. Any failed check exits non-zero.

## 5. Endpoint Contract Export

Use `./start.sh endpoints export --format env|json` to emit a stable,
machine-readable consumer endpoint contract: canonical, distinct
container/host/Kong/public endpoints and active SOURCE modes per
consumer-relevant service, plus every per-consumer `ATLAS_STORE_*` storage
field. The field names are a compatibility contract. Output is secret-free by
default (infra secrets are `${VAR}` references); `--with-secrets` resolves only
consumer-scoped credentials and refuses stdout (requires `--output PATH`).
Output is deterministic and byte-stable. See
[reusing-atlas.md §6.5](https://github.com/thekaveh/atlas/blob/main/docs/deployment/reusing-atlas.md).

## 6. Launch Flow

The Textual UI handles the wizard, service summary, launch confirmation, and streamed Compose logs. Non-TTY shells use the linear fallback.

## 6. Verification Commands

```bash
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check_doc_links.py
```

## 7. Reset Behavior

Use `./stop.sh --cold` when a service needs a fresh volume state. Use the normal stop path when preserving local state matters.

## 8. Gateway Behavior

Kong aliases depend on hosts setup and generated route configuration. Direct ports remain useful for local smoke tests and troubleshooting.
