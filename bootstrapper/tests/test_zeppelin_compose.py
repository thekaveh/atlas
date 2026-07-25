"""Smoke + structural tests for services/zeppelin/compose.yml.

Mirrors the test_spark_compose.py pattern — exercises through the merged
top-level compose (the fragment can't render standalone because it
references backend-network from the top-level)."""
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / "services" / "zeppelin" / "compose.yml"

@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI unavailable")
def test_zeppelin_fragment_renders():
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
    assert "zeppelin" in services, "zeppelin missing from merged compose"


def test_zeppelin_depends_on_spark_init():
    """Same cold-start race fix as spark-connect: Zeppelin's first `%spark`
    cell writes via spark.eventLog.dir=s3a://spark-history/. Spark 4.x's
    EventLogFileWriters does NOT auto-create the base directory, so the
    bucket (created by spark-init via minio/mc) must exist first.
    Pass 16 added the dependency; this test pins it.
    """
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    deps = doc["services"]["zeppelin"]["depends_on"]
    assert "spark-init" in deps, (
        "zeppelin must depends_on spark-init or the first %spark cell at "
        "cold start fails IllegalArgumentException on s3a://spark-history/."
    )
    assert deps["spark-init"]["condition"] == "service_completed_successfully"
