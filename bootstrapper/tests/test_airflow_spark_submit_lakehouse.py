from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Callable

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
AIRFLOW_DIR = ROOT / "services" / "airflow"
SPARK_DIR = ROOT / "services" / "spark"
DAG = AIRFLOW_DIR / "dags" / "lakehouse_spark_submit_smoke.py"
DOCKERFILE = AIRFLOW_DIR / "build" / "Dockerfile"
INIT_SCRIPT = AIRFLOW_DIR / "init" / "scripts" / "init-airflow.sh"
S3A_SMOKE = ROOT / "scripts" / "smoke_spark_s3a.sh"
S3A_PROBE = ROOT / "scripts" / "spark_s3a_roundtrip.py"
_FAKE_DOCKER_SOURCE = (
    "#!/usr/bin/env bash\n"
    "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
    "mkdir -p \"$FAKE_DOCKER_STATE\"\n"
    "resource_file() { printf '%s/%s-%s' \"$FAKE_DOCKER_STATE\" \"$1\" \"$2\"; }\n"
    "if [[ \"$1 $2\" = 'container inspect' ]]; then\n"
    "  name=\"${@: -1}\"; file=\"$(resource_file container \"$name\")\"\n"
    "  [[ -e \"$file\" ]] || exit 1\n"
    "  if [[ \"$name\" = atlas-s3a-spark-* && -n \"${FAKE_DOCKER_DELAY_PROBE_INSPECTIONS:-}\" ]]; then\n"
    "    counter=\"$(resource_file counter \"$name\")\"; seen=0\n"
    "    [[ ! -e \"$counter\" ]] || read -r seen < \"$counter\"\n"
    "    if ((seen < FAKE_DOCKER_DELAY_PROBE_INSPECTIONS)); then printf '%s\\n' \"$((seen + 1))\" > \"$counter\"; exit 1; fi\n"
    "  fi\n"
    "  if [[ \"${FAKE_DOCKER_MALFORMED_INSPECT:-}\" = 1 ]]; then printf 'malformed\\n'; exit 0; fi\n"
    "  printf '/%s %s\\n' \"$name\" \"$(cat \"$file\")\"; exit 0\n"
    "fi\n"
    "if [[ \"$1 $2\" = 'network inspect' ]]; then\n"
    "  name=\"${@: -1}\"; file=\"$(resource_file network \"$name\")\"\n"
    "  [[ -e \"$file\" ]] || exit 1\n"
    "  if [[ \"${FAKE_DOCKER_MALFORMED_INSPECT:-}\" = 1 ]]; then printf 'malformed\\n'; exit 0; fi\n"
    "  printf '%s %s\\n' \"$name\" \"$(cat \"$file\")\"; exit 0\n"
    "fi\n"
    "if [[ \"$1 $2\" = 'ps -a' || \"$1 $2\" = 'network ls' ]]; then exit 0; fi\n"
    "if [[ \"$1 $2\" = 'network create' ]]; then\n"
    "  name=\"${@: -1}\"; token=\"$ATLAS_S3A_OWNER_TOKEN\"\n"
    "  if [[ \"${FAKE_DOCKER_COLLISION:-}\" = network ]]; then token=foreign; fi\n"
    "  printf '%s' \"$token\" > \"$(resource_file network \"$name\")\"\n"
    "  [[ \"${FAKE_DOCKER_COLLISION:-}\" != network ]] || exit 125\n"
    "  exit 0\n"
    "fi\n"
    "if [[ \"$FAKE_DOCKER_HANG\" = pull && \"$1\" = pull ]]; then sleep 30; fi\n"
    "if [[ \"$1\" = run ]]; then\n"
    "  name=; detached=false; auto_remove=false; args=(\"$@\")\n"
    "  for ((i=0; i<${#args[@]}; i++)); do\n"
    "    [[ \"${args[$i]}\" != --name ]] || name=\"${args[$((i+1))]}\"\n"
    "    [[ \"${args[$i]}\" != --detach ]] || detached=true\n"
    "    [[ \"${args[$i]}\" != --rm ]] || auto_remove=true\n"
    "  done\n"
    "  if [[ -n \"$name\" ]]; then\n"
    "    token=\"$ATLAS_S3A_OWNER_TOKEN\"\n"
    "    if [[ \"${FAKE_DOCKER_COLLISION:-}\" = \"$name\" ]]; then token=foreign; fi\n"
    "    printf '%s' \"$token\" > \"$(resource_file container \"$name\")\"\n"
    "    [[ \"${FAKE_DOCKER_COLLISION:-}\" != \"$name\" ]] || exit 125\n"
    "  fi\n"
    "  if [[ \"$FAKE_DOCKER_HANG\" = readiness && \"$*\" = *minio/mc:* ]]; then sleep 30; fi\n"
    "  if [[ \"$FAKE_DOCKER_HANG\" = *spark* && \"$*\" = *spark-submit* ]]; then sleep 30; fi\n"
    "  if [[ \"$auto_remove\" = true && \"$detached\" = false && -n \"$name\" ]]; then\n"
    "    rm -f \"$(resource_file container \"$name\")\"\n"
    "  fi\n"
    "  exit 0\n"
    "fi\n"
    "if [[ \"$FAKE_DOCKER_HANG\" = *cleanup* && \"$1\" = rm && ! -e \"$FAKE_DOCKER_CLEANUP_MARKER\" ]]; then\n"
    "  : > \"$FAKE_DOCKER_CLEANUP_MARKER\"; sleep 30\n"
    "fi\n"
    "if [[ \"${FAKE_DOCKER_CLEANUP_FAIL:-}\" = 1 && ( \"$1\" = rm || \"$1 $2\" = 'network rm' ) ]]; then exit 42; fi\n"
    "if [[ \"$1\" = rm ]]; then\n"
    "  for name in \"${@:3}\"; do rm -f \"$(resource_file container \"$name\")\"; done; exit 0\n"
    "fi\n"
    "if [[ \"$1 $2\" = 'network rm' ]]; then\n"
    "  name=\"${@: -1}\"; rm -f \"$(resource_file network \"$name\")\"; exit 0\n"
    "fi\n"
    "exit 0\n"
)


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _fake_docker_environment(
    tmp_path: Path, hang_mode: str
) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(_FAKE_DOCKER_SOURCE, encoding="utf-8")
    fake_docker.chmod(0o755)
    log = tmp_path / "docker.log"
    return (
        {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(log),
            "FAKE_DOCKER_HANG": hang_mode,
            "FAKE_DOCKER_CLEANUP_MARKER": str(tmp_path / "cleanup-started"),
            "FAKE_DOCKER_STATE": str(tmp_path / "docker-state"),
            "ATLAS_S3A_SMOKE_TIMEOUT_SECONDS": "3",
            "ATLAS_S3A_COMMAND_TIMEOUT_SECONDS": "1",
            "ATLAS_S3A_PULL_TIMEOUT_SECONDS": "1",
        },
        log,
    )


def _wait_for_process_barrier(
    process: subprocess.Popen[str],
    ready: Callable[[], bool],
    failure: str,
) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready():
            return
        if process.poll() is not None:
            pytest.fail(failure)
        time.sleep(0.05)
    os.killpg(process.pid, signal.SIGKILL)
    process.communicate()
    pytest.fail(failure)


def test_airflow_image_has_real_spark_submit_client_and_lakehouse_jars() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "openjdk-17-jdk-headless" in dockerfile
    assert "JAVA_HOME=/opt/java/openjdk" in dockerfile
    assert "SPARK_HOME=/home/airflow/spark" in dockerfile
    assert "hadoop-aws-${HADOOP_AWS_VERSION}.jar" in dockerfile
    assert "bundle-${AWS_SDK_BUNDLE_VERSION}.jar" in dockerfile
    assert "${ICEBERG_SPARK_RUNTIME_ARTIFACT}-${ICEBERG_VERSION}.jar" in dockerfile
    assert "${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar" in dockerfile
    assert "atlas-lakehouse-smoke.jar" in dockerfile
    assert "COPY --chown=airflow:0 lakehouse-smoke/LakehouseSmoke.java" in dockerfile
    assert "sha512sum -c -" in dockerfile


def test_airflow_compose_exposes_lakehouse_spark_submit_environment() -> None:
    compose = _yaml(AIRFLOW_DIR / "compose.yml")
    expected_env = {
        "SPARK_MASTER_URL": "spark://spark-master:7077",
        "ATLAS_LAKEHOUSE_SPARK_DEPLOY_MODE": "cluster",
        "ATLAS_LAKEHOUSE_SMOKE_JAR_PATH": "/opt/airflow/atlas-jars/atlas-lakehouse-smoke.jar",
        "MINIO_ENDPOINT": "http://minio:9000",
        "MINIO_BUCKET_ICEBERG_JARS": "${MINIO_BUCKET_ICEBERG_JARS:-jars}",
        "MINIO_BUCKET_ICEBERG_LANDING": "${MINIO_BUCKET_ICEBERG_LANDING:-landing}",
        "MINIO_BUCKET_ICEBERG_LAKEHOUSE": "${MINIO_BUCKET_ICEBERG_LAKEHOUSE:-lakehouse}",
        "MINIO_ICEBERG_ACCESS_KEY": "${MINIO_ICEBERG_ACCESS_KEY}",
        "MINIO_ICEBERG_SECRET_KEY": "${MINIO_ICEBERG_SECRET_KEY}",
        "ICEBERG_REST_URI": "http://iceberg-rest:8181",
    }

    for svc_name in ("airflow-webserver", "airflow-scheduler", "airflow-dag-processor"):
        env = compose["services"][svc_name]["environment"]
        for key, value in expected_env.items():
            assert env.get(key) == value, f"{svc_name}.{key}"

    init_env = compose["services"]["airflow-init"]["environment"]
    assert init_env["ICEBERG_REST_SOURCE"] == "${ICEBERG_REST_SOURCE:-disabled}"
    assert init_env["ATLAS_LAKEHOUSE_SPARK_DEPLOY_MODE"] == "cluster"


def test_airflow_init_seeds_spark_default_for_cluster_spark_submit() -> None:
    body = INIT_SCRIPT.read_text(encoding="utf-8")

    assert "for orphan in spark_default minio_default weaviate_default neo4j_default" in body
    assert "--conn-type spark" in body
    assert "--conn-host spark-master" in body
    assert "--conn-port 7077" in body
    assert 'deploy_mode="${ATLAS_LAKEHOUSE_SPARK_DEPLOY_MODE:-cluster}"' in body
    assert '\\"deploy-mode\\": \\"${deploy_mode}\\"' in body
    assert '\\"spark-binary\\": \\"spark-submit\\"' in body


def test_airflow_manifest_declares_lakehouse_spark_submit_topology() -> None:
    manifest = _yaml(AIRFLOW_DIR / "service.yml")
    tracks = _yaml(ROOT / "bootstrapper" / "tracks.yml")["tracks"]
    data_eng = next(track for track in tracks if track["key"] == "data-eng")

    assert manifest["category"] == "agents"
    assert {source["id"] for source in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert "airflow" in data_eng["services"]
    assert {"spark", "minio", "iceberg-rest"} <= set(manifest["depends_on"]["optional"])
    assert {"spark", "minio", "iceberg-rest"} <= set(manifest["data_flow"]["calls"])
    assert "SparkSubmit" in manifest["rows"][0]["description"]


def test_spark_master_keeps_backend_only_rest_submission_api_enabled() -> None:
    compose = _yaml(SPARK_DIR / "compose.yml")
    master = compose["services"]["spark-master"]
    master_env = master["environment"]
    worker = compose["services"]["spark-worker"]
    worker_env = worker["environment"]
    worker_command = "\n".join(worker["command"])

    assert "6066" not in "\n".join(master.get("ports", []))
    assert "SPARK_MASTER_OPTS" in master_env
    assert "spark.master.rest.enabled=true" in master_env["SPARK_MASTER_OPTS"]
    assert "spark.master.rest.host=spark-master" in master_env["SPARK_MASTER_OPTS"]
    assert "spark.master.rest.port=6066" in master_env["SPARK_MASTER_OPTS"]
    assert "spark.standalone.submit.waitAppCompletion=true" in master_env["SPARK_MASTER_OPTS"], (
        "spark-master must set spark.standalone.submit.waitAppCompletion=true (#792 option 3) "
        "so the standalone submit blocks to completion and reports the final driver state — "
        "the SparkSubmitOperator's post-submit poll via the :7077 RPC connection is a benign "
        "false-negative once the submit has reported success."
    )
    assert worker_env["AWS_ACCESS_KEY_ID"] == "${MINIO_ROOT_USER}"
    assert worker_env["AWS_SECRET_ACCESS_KEY"] == "${MINIO_ROOT_PASSWORD}"
    assert worker_env["AWS_ENDPOINT_URL_S3"] == "http://minio:9000"
    assert "HADOOP_CONF_DIR=/tmp/atlas-spark-hadoop-conf" in worker_command
    assert "fs.s3a.path.style.access" in worker_command
    assert "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider" in worker_command


def test_spark_master_rest_status_endpoint_is_documented() -> None:
    spark_readme = (SPARK_DIR / "README.md").read_text(encoding="utf-8")
    airflow_readme = (AIRFLOW_DIR / "README.md").read_text(encoding="utf-8")

    for expected in (
        "spark-master:6066",
        "SparkSubmitOperator",
        "backend-network-only",
        "driver status",
    ):
        assert expected in spark_readme

    assert "spark-master:6066" in airflow_readme
    assert "post-submit driver status" in airflow_readme


def test_lakehouse_spark_submit_smoke_dag_prepares_assets_and_submits_s3a_jar() -> None:
    body = DAG.read_text(encoding="utf-8")

    assert "SparkSubmitOperator" in body
    assert "S3Hook" in body
    assert "schedule=None" in body
    assert "s3a://" in body
    assert "ATLAS_LAKEHOUSE_SMOKE_JAR_PATH" in body
    assert "java_class=LAKEHOUSE_SMOKE_CLASS" in body
    assert "deploy_mode=DEPLOY_MODE" in body
    assert 'os.environ.get("MINIO_ROOT_USER", "")' in body
    assert '"MINIO_ICEBERG_ACCESS_KEY", ""' in body
    for expected in [
        "spark.eventLog.enabled",
        "spark.eventLog.dir",
        "spark.hadoop.fs.s3a.endpoint",
        "spark.driverEnv.AWS_ACCESS_KEY_ID",
        "spark.executorEnv.AWS_ENDPOINT_URL_S3",
        "spark.sql.catalog.lakehouse.uri",
        "spark.sql.catalog.lakehouse.warehouse",
        "spark.sql.catalog.lakehouse.s3.access-key-id",
        "spark.sql.catalog.lakehouse.s3.secret-access-key",
    ]:
        assert expected in body


def test_airflow_docs_describe_s3a_spark_submit_validation_path() -> None:
    readme = (AIRFLOW_DIR / "README.md").read_text(encoding="utf-8")
    source_docs = (ROOT / "docs" / "deployment" / "source-configuration.md").read_text(
        encoding="utf-8"
    )
    combined = f"{readme}\n{source_docs}"

    for expected in [
        "SparkSubmitOperator",
        "s3a://jars/",
        "deploy_mode=\"cluster\"",
        "lakehouse_spark_submit_smoke",
        "Spark History",
        "Iceberg REST",
    ]:
        assert expected in combined


def test_airflow_docs_describe_task_sdk_connection_context_boundary() -> None:
    readme = (AIRFLOW_DIR / "README.md").read_text(encoding="utf-8")
    source_docs = (ROOT / "docs" / "deployment" / "source-configuration.md").read_text(
        encoding="utf-8"
    )

    for docs in (readme, source_docs):
        for expected in (
            "outside a task execution context",
            "AirflowNotFoundException",
            "BaseHook.get_connection",
            'S3Hook(aws_conn_id="minio_default")',
            "airflow.settings.Session",
            "airflow.models.Connection",
            "minio_default",
            "spark_default",
            "DAG tasks should keep using hooks/operators",
        ):
            assert expected in docs


def test_required_ci_runs_a_real_spark_minio_s3a_round_trip() -> None:
    workflow = (ROOT / ".github/workflows/services-lint.yml").read_text(
        encoding="utf-8"
    )
    smoke = S3A_SMOKE.read_text(encoding="utf-8")
    probe = S3A_PROBE.read_text(encoding="utf-8")

    assert S3A_SMOKE.stat().st_mode & 0o111
    assert all(
        fragment in workflow
        for fragment in (
            'scripts/smoke_spark_s3a.sh "$image_tag"',
            '"$context|$dockerfile" = "services/spark/build|Dockerfile"',
        )
    )
    assert all(
        fragment in smoke
        for fragment in (
            "minio/minio:",
            "minio/mc:",
            "spark.hadoop.fs.s3a.endpoint=http://minio:9000",
            "/opt/spark/bin/spark-submit",
            "ATLAS_S3A_SMOKE_TIMEOUT_SECONDS",
            "scripts/bounded_subprocess.py",
            "handshake_ticks=$((handshake_timeout * 10))",
            'cleanup_resource container "$probe" "$probe_uncertain"',
            'cleanup_resource container "$probe" "$probe_uncertain" "$probe_reconcile_timeout"',
            'owner_label="com.atlas.s3a-smoke-token"',
            '--label "${owner_label}=${owner_token}"',
            '--name "$probe"',
        )
    )
    assert "handshake_deadline=$((SECONDS" not in smoke
    assert all(
        fragment in probe
        for fragment in (
            ".write.mode(",
            ".parquet(target)",
            "spark.read.parquet",
            "assert rows ==",
        )
    )


@pytest.mark.parametrize("hang_mode", ["pull", "readiness", "spark"])
def test_s3a_smoke_bounds_docker_hangs_and_cleans_owned_resources(
    tmp_path: Path, hang_mode: str
) -> None:
    env, log = _fake_docker_environment(tmp_path, hang_mode)

    started = time.monotonic()
    try:
        result = subprocess.run(
            [str(S3A_SMOKE), "atlas-test-spark:latest"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{hang_mode} Docker hang escaped the S3A smoke deadline")

    elapsed = time.monotonic() - started
    calls = log.read_text(encoding="utf-8")
    assert result.returncode != 0
    assert elapsed < 8
    if hang_mode == "pull":
        assert "network create" not in calls
    else:
        assert all(
            resource in calls
            for resource in (
                "atlas-s3a-mc-",
                "atlas-s3a-minio-",
                "network inspect --format",
            )
        )
        if hang_mode == "spark":
            assert "atlas-s3a-spark-" in calls
        assert "network rm atlas-s3a-smoke-" in calls


def test_s3a_smoke_never_removes_foreign_network_collision(tmp_path: Path) -> None:
    env, log = _fake_docker_environment(tmp_path, "none")
    env["FAKE_DOCKER_COLLISION"] = "network"

    result = subprocess.run(
        [str(S3A_SMOKE), "atlas-test-spark:latest"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )

    calls = log.read_text(encoding="utf-8")
    assert result.returncode != 0
    assert "network create --label com.atlas.s3a-smoke-token=" in calls
    assert "network rm atlas-s3a-smoke-" not in calls
    foreign = next((tmp_path / "docker-state").glob("network-atlas-s3a-smoke-*"))
    assert foreign.read_text(encoding="utf-8") == "foreign"


def test_s3a_probe_reconciles_beyond_short_command_timeout(tmp_path: Path) -> None:
    env, log = _fake_docker_environment(tmp_path, "spark")
    env.update(
        ATLAS_S3A_SMOKE_TIMEOUT_SECONDS="8",
        ATLAS_S3A_COMMAND_TIMEOUT_SECONDS="1",
        ATLAS_S3A_PULL_TIMEOUT_SECONDS="3",
        FAKE_DOCKER_DELAY_PROBE_INSPECTIONS="3",
    )

    result = subprocess.run(
        [str(S3A_SMOKE), "atlas-test-spark:latest"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )

    assert result.returncode != 0
    assert not list((tmp_path / "docker-state").glob("container-atlas-s3a-spark-*"))
    calls = log.read_text(encoding="utf-8")
    probe_inspections = [
        line
        for line in calls.splitlines()
        if line.startswith("container inspect --format")
        and "atlas-s3a-spark-" in line
    ]
    assert len(probe_inspections) >= 5


def test_s3a_probe_reconciliation_is_clipped_to_remaining_overall_deadline(
    tmp_path: Path,
) -> None:
    env, log = _fake_docker_environment(tmp_path, "spark")
    env.update(
        ATLAS_S3A_SMOKE_TIMEOUT_SECONDS="3",
        ATLAS_S3A_COMMAND_TIMEOUT_SECONDS="8",
        ATLAS_S3A_PULL_TIMEOUT_SECONDS="30",
        FAKE_DOCKER_DELAY_PROBE_INSPECTIONS="999",
    )

    started = time.monotonic()
    result = subprocess.run(
        [str(S3A_SMOKE), "atlas-test-spark:latest"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode != 0
    assert time.monotonic() - started < 20
    probe_inspections = [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith("container inspect --format")
        and "atlas-s3a-spark-" in line
    ]
    assert 1 <= len(probe_inspections) < 10


@pytest.mark.parametrize("failure_mode", ("remove", "malformed-inspect"))
def test_s3a_smoke_success_never_hides_unproven_cleanup(
    tmp_path: Path, failure_mode: str,
) -> None:
    env, _log = _fake_docker_environment(tmp_path, "none")
    env.update(
        ATLAS_S3A_SMOKE_TIMEOUT_SECONDS="15",
        ATLAS_S3A_COMMAND_TIMEOUT_SECONDS="3",
        ATLAS_S3A_PULL_TIMEOUT_SECONDS="3",
    )
    if failure_mode == "remove":
        env["FAKE_DOCKER_CLEANUP_FAIL"] = "1"
    else:
        env["FAKE_DOCKER_MALFORMED_INSPECT"] = "1"

    result = subprocess.run(
        [str(S3A_SMOKE), "atlas-test-spark:latest"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )

    assert result.returncode != 0
    assert "S3A smoke cleanup could not be proven" in result.stderr


@pytest.mark.parametrize(
    "signal_case",
    [
        *[
            pytest.param(
                (signal.SIGHUP, 129, "pull", "pull minio/minio:"),
                id=f"hup-pull-{attempt}",
            )
            for attempt in range(5)
        ],
        (signal.SIGINT, 130, "spark", "spark-submit"),
        (signal.SIGTERM, 143, "spark", "spark-submit"),
    ],
)
def test_s3a_smoke_forwards_signals_then_cleans_owned_resources(
    tmp_path: Path,
    signal_case: tuple[signal.Signals, int, str, str],
) -> None:
    interruption, expected_returncode, hang_mode, active_fragment = signal_case
    env, log = _fake_docker_environment(tmp_path, hang_mode)
    env.update(
        ATLAS_S3A_SMOKE_TIMEOUT_SECONDS="30",
        ATLAS_S3A_COMMAND_TIMEOUT_SECONDS="30",
        ATLAS_S3A_PULL_TIMEOUT_SECONDS="30",
    )
    process = subprocess.Popen(
        [str(S3A_SMOKE), "atlas-test-spark:latest"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _wait_for_process_barrier(
        process,
        lambda: log.exists()
        and active_fragment in log.read_text(encoding="utf-8"),
        "S3A smoke never reached its bounded Docker command",
    )

    process.send_signal(interruption)
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        pytest.fail(f"S3A smoke did not forward {interruption.name}")

    calls = log.read_text(encoding="utf-8")
    assert process.returncode == expected_returncode
    if hang_mode == "pull":
        assert "network create" not in calls
    else:
        assert all(
            resource in calls
            for resource in (
                "atlas-s3a-spark-",
                "atlas-s3a-mc-",
                "atlas-s3a-minio-",
                "network inspect --format",
            )
        )
        assert "network rm atlas-s3a-smoke-" in calls


@pytest.mark.parametrize(
    "transition_case",
    [
        ("command-launch-window", "spark", "run Spark S3A round trip"),
        ("command-exit-window", "none", "run Spark S3A round trip"),
        ("cleanup-entry", "none", ""),
    ],
)
def test_s3a_smoke_handles_signals_at_child_ownership_transitions(
    tmp_path: Path, transition_case: tuple[str, str, str]
) -> None:
    signal_phase, hang_mode, signal_label = transition_case
    env, log = _fake_docker_environment(tmp_path, hang_mode)
    event_log = tmp_path / "events.log"
    env.update(
        ATLAS_S3A_TEST_SIGNAL_PHASE=signal_phase,
        ATLAS_S3A_TEST_SIGNAL_NAME="TERM",
        ATLAS_S3A_TEST_SIGNAL_LABEL=signal_label,
        ATLAS_S3A_TEST_EVENT_LOG=str(event_log),
        ATLAS_S3A_SMOKE_TIMEOUT_SECONDS="30",
        ATLAS_S3A_COMMAND_TIMEOUT_SECONDS="30",
        ATLAS_S3A_PULL_TIMEOUT_SECONDS="30",
    )

    result = subprocess.run(
        [str(S3A_SMOKE), "atlas-test-spark:latest"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    calls = log.read_text(encoding="utf-8")
    assert result.returncode == 143
    assert "network rm atlas-s3a-smoke-" in calls
    if signal_phase.startswith("command-"):
        assert not event_log.exists()


def test_s3a_smoke_reaps_child_when_interruption_is_repeated(tmp_path: Path) -> None:
    env, log = _fake_docker_environment(tmp_path, "spark,cleanup")
    env.update(
        ATLAS_S3A_SMOKE_TIMEOUT_SECONDS="30",
        ATLAS_S3A_COMMAND_TIMEOUT_SECONDS="30",
        ATLAS_S3A_PULL_TIMEOUT_SECONDS="30",
    )
    process = subprocess.Popen(
        [str(S3A_SMOKE), "atlas-test-spark:latest"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _wait_for_process_barrier(
        process,
        lambda: log.exists()
        and "spark-submit" in log.read_text(encoding="utf-8"),
        "S3A smoke never reached its Spark probe",
    )

    process.send_signal(signal.SIGTERM)
    cleanup_marker = Path(env["FAKE_DOCKER_CLEANUP_MARKER"])
    _wait_for_process_barrier(
        process,
        cleanup_marker.exists,
        "S3A smoke never reached the controlled cleanup barrier",
    )

    assert process.poll() is None
    process.send_signal(signal.SIGINT)
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        pytest.fail("S3A smoke did not reap its child after repeated signals")

    assert process.returncode == 143
    assert "network rm atlas-s3a-smoke-" in log.read_text(encoding="utf-8")


def test_s3a_smoke_preserves_signal_status_and_retries_interrupted_cleanup(
    tmp_path: Path,
) -> None:
    env, log = _fake_docker_environment(tmp_path, "cleanup")
    env.update(
        ATLAS_S3A_SMOKE_TIMEOUT_SECONDS="30",
        ATLAS_S3A_COMMAND_TIMEOUT_SECONDS="30",
        ATLAS_S3A_PULL_TIMEOUT_SECONDS="30",
    )
    process = subprocess.Popen(
        [str(S3A_SMOKE), "atlas-test-spark:latest"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _wait_for_process_barrier(
        process,
        lambda: log.exists()
        and "rm -f atlas-s3a-minio-" in log.read_text(encoding="utf-8"),
        "S3A smoke never began cleanup",
    )

    process.send_signal(signal.SIGTERM)
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        pytest.fail("S3A smoke did not finish cleanup after SIGTERM")

    calls = log.read_text(encoding="utf-8")
    assert process.returncode == 143
    assert calls.count("rm -f atlas-s3a-minio-") >= 2
    assert "network rm atlas-s3a-smoke-" in calls


@pytest.mark.parametrize("runner_mode", ["term-resistant", "stopped"])
def test_s3a_smoke_kills_runner_that_never_publishes_readiness(
    tmp_path: Path, runner_mode: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    runner_tmp = tmp_path / "runner-tmp"
    runner_tmp.mkdir()
    pid_log = tmp_path / "runner-pids.log"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "trap '' HUP INT TERM\n"
        "ready_dirs=( \"$TMPDIR\"/atlas-s3a-ready.* )\n"
        ": > \"${ready_dirs[0]}/.forced.pending\"\n"
        "printf '%s\\n' \"$$\" >> \"$FAKE_RUNNER_PID_LOG\"\n"
        "if [[ \"$FAKE_RUNNER_MODE\" = stopped ]]; then kill -STOP \"$$\"; fi\n"
        "exec tail -f /dev/null\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_RUNNER_PID_LOG": str(pid_log),
        "FAKE_RUNNER_MODE": runner_mode,
        "TMPDIR": str(runner_tmp),
        "ATLAS_S3A_SMOKE_TIMEOUT_SECONDS": "2",
        "ATLAS_S3A_COMMAND_TIMEOUT_SECONDS": "1",
        "ATLAS_S3A_PULL_TIMEOUT_SECONDS": "1",
    }

    started = time.monotonic()
    result = subprocess.run(
        [str(S3A_SMOKE), "atlas-test-spark:latest"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=7,
        check=False,
    )

    assert result.returncode != 0
    assert time.monotonic() - started < 7
    for pid in map(int, pid_log.read_text(encoding="utf-8").splitlines()):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert not list(runner_tmp.glob("atlas-s3a-ready.*"))
