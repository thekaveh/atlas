# 8.2. Troubleshooting

Common startup and shutdown problems and their fixes. If you hit something not covered here, open an issue.

## 1. "Refusing to run as root"

```
start.sh: refusing to run as root.
# or: stop.sh: refusing to run as root.
```

You ran the complete launcher or stopper under `sudo`. Atlas runs repository workflows as your user. Only `/etc/hosts` editing needs root: `--setup-hosts` and `--clean-hosts` invoke a bytecode-free privileged helper for that single atomic write, then return to the unprivileged process.

**Fix:** drop the `sudo`:

```bash
./start.sh              # normal use
./start.sh --setup-hosts # if you want host entries set up up front
./stop.sh --clean-hosts  # stop services and remove Atlas host entries
```

## 2. Recovering from a prior sudo launch or stop

If you ran `start.sh` or `stop.sh` under `sudo` on a version before the guard landed, root may own files the next non-sudo run cannot overwrite. Symptoms:

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

On Linux substitute `staff` with your primary group (e.g. `$(id -gn)`).

**Then nuke the broken venv** (uv will recreate it on the next run):

```bash
rm -rf bootstrapper/.venv
```

**Re-launch normally** (no sudo):

```bash
./start.sh
```

## 3. "Permission denied" writing `kong-dynamic.yml`

Same root cause as above. `volumes/api/kong-dynamic.yml` is regenerated at every startup, so it can also be safely deleted:

```bash
sudo rm -f volumes/api/kong-dynamic.yml
```

For other unwritable directories under `volumes/`, Atlas preserves all contents and reports a shell-quoted `chown` command. Repair ownership instead of deleting the directory.

## 4. Apache Airflow build fails with `ResolutionImpossible`

```
ERROR: Cannot install apache-airflow-providers-amazon>=9.30.0 because these package versions have conflicting dependencies.
The user requested apache-airflow-providers-amazon>=9.30.0
The user requested (constraint) apache-airflow-providers-amazon==9.29.0
```

The pin in `services/airflow/build/requirements.txt` was above the floor the upstream Airflow constraints file allows. The pin has been relaxed to `>=9.29.0` on main; pull the latest:

```bash
git checkout main && git pull
```

Then re-run:

```bash
./start.sh
```

## 5. n8n container restart-loops with `Command start not found`

The n8n data volume is corrupted (usually after an interrupted upgrade). Wipe just that one volume rather than the whole stack:

```bash
docker volume rm ${PROJECT_NAME}-n8n-data
./start.sh
```

## 6. Cold start when things just won't reconcile

When in doubt:

```bash
./stop.sh --cold   # remove all containers + volumes
./start.sh         # rebuild from scratch
```

This is destructive (drops all stack data — including any Supabase DB content, model selections, n8n workflows) so use it as a last resort.
