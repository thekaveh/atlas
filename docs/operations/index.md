# 7.1. Operations

This page is for operators who run an Atlas stack. It lists the start, stop and
diagnostic commands and explains automation, validation, support bundles and
managed host processes.

Related operator pages:

- [SOURCE Configuration](source-configuration.md): the deployment mode of each service.
- [Ports and Routes](ports-and-routes.md): the port block and the Kong hostnames.
- [Access and Credentials](access-and-credentials.md): login details and secrets.
- [Expected Startup Warnings](expected-startup-warnings.md): log lines you can ignore.
- [Reusing Atlas](reusing-atlas.md) and [Submodule Usage](submodule-usage.md): Atlas inside a parent project.
- [Troubleshooting](../TROUBLESHOOTING.md): recovery steps for failed starts.

## 1. Runtime Commands

Each line below is a complete command. None of them deletes data or edits the
host; §1.1 lists the commands that do. The `--consumer` lines need an
`atlas.consumer.yml` file in the current directory.

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
./start.sh models probe --kind embedding
./start.sh storage inventory
./stop.sh
```

Before a subcommand (`doctor`, `endpoints`, `env`, `compose`, `managed-host`, …) only `--consumer` applies; it is exported for the subcommand. Output-mode flags (`--no-tui`, `--json`, `--no-splash`, `--detach`) are accepted there and ignored. Any other start option there, such as `-p` or `--base-port`, has no effect, and Atlas prints a warning that names it. Subcommands read the project and ports from `.env`.

The managed-host families share one lifecycle synopsis. This is **syntax, not
shell**: the bars separate alternative actions, so pick exactly one per
invocation. A shell would parse a literal `|` as a pipeline.

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
./start.sh storage clean        # deletes removable Ollama/ComfyUI model files; selected or routed ones are retained (--yes skips the prompt)
```

### 1.2. Model capability probe

`./start.sh models probe` measures model capabilities through the running LiteLLM gateway. It does not trust the catalog.

- **What it probes.** Each probe sends one request that has a fixed expected answer. Tool calling: the model must call an offered tool. JSON output: a JSON object that answers 2+2. Vision: name the colour of a red square. Embedding dimension: the same probe that `lightrag-init` uses.
- **Scope.** By default it probes the configured default chat, vision and embedding models, for the capabilities their catalog entry declares. `--model` and `--kind` narrow it.
- **Cost guard.** It prints the request count first. It refuses a run over `--max-requests` (default 20, exit 3), because cloud probes are billed.
- **Verdicts.** Each result is `supported`, `unsupported` or `unavailable`. `unavailable` means the gateway is unreachable or failed, or it answered with an authentication, unknown-model, timeout or rate-limit error. These errors say nothing about the model. A declared capability that measures `unsupported` is a failure, and the command exits 1.
- **Storage.** Results go to the gitignored `volumes/litellm/capability-probes.json`, keyed by model, provider, gateway alias and catalog revision. A stored `supported` or `unsupported` verdict is reused only for the same key. `unavailable` is measured again, and `--refresh` measures everything again. Every result, new or stored, is printed.
- **Connection.** It reaches the gateway on `HOST_BIND_IP` (default `127.0.0.1`) and `LITELLM_PORT`. Probes never run during `./start.sh` and never change model selection.

## 2. Automation

Use `./start.sh --no-tui --detach` for scripted bring-up. The alias
`--no-follow` is equivalent. Atlas runs the normal start pipeline, waits for
Compose health gates, prints a per-service status summary, and exits instead of
following logs. Add `--json` for machine-readable status in CI or parent-repo
wrappers.

**`./start.sh` exit codes and cancelling.** `0` means the stack started. `1`
means it did not: a build, `up` or one-shot init container failed. The error
line ends with the failed container's last 40 log lines. `Ctrl+C` differs by
front end:

- In the wizard (TUI), `130` means `Ctrl+C` interrupted a launch in progress.
  It prints what was left running, and whether this start had already stopped a
  previously running stack. `Ctrl+C` after the launch finished keeps
  its result (`0` or `1`). The wait for one-shot init containers (up to 900 s)
  stops within a few seconds of `Ctrl+C`.
- With `--no-tui`, `Ctrl+C` during startup exits `1` with the same notice;
  after a successful start it only stops following the logs and exits `130`.
  `Ctrl+C` during a `--cold` teardown stops the start and says the teardown may
  already have removed the project's volumes, instead of reporting a failed
  cleanup.

## 3. Headless Validation

Use `./start.sh env backfill` after updating an Atlas submodule pin. It keeps
existing values and appends new `.env.example` keys. It fills a blank value only
when the new example has a non-blank default. It reports the affected keys by
source section. Then run `./start.sh --consumer ./atlas.consumer.yml compose
validate` to validate the assembled stack, including manifest-declared
external overlays and back-compatible `services/_user/<name>/compose.yml`
overlays.

Exit code `0` means the env backfill or Compose validation succeeded.
`compose validate` returns Compose's failing status code when validation fails.

## 4. Consumer Doctor

Use `./start.sh --consumer ./atlas.consumer.yml doctor` for consumer CI
preflight before starting containers. Its checks cover the consumer manifest,
Compose validation, `_user` overlay env references, plugin directories,
`plugin.yml` and declared env, model sidecars, endpoint reporting and
tracked-file cleanliness.
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
  - A failure before the pipeline starts leaves no bundle. Examples: Docker
    unavailable, an unsupported Compose version, a failed `--setup-hosts`, a
    legacy `external` source, an invalid flag. Run
    `./start.sh doctor --bundle PATH` instead.
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

- `checks`: every doctor check, `id` / `status` / `message` / `available`.
  `skipped` checks (for example, the service is disabled) are recorded, not
  dropped. So are `unavailable` checks (it raised, ran past the time budget, or
  never started).
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
placeholders. A pattern above still catches them as `KEY=value`, in a URL or
in a header.

A secret in an unusual shape can still get through, and so can one the
terminal wrapped across lines. Read the preview before you share the file.

## 5. Endpoint Contract Export

Use `./start.sh endpoints export --format env|json` to emit the consumer endpoint contract. It is stable and machine-readable. For each consumer-relevant service it gives the canonical, distinct container, host, Kong and public endpoints and the active SOURCE mode. The services are Backend, LiteLLM, ComfyUI, Ollama, MinIO, Weaviate, Neo4j, n8n, Redis, Supabase and Asset Worker. It also gives every per-consumer `ATLAS_STORE_*` storage field.

- The field names are a compatibility contract.
- Output is secret-free by default: infra secrets are `${VAR}` references. `--with-secrets` resolves only consumer-scoped credentials, and it refuses stdout (it requires `--output PATH`).
- Output is deterministic and byte-stable, so parent wrappers can diff it across runs.

When a consumer manifest sets `BASE_PORT: auto`, the port block is allocated at
bring-up. Before that, an export would describe the default block, which on a
shared host can belong to another Atlas project. The command therefore exits
`3` until a block exists. Pass `--allow-unresolved` only for deliberate
pre-allocation uses, such as CI templating or a committed sample. See
[Reusing Atlas §6.5](reusing-atlas.md#65-exporting-the-endpoint-contract-endpoints-export).

## 6. Backend Plugin Manifest

A backend plugin package under `BACKEND_PLUGINS_DIR` can ship an optional `plugin.yml` (`plugin_manifest_version: 1`). It declares a typed, validated contract: `name`, `route_prefix`, `health_path`/`docs_url`, `auth: inherit|open|key-auth`, optional Kong timeouts and buffering, and a typed `env` with `default`, `required` and `secret`.

- A plugin without a manifest inherits the Backend application identity boundary.
- A malformed manifest stops only that plugin from loading (inventory status `error`); other plugins stay healthy. Duplicate names, overlapping prefixes and prefixes that shadow a built-in backend route are rejected before mounting.
- Startup and the consumer doctor validate the declared env (required-missing, enum and type warnings). Secrets are masked as `***`. `GET /plugins` (internal-service auth) returns the inventory.
- `auth` applies at Kong and in the application. `inherit` requires Backend identity. `key-auth` checks `BACKEND_KONG_API_KEY` at both layers. Only explicit `open` routes are public.
- A plugin with timeouts gets its own Kong service, so its `connect_timeout`, `write_timeout` and `read_timeout` (milliseconds) do not affect other backend routes. An omitted `read_timeout`/`write_timeout` gets the backend's timeout (at least 3,630,000 ms). An omitted `connect_timeout` keeps Kong's 60,000 ms default.
- A plugin that sets `request_buffering` or `response_buffering` also gets its own service. `false` streams uploads or downloads through Kong. `request_buffering: false` also lifts the backend's 16 MiB default body limit for that prefix; the plugin enforces its own limit.

See [Reusing Atlas §6.3.1](reusing-atlas.md#631-declaring-a-typed-plugin-contract-with-pluginyml).

## 7. Health And Logs

For scripted status, use `./start.sh --no-tui --detach --json` (§2). To collect logs for an issue, write a support bundle (§4.1).

## 8. Managed Host Lifecycle

ComfyUI MPS and vLLM Metal on Apple Silicon, and headless Blender MCP, run as
native host processes outside Docker Compose.

- Atlas starts selected managed hosts only after configuration, dependency,
  route, host and localhost validation pass and the operator confirms launch.
- If an image build, Compose startup or a required init container fails,
  startup rolls back only the host processes this invocation created. A host
  that was running before stays untouched.
- A launch lock in the state directory serializes concurrent launchers, so
  exactly one owns a newly created process.

After the stack converges, the native processes stay part of the running
deployment, and a plain `./stop.sh` leaves them running on purpose. These
runtimes are host-global: another consumer on the same machine may use the same
ComfyUI-MPS, vLLM-Metal or Blender-MCP process. SOURCE cannot prove ownership,
because `.env` is mutable and the state directory is shared. Stopping them is
therefore an explicit opt-in.

`./stop.sh` reports which managed runtimes it left running. Its exit code
depends only on the container teardown. `./stop.sh --stop-managed-hosts` also
stops the three built-in managed runtimes (ComfyUI-MPS, vLLM-Metal and
Blender-MCP) from their state directories. If a native process is still live
after that, the command exits nonzero. `--cold` does not change this.

A consumer-declared managed host process (`managed_host_services` in
`atlas.consumer.yml`) is outside `./stop.sh`'s scope. `--stop-managed-hosts`
does not stop it, and the left-running advisory does not list it. Stop one with
`./start.sh managed-host stop <name>`.
