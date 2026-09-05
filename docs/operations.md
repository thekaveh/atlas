# 8.1. Operations

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
./start.sh managed-host list
./start.sh managed-host preflight|install|start|stop|status|health|remove <name>
./start.sh comfyui-mps preflight|install|provision|provision-nodes|start|stop|status|health|remove
./start.sh vllm-metal preflight|install|start|stop|status|health|remove
./start.sh blender-mcp preflight|install|start|stop|status|health|remove
./stop.sh
./stop.sh --cold
./stop.sh --clean-hosts
./stop.sh --stop-managed-hosts
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
env references, plugin directories, plugin.yml manifest + declared-env
validation, model sidecars, endpoint reporting, and tracked-file cleanliness.
Docker-dependent checks are marked skipped when Docker is unavailable;
Docker-free checks still run. Use `--format json` for CI parsing. Any failed
check exits non-zero.

## 5. Endpoint Contract Export

Use `./start.sh endpoints export --format env|json` to emit a stable,
machine-readable consumer endpoint contract: canonical, distinct
container/host/Kong/public endpoints and active SOURCE modes per
consumer-relevant service (Backend, LiteLLM, ComfyUI, Ollama, MinIO, Weaviate,
Neo4j, n8n, Redis, Supabase), plus every per-consumer `ATLAS_STORE_*` storage
field. The field names are a compatibility contract. Output is secret-free by
default (infra secrets are `${VAR}` references); `--with-secrets` resolves only
consumer-scoped credentials and refuses stdout (requires `--output PATH`).
Output is deterministic and byte-stable, so parent wrappers can diff it across
runs.

When a consumer manifest sets `BASE_PORT: auto`, the port block is allocated at
bring-up. Exporting before that would describe the default block rather than
this stack's, and on a host running several Atlas projects that block plausibly
belongs to another one — so the endpoints would answer from the wrong stack
instead of refusing. The command therefore exits `3` until a block exists. Pass
`--allow-unresolved` for the legitimate pre-allocation cases (CI templating,
committing a sample) so the ambiguity is chosen rather than stumbled into. See
[reusing-atlas.md §6.5](https://github.com/thekaveh/atlas/blob/main/docs/deployment/reusing-atlas.md).

## 6. Backend Plugin Manifest

A backend plugin package mounted under `BACKEND_PLUGINS_DIR` may ship an optional
`plugin.yml` (`plugin_manifest_version: 1`) declaring a typed, validated
contract: `name`, `route_prefix`, `health_path`/`docs_url`, `auth:
inherit|open|key-auth`, optional per-plugin Kong upstream timeouts, and
typed/`default`/`required`/`secret` `env`. Absent
manifests inherit the Backend application identity boundary. A present-but-malformed
manifest skips only that plugin with a structured error and leaves others
healthy; duplicate names, overlapping prefixes, and prefixes shadowing a built-in
backend route are rejected before mounting. Declared env is validated at startup
and by the consumer doctor (required-missing / enum / type warnings, secrets
masked as `***`). Internal-service-authenticated `GET /plugins` returns the
resulting inventory. Per-plugin `auth` composes into Kong and application
policies: `inherit` requires Backend identity, `key-auth` validates
`BACKEND_KONG_API_KEY` at both layers, and only explicit `open` routes are
public. Timeout-bearing plugins receive dedicated Kong services so their
strict millisecond `connect_timeout`, `write_timeout`, and `read_timeout`
overrides do not affect other backend routes; omitted fields retain Kong's
defaults. See
[reusing-atlas.md §6.3.1](https://github.com/thekaveh/atlas/blob/main/docs/deployment/reusing-atlas.md#631-declaring-a-typed-plugin-contract-with-pluginyml).

## 7. Health And Logs

The launch phase streams Docker Compose output through the Textual UI. The same command path works without the TUI in non-interactive environments.

## 8. Managed Host Lifecycle

ComfyUI MPS and vLLM Metal on Apple Silicon, plus headless Blender MCP, run as
native host processes outside Docker Compose. Atlas starts selected managed hosts only after
configuration, dependency, route, host, and localhost validation completes and
the operator confirms launch. If image build, Compose startup, or a required
init container fails, startup rolls back only the host processes created by
that invocation; a host that was running beforehand remains untouched. A
state-directory launch lock serializes concurrent launchers so exactly one can
own a newly created process.

After the stack converges, the native processes remain part of the running
Atlas deployment — and a plain `./stop.sh` deliberately leaves them running.
These runtimes are host-global: another consumer on the same machine may be
using the same ComfyUI-MPS, vLLM-Metal or Blender-MCP process, and SOURCE
cannot prove ownership because `.env` is mutable and the state directory is
shared. Stopping them is therefore an explicit opt-in, not a default.

`./stop.sh` reports which managed runtimes it left running and exits on the
container teardown result alone. `./stop.sh --stop-managed-hosts` additionally
tears down the three built-in managed host runtimes — ComfyUI-MPS, vLLM-Metal
and Blender-MCP — from their state directories, and a native process still live
after that attempt makes the command exit nonzero. `--cold` does not change
this behavior.

A consumer-declared managed host process (`managed_host_services` in
`atlas.consumer.yml`) is outside `./stop.sh`'s scope entirely: it is neither
stopped by `--stop-managed-hosts` nor listed in the left-running advisory. Stop
one explicitly with `./start.sh managed-host stop <name>`.
