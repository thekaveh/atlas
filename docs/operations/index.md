# 7.1. Operations

## 1. Runtime Commands

Every line below is a complete, safe-to-run command:

```bash
./start.sh
./start.sh --consumer ./atlas.consumer.yml
./start.sh env backfill
./start.sh compose validate
./start.sh --consumer ./atlas.consumer.yml compose validate
./start.sh doctor
./start.sh doctor --format json
./start.sh --consumer ./atlas.consumer.yml doctor --format json
./start.sh doctor --bundle ./atlas-support.tar.gz
./start.sh --no-tui --support-bundle ./atlas-support.tar.gz
./start.sh endpoints export --format env
./start.sh endpoints export --format json
./start.sh --no-tui --detach
./start.sh managed-host list
./stop.sh
```

The managed-host families share one lifecycle synopsis. This is **syntax, not
shell** — the bars separate alternative actions, so pick exactly one per
invocation (in a shell, a literal `|` would be parsed as a pipeline):

```text
./start.sh managed-host <action> <name>     actions: preflight|install|start|stop|status|health|remove
./start.sh comfyui-mps <action>             actions: preflight|install|provision|provision-nodes|start|stop|status|health|remove
./start.sh vllm-metal <action>              actions: preflight|install|start|stop|status|health|remove
./start.sh blender-mcp <action>             actions: preflight|install|start|stop|status|health|remove
```

A concrete example of the synopsis form:

```bash
./start.sh comfyui-mps status
```

### 1.1. Destructive and host-mutating commands

Keep these out of scripts and muscle memory — each changes state beyond the
running containers:

```bash
./stop.sh --cold                # DELETES every named Atlas project volume (databases, workflows, models)
./stop.sh --clean-hosts         # edits /etc/hosts (removes Atlas *.localhost entries)
./stop.sh --stop-managed-hosts  # stops Atlas-managed processes running on the host
```

`--no-tui --detach` also accepts `--json` for machine-readable status (see §2).

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

### 4.1. Support bundle

A support bundle packages the doctor results, the effective configuration, and
a log excerpt into one local `.tar.gz` you can attach to an issue. A relative
`PATH` is resolved against the directory you ran `./start.sh` from.

- `./start.sh doctor --bundle PATH` runs the checks and writes the bundle.
- `./start.sh --support-bundle PATH` writes one only if the startup pipeline
  fails:
  - In the Textual app, that is the launch; the log excerpt is the session log.
  - Under `--no-tui`, that is the startup steps; the log excerpt is what the run
    printed. Docker Compose output that goes straight to the terminal is not
    captured.
  - A failure before the pipeline starts (Docker missing, an invalid flag)
    leaves no bundle; run `./start.sh doctor --bundle PATH` then.
  - Stopping the log stream after a successful start (Ctrl+C) is not a failure
    and writes nothing.

**Before anything is written**, the contents are shown:

- **Under `--no-tui` and `doctor`**, in the terminal. With `--format json` the
  preview goes to standard error, so standard output stays clean JSON.
- **In the Textual app**, in the log pane. `bundle.json` is shown in full; each
  log excerpt shows its first and last 20 lines, since the pane already holds
  that log. Ctrl+Q waits until the bundle is written.

The file is created owner-only (`0600`). **Nothing is sent anywhere.** While the
checks run, Python socket connections to anything but loopback or a Unix socket
are refused. That guard does not cover subprocesses such as
`docker compose config`. It also means a check that probes a non-local address
reports that it could not connect, so `doctor --bundle` can differ from plain
`doctor` there.

**Format.** The archive holds `atlas-support-bundle/bundle.json` (schema
`atlas-support-bundle/1`), one `atlas-support-bundle/logs/<name>` file per log
excerpt, and nothing else. The members' owner is fixed and their timestamps are
the bundle's generation time, so archive metadata carries no user or host names.

`bundle.json` contains:

- `checks`: every doctor check, `id` / `status` / `message` / `available`. A
  check that is `skipped` (for example, its service is disabled) or
  `unavailable` (it raised, ran past the time budget, or never started) is
  recorded, not dropped.
- `findings`: each failing or warning check, with the configuration keys it
  names. Each key has its `value`, `origin` and an `action` saying where to
  change it. The origin is the file that sets the key: a consumer manifest or
  its env file, `ATLAS_ENV_USER_FILE`, `.env.user`, or `.env`.
- `config`: the allowlisted keys, with the same `value` / `origin` / `action`.
- `logs`: per excerpt, its original and kept size and whether it was cut.
- `truncation`: every cut the caps forced.
- `omitted`: how many fields the allowlist left out.
- `context`, `host` (OS, CPU architecture, Python version), and `allowlist`.

**Allowlist.** By default the bundle includes only:

- Check `id`, `status` and `message`.
- Configuration keys matching `*_SOURCE`, `BASE_PORT` / `*_PORT`, `*MODEL` /
  `*MODELS`, `PROJECT_NAME`, `ATLAS_PROFILE_APPLIED`, `COMPOSE_PROFILES` and
  `HOST_BIND_IP`.
- The redacted log tails.

`doctor --bundle PATH --include-unlisted` adds check `details` and every other
configuration key. It only adds fields, and they are redacted too.

**Bounds.** Collection is bounded in size and time:

- Each log excerpt keeps its last 256 KiB.
- Each text field keeps 8,192 characters.
- All checks together get a 60-second wall-time budget.
- Redaction patterns are length-bounded, so long log lines stay fast.

Each cut is recorded in `truncation`. If `.env` cannot be read, log excerpts are
left out, because their known secret values could not be scrubbed.

**Redaction is best-effort.** Terminal colour codes are stripped first. Then
every string is scrubbed of:

- URL credentials (`scheme://user:pass@host`);
- `Authorization`, `Cookie` and API-key headers, and `Bearer` / `Basic` /
  `Token` credentials;
- `key=value`, `key: value`, JSON and Python-dict pairs whose key names a
  secret (`password`, `secret`, `token`, `*_key`, and so on);
- PEM private keys, including a block cut off by the log window;
- common token shapes (OpenAI, GitHub, Hugging Face, Slack, AWS, Google, JWT);
- the literal values of secret-named environment keys, from `.env` and from
  the shell environment.

Values made only of lowercase letters, `-` and `_` are not matched by value.
Those are enum toggles such as `disabled`, and the public `.env.example`
placeholders; a pattern above still catches them as `KEY=value`, in a URL or
in a header.

A secret in an unusual shape can still get through, and so can one the
terminal wrapped across lines. Read the preview before you share the file.

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
[reusing-atlas.md §6.5](https://github.com/thekaveh/atlas/blob/main/docs/operations/reusing-atlas.md).

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
[reusing-atlas.md §6.3.1](https://github.com/thekaveh/atlas/blob/main/docs/operations/reusing-atlas.md#631-declaring-a-typed-plugin-contract-with-pluginyml).

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
