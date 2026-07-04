from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "smoke-iceberg-advanced-sql.sh"
JUPYTER_NOTEBOOK = (
    ROOT / "services" / "jupyterhub" / "build" / "notebooks" / "12_iceberg_advanced_sql.ipynb"
)
ZEPPELIN_NOTEBOOK = ROOT / "services" / "zeppelin" / "notebooks" / "iceberg_advanced_sql.zpln"
SPARK_README = ROOT / "services" / "spark" / "README.md"
ZEPPELIN_README = ROOT / "services" / "zeppelin" / "README.md"
JUPYTER_README = ROOT / "services" / "jupyterhub" / "README.md"
CONTRACT_DOC = ROOT / "docs" / "deployment" / "iceberg-advanced-smoke.md"


ADVANCED_TOKENS = [
    "MERGE INTO",
    "VERSION AS OF",
    "rollback_to_snapshot",
    "rewrite_data_files",
    "expire_snapshots",
    "remove_orphan_files",
    "CREATE BRANCH",
    "spark.wap.branch",
    "ADD COLUMN",
    "readStream",
    "writeStream.format(\"iceberg\")",
    "checkpointLocation",
    "s3a://landing/",
    "s3a://checkpoints/",
]


def _notebook_text(path: Path) -> str:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    assert notebook["nbformat_minor"] >= 5
    assert all("id" in cell for cell in notebook["cells"])
    return "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
        if cell.get("cell_type") in {"markdown", "code"}
    )


def test_opt_in_smoke_script_covers_spark_connect_and_zeppelin_surfaces() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in script
    assert "SPARK_SOURCE=container" in script
    assert "ICEBERG_REST_SOURCE=container" in script
    assert "MINIO_SOURCE=container" in script
    assert "spark-connect" in script
    assert "zeppelin" in script
    assert '${PROJECT_NAME:-atlas}' in script
    assert '${project}-jupyterhub' in script
    assert '${project}-zeppelin' in script
    assert "run_spark_connect" in script
    assert "run_zeppelin" in script
    assert "all" in script

    for token in ADVANCED_TOKENS:
        assert token in script


def test_spark_connect_notebook_exercises_advanced_iceberg_capabilities() -> None:
    text = _notebook_text(JUPYTER_NOTEBOOK)

    assert "Spark Connect" in text
    assert "SparkSession.builder.remote" in text
    assert "sc://spark-connect:15002" in text
    assert "atlas_smoke" in text
    assert "bronze" not in text
    assert "silver" not in text
    assert "gold" not in text

    for token in ADVANCED_TOKENS:
        assert token in text


def test_zeppelin_notebook_exercises_same_contract_without_spark_connect() -> None:
    notebook = json.loads(ZEPPELIN_NOTEBOOK.read_text(encoding="utf-8"))
    text = "\n".join(paragraph.get("text", "") for paragraph in notebook["paragraphs"])

    assert "%spark.sql" in text
    assert "spark://spark-master:7077" in text
    assert "Spark Connect" not in text
    assert "spark.remote" not in text
    assert "atlas_smoke" in text

    for token in ADVANCED_TOKENS:
        assert token in text


def test_docs_describe_no_new_service_contract_and_opt_in_validation() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [SPARK_README, ZEPPELIN_README, JUPYTER_README, CONTRACT_DOC]
    )

    for expected in [
        "scripts/smoke-iceberg-advanced-sql.sh",
        "SPARK_SOURCE=container",
        "ICEBERG_REST_SOURCE=container",
        "MINIO_SOURCE=container",
        "No new service",
        "no new SOURCE",
        "no new port",
        "data-eng",
        "all",
        "Spark Connect",
        "Zeppelin",
        "MERGE INTO",
        "Structured Streaming",
        "s3a://landing/",
        "s3a://checkpoints/",
    ]:
        assert expected in combined


def test_existing_service_topology_remains_unchanged_for_smoke_suite() -> None:
    spark_manifest = yaml.safe_load((ROOT / "services" / "spark" / "service.yml").read_text())
    zeppelin_manifest = yaml.safe_load((ROOT / "services" / "zeppelin" / "service.yml").read_text())
    jupyter_manifest = yaml.safe_load((ROOT / "services" / "jupyterhub" / "service.yml").read_text())

    assert {source["id"] for source in spark_manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert {source["id"] for source in zeppelin_manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert {source["id"] for source in jupyter_manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert spark_manifest["category"] == "data"
    assert zeppelin_manifest["category"] == "apps"
    assert jupyter_manifest["category"] == "apps"
    assert "iceberg-rest" in spark_manifest["depends_on"]["optional"]
    assert "spark" in zeppelin_manifest["depends_on"]["required"]
    assert "spark" in jupyter_manifest["depends_on"]["optional"]
