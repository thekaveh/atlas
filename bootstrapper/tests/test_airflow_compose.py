"""Smoke + structural tests for services/airflow/compose.yml."""
from pathlib import Path
import subprocess
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / "services" / "airflow" / "compose.yml"


def test_airflow_fragment_renders():
    result = subprocess.run(
        [
            "docker", "compose",
            "--env-file", str(REPO_ROOT / ".env.example"),
            "-p", "atlas",
            "-f", str(REPO_ROOT / "docker-compose.yml"),
            "config", "--services",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    services = set(result.stdout.split())
    # 4-container family — Airflow 3.x requires a standalone dag-processor
    # (the scheduler no longer parses DAG files in-process; without
    # dag-processor, no DAGs are ever loaded into the metadata DB).
    for svc in ("airflow-webserver", "airflow-scheduler",
                "airflow-dag-processor", "airflow-init"):
        assert svc in services, f"{svc} missing from merged compose"


def test_airflow_init_uses_bash_passthrough():
    """apache/airflow:3.3.0's ENTRYPOINT is entrypoint_prod.sh, which
    treats the first arg as an `airflow` subcommand and exec's
    `airflow "$@"`. Without the `bash` prefix, our
    `["/scripts/init-airflow.sh"]` becomes `exec airflow
    /scripts/init-airflow.sh` → exit 2 (invalid subcommand) → init
    fails before the script body runs → webserver/scheduler/dag-processor
    never start (all depends_on init: service_completed_successfully).

    The entrypoint explicitly supports `bash` as a passthrough — Pass 38
    caught this; before the fix, every Connection seed, DB create, role
    create, and admin-user create was silently bypassed at cold start.
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    cmd = doc["services"]["airflow-init"]["command"]
    assert cmd[0] in ("bash", "/bin/bash"), (
        f"airflow-init.command must start with 'bash' to escape the "
        f"apache/airflow entrypoint's airflow-subcommand dispatch. "
        f"Got cmd[0]={cmd[0]!r}; would `exec airflow {cmd[0]}` → invalid "
        f"subcommand → init silently no-ops + the whole family fails to start."
    )


def test_airflow_webserver_healthcheck_uses_api_v2_monitor_path():
    """Airflow 3.x's api-server retired `/health`; the canonical health
    endpoint is `/api/v2/monitor/health`. Using `/health` returns 404
    forever and Docker marks the container unhealthy — the TUI then
    shows an orange dot for Airflow even though the UI is fully alive.

    Observed live the morning after PR #35 merged: the user reported
    "I can't find the new Airflow service" because the TUI's "unhealthy"
    badge masked an actually-working Airflow at port 64060. Verified
    against Airflow 3.3.0:
      $ curl localhost:8080/api/v2/monitor/health
      → 200 {"metadatabase":{"status":"healthy"},
             "scheduler":{"status":"healthy",...},
             "dag_processor":{"status":"healthy",...}}
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    test = doc["services"]["airflow-webserver"]["healthcheck"]["test"]
    joined = " ".join(test)
    assert "/api/v2/monitor/health" in joined, (
        f"airflow-webserver healthcheck must probe `/api/v2/monitor/health` "
        f"(Airflow 3.x retired the legacy `/health` route). Got: {joined!r}"
    )
    assert " /health" not in joined and joined != "/health", (
        f"airflow-webserver healthcheck still references the legacy "
        f"`/health` path which returns 404 in Airflow 3.x: {joined!r}"
    )


def test_airflow_dag_processor_uses_correct_command():
    """Airflow 3.x's dag-processor is a separate process; the upgrade
    guide is explicit: `The Dag processor must now be started
    independently, even for local or development setups: airflow dag-processor`.
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    dp = doc["services"]["airflow-dag-processor"]
    assert dp["command"] == ["airflow", "dag-processor"], (
        f"airflow-dag-processor.command must be ['airflow', 'dag-processor'], "
        f"got {dp['command']!r}. Without it the service runs the default "
        f"image entrypoint and no DAGs are parsed."
    )


def test_airflow_scheduler_carries_api_secret_key():
    """airflow-scheduler MUST set AIRFLOW__API__SECRET_KEY.

    Airflow 3.x signs inter-process payloads (DagFileProcessor → scheduler
    RPC, deferrable triggers, JWT in multi-scheduler HA). Both api-server
    and scheduler need the same secret.

    Historical note: pre-Airflow-3 this lived at [webserver] secret_key;
    early drafts of this service used AIRFLOW__WEBSERVER__SECRET_KEY and
    it was a silent no-op (the [webserver] section was retired alongside
    the `airflow webserver` subcommand). The test asserts the canonical
    3.x name to lock against regression.
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    # Pass 16 added airflow-dag-processor as a 3rd long-running JVM —
    # it also signs RPC payloads to the scheduler, so the secret must
    # match across all three.
    for svc_name in ("airflow-webserver", "airflow-scheduler", "airflow-dag-processor"):
        env = doc["services"][svc_name]["environment"]
        assert "AIRFLOW__API__SECRET_KEY" in env, (
            f"{svc_name} is missing AIRFLOW__API__SECRET_KEY in its "
            f"compose env block. Airflow 3.x requires this for inter-process "
            f"signed payloads; missing it breaks deferrable triggers and HA."
        )
        # Must reference the auto-generated env var, not a literal.
        assert env["AIRFLOW__API__SECRET_KEY"] == "${AIRFLOW_SECRET_KEY}", (
            f"{svc_name}.AIRFLOW__API__SECRET_KEY should be "
            f"${{AIRFLOW_SECRET_KEY}}, not {env['AIRFLOW__API__SECRET_KEY']!r}"
        )


def test_airflow_webserver_uses_fab_auth_manager_for_basic_auth():
    """Airflow 3.x defaults to SimpleAuthManager. The Web UI authentication
    surface (FAB session cookie at `/login/`) needs explicit FabAuthManager
    + the FAB-section basic_auth backend; without those, the Web UI logs
    users in via SimpleAuthManager's password-token flow which doesn't
    match the seeded `admin` user.

    History:
    - Pass 9 caught: AIRFLOW__API__AUTH_BACKENDS (legacy 2.x name) was
      silently ignored — Airflow 3.x renamed the section to [fab].
    - Pass 14 caught: the public REST API at `/api/v2/` is JWT-ONLY
      (`/auth/token` exchange). FAB+basic_auth governs LEGACY FAB
      endpoints and Web-UI sessions, NOT `/api/v2/`. So these env
      vars stay in place for the UI; the README curl examples switched
      to the two-step JWT pattern.
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    env = doc["services"]["airflow-webserver"]["environment"]
    assert env.get("AIRFLOW__CORE__AUTH_MANAGER", "").endswith("FabAuthManager"), (
        "airflow-webserver must set AIRFLOW__CORE__AUTH_MANAGER to "
        "FabAuthManager — SimpleAuthManager (the 3.x default) does not "
        "support HTTP basic auth, which the README + Hermes integration use."
    )
    assert "AIRFLOW__FAB__AUTH_BACKENDS" in env, (
        "AIRFLOW__FAB__AUTH_BACKENDS missing — Airflow 3.x moved this from "
        "[api] auth_backends to [fab] auth_backends. The [api] env var is "
        "silently ignored; without the [fab] version, basic_auth is unwired."
    )
    assert "basic_auth" in env["AIRFLOW__FAB__AUTH_BACKENDS"]
    # The deprecated [api] form should NOT exist — its presence would
    # signal someone reverted without realizing it's a no-op in 3.x.
    assert "AIRFLOW__API__AUTH_BACKENDS" not in env, (
        "AIRFLOW__API__AUTH_BACKENDS is a no-op in Airflow 3.x; use "
        "AIRFLOW__FAB__AUTH_BACKENDS instead. Remove the deprecated var."
    )
