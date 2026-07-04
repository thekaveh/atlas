"""Smoke + structural tests for services/spark/compose.yml.

The fragment cross-references `backend-network` (defined in top-level
docker-compose.yml) and `depends_on: minio-init` (from services/minio/),
so it cannot render standalone. We exercise it through the merged
top-level compose, which is also how it ships at runtime.
"""
from pathlib import Path
import subprocess
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / "services" / "spark" / "compose.yml"

def test_spark_fragment_renders():
    result = subprocess.run(
        [
            "docker", "compose",
            "--env-file", str(REPO_ROOT / ".env.example"),
            "-p", "atlas",
            "-f", str(REPO_ROOT / "docker-compose.yml"),
            "config", "-q",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"compose render failed:\n{result.stderr}"
    # Confirm spark services are present in the merged shape (5 containers
    # since Pass 2 added the dedicated spark-connect sidecar — earlier
    # attempts at in-master Spark Connect didn't actually bind the listener).
    services_result = subprocess.run(
        [
            "docker", "compose",
            "--env-file", str(REPO_ROOT / ".env.example"),
            "-p", "atlas",
            "-f", str(REPO_ROOT / "docker-compose.yml"),
            "config", "--services",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert services_result.returncode == 0, services_result.stderr
    services = set(services_result.stdout.split())
    for svc in (
        "spark-master", "spark-worker", "spark-history",
        "spark-connect", "spark-init",
    ):
        assert svc in services, f"{svc} missing from merged compose"


def test_spark_connect_sidecar_uses_start_connect_server():
    """spark-connect must run apache/spark's start-connect-server.sh — the
    only upstream-supported way to bind the Connect gRPC listener on 15002.

    Locks against a future "let's run it in-master" refactor (Pass 2 audit
    P2-2 verified that SPARK_DAEMON_JAVA_OPTS alone does NOT start the
    listener; standalone Master ignores --conf and spark.plugins).
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert "spark-connect" in doc["services"], "spark-connect sidecar missing"
    cmd = doc["services"]["spark-connect"]["command"]
    assert cmd[0] == "/opt/spark/sbin/start-connect-server.sh", (
        f"spark-connect command should start with start-connect-server.sh, "
        f"got {cmd[0]!r}"
    )
    # Must target the standalone master.
    assert "--master" in cmd and "spark://spark-master:7077" in cmd
    # Must NOT publish a host port (backend-only by design).
    assert "ports" not in doc["services"]["spark-connect"], (
        "spark-connect should be backend-only — no host port mapping."
    )


def test_spark_connect_caps_standalone_cluster_cores():
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    connect_cmd = doc["services"]["spark-connect"]["command"]
    joined_connect_cmd = " ".join(connect_cmd)

    assert "spark.cores.max=${SPARK_CONNECT_CORES_MAX:-1}" in joined_connect_cmd

    master_cmd = " ".join(doc["services"]["spark-master"]["command"])
    worker_cmd = " ".join(doc["services"]["spark-worker"]["command"])
    assert "spark.cores.max" not in master_cmd
    assert "spark.cores.max" not in worker_cmd


def test_spark_init_uses_minio_mc_image():
    """spark-init must use the minio/mc image — Alpine's `apk add mc`
    installs GNU Midnight Commander (TUI file manager), NOT MinIO Client.
    Pass 2 audit P2-1 verified this empirically; this test locks the fix.
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    image = doc["services"]["spark-init"]["image"]
    assert "minio/mc" in image, (
        f"spark-init image should be minio/mc:..., got {image!r}. "
        "alpine:latest's mc package is Midnight Commander, not MinIO Client."
    )


def test_spark_connect_emits_event_log_to_history_bucket():
    """spark-connect must set spark.eventLog.enabled=true +
    spark.eventLog.dir=s3a://spark-history/ so that Connect-driven
    Spark sessions actually feed the History Server. Without these,
    the Pass 15 fix wouldn't deliver; the documented spark-history
    feature ships non-functional.
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    cmd = doc["services"]["spark-connect"]["command"]
    joined = " ".join(cmd)
    assert "spark.eventLog.enabled=true" in joined, (
        "spark-connect must enable eventLog or the History Server stays "
        "empty forever. Pass 15 fix."
    )
    assert "spark.eventLog.dir=s3a://spark-history/" in joined, (
        "spark-connect must point eventLog at s3a://spark-history/."
    )


def test_spark_connect_runs_in_foreground_via_wait_flag():
    """spark-connect must invoke start-connect-server.sh with `--wait`
    (or SPARK_NO_DAEMONIZE=1 in env) so the underlying spark-daemon.sh
    submit branch runs in the foreground.

    Without this, the script delegates to `nohup ... &` and exits
    immediately — Docker treats command-rc=0 as success, the container
    stops, `restart: unless-stopped` puts it in a perpetual restart
    loop, and sc://spark-connect:15002 NEVER serves. Pass 37 caught
    this; before the fix, Airflow's spark_smoke + Zeppelin's
    `spark.remote` config would fail with connection refused on every
    cold start.

    Verified against upstream apache/spark v4.1.2:
    - sbin/start-connect-server.sh: shifts --wait and sets
      SPARK_NO_DAEMONIZE=1
    - sbin/spark-daemon.sh::execute_command: runs `nohup ... &` unless
      SPARK_NO_DAEMONIZE is set, in which case foregrounds with `"$@"`
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    cmd = doc["services"]["spark-connect"]["command"]
    env = doc["services"]["spark-connect"].get("environment", {})
    has_wait = "--wait" in cmd
    has_no_daemonize = env.get("SPARK_NO_DAEMONIZE") in ("1", "true", "TRUE")
    assert has_wait or has_no_daemonize, (
        "spark-connect must run in foreground via --wait or "
        "SPARK_NO_DAEMONIZE=1. Without one, start-connect-server.sh "
        "exits immediately and the container restart-loops; the "
        "gRPC listener never serves."
    )


def test_spark_connect_depends_on_spark_init():
    """spark-connect must wait for spark-init (which creates the
    spark-history MinIO bucket) before starting — Spark 4.x's
    EventLogFileWriters does NOT auto-create the base eventLog dir;
    a cold-start race would throw IllegalArgumentException at the
    first Spark Connect session.
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    deps = doc["services"]["spark-connect"]["depends_on"]
    assert "spark-init" in deps, (
        "spark-connect must depends_on spark-init or the first job at "
        "cold start fails 'Log directory s3a://spark-history/ is not a "
        "directory'."
    )
    assert deps["spark-init"]["condition"] == "service_completed_successfully"


def test_spark_master_rest_status_contract_comments_are_current():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "it's off by default" not in text
    assert "standalone-master REST API" in text
    assert "Airflow" in text


def test_spark_connect_core_cap_is_documented():
    readme = (REPO_ROOT / "services" / "spark" / "README.md").read_text(
        encoding="utf-8"
    )
    for expected in (
        "SPARK_CONNECT_CORES_MAX=1",
        "spark.cores.max",
        "standalone workloads",
        "Spark Connect parallelism",
    ):
        assert expected in readme
