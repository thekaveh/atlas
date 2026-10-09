# 7.2. Sudo Recovery

This page fixes a launch or stop that ran under `sudo`. `start.sh` and `stop.sh` link here. For every other startup problem, see [Quick Start Troubleshooting](quick-start/troubleshooting.md).

If a problem is not covered, open an issue. Attach a redacted support bundle from `./start.sh doctor --bundle ./atlas-support.tar.gz`. Alternatively, re-run the failing start with `--support-bundle ./atlas-support.tar.gz`. Read its preview first ([Operations §4.1](operations/index.md#41-support-bundle)).

## 1. "Refusing to run as root"

```
start.sh: refusing to run as root.
# or: stop.sh: refusing to run as root.
```

You ran the launcher or the stopper under `sudo`. Atlas runs repository workflows as your user. Only `/etc/hosts` editing needs root. `--setup-hosts` and `--clean-hosts` call a bytecode-free privileged helper for that one atomic write, then return to the unprivileged process.

**Fix:** drop the `sudo`:

```bash
./start.sh              # normal use
./start.sh --setup-hosts # if you want host entries set up up front
./stop.sh --clean-hosts  # stop services and remove Atlas host entries
```

## 2. Recovering from a prior sudo launch or stop

An Atlas version without the root guard could run `start.sh` or `stop.sh` under `sudo`. Root can then own files that the next non-sudo run cannot overwrite. Symptoms:

```
error: Project virtual environment directory `.../bootstrapper/.venv` cannot be used because it is not a valid Python environment (no Python executable was found)
```

or:

```
Failed to write Kong configuration: [Errno 13] Permission denied: '.../volumes/api/kong-dynamic.yml'
```

**Find every root-owned file in the tree:**

```bash
find . -uid 0 -not -path './.git/*'
```

**Take ownership back:**

```bash
sudo chown -R "$(whoami):staff" volumes bootstrapper
```

On Linux, replace `staff` with your primary group (for example `$(id -gn)`).

**Then delete the broken venv** (uv creates it again on the next run):

```bash
rm -rf bootstrapper/.venv
```

**Re-launch normally** (no sudo):

```bash
./start.sh
```

## 3. "Permission denied" writing `kong-dynamic.yml`

The root cause is the same as in §2. Atlas regenerates `volumes/api/kong-dynamic.yml` at every start, so you can safely delete it:

```bash
sudo rm -f volumes/api/kong-dynamic.yml
```

For other unwritable directories under `volumes/`, Atlas keeps all contents and prints a shell-quoted `chown` command. Repair the ownership; do not delete the directory.

## 4. Cold start when things just won't reconcile

**Warning:** `./stop.sh --cold` deletes every Atlas volume of this project: databases, n8n workflows, downloaded models and, with the default `BACKUP_S3_MODE=local`, your backups. Take a backup off the host first. Follow §10 "Recovery Procedures" in [Quick Start Troubleshooting](quick-start/troubleshooting.md) for the full procedure. That page also covers a reset of one volume, such as `n8n-data`.
