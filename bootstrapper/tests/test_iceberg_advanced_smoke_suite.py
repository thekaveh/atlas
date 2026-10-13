from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
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
CONTRACT_DOC = ROOT / "docs" / "operations" / "iceberg-advanced-smoke.md"


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
    assert '${ZEPPELIN_PORT:-63099}' in script
    assert '${ZEPPELIN_PORT:-63082}' not in script
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


def _streaming_completion_source(surface: str) -> str:
    if surface == "script":
        return SCRIPT.read_text().split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    if surface == "jupyter":
        notebook = json.loads(JUPYTER_NOTEBOOK.read_text())
        cells = ["".join(cell.get("source", [])) for cell in notebook["cells"]]
    else:
        notebook = json.loads(ZEPPELIN_NOTEBOOK.read_text())
        cells = [p["text"].split("\n", 1)[-1] for p in notebook["paragraphs"]]
    return next(cell for cell in cells if "awaitTermination" in cell)


def _streaming_completion_tail(source: str) -> ast.Module:
    body = ast.parse(source).body
    creation = next(
        i for i, node in enumerate(body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "query" for target in node.targets)
    )
    # Run the real wait and verification after query creation, never the Spark
    # imports, SQL, table writes, connection setup or later maintenance calls.
    return ast.Module(body=body[creation + 1:creation + 3], type_ignores=[])


class _CompletionQuery:
    def __init__(self, outcome):
        self.outcome = outcome
        self.active = True
        self.stop_calls = 0
        self.waits = []

    def awaitTermination(self, seconds):
        self.waits.append(seconds)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        self.active = not self.outcome
        return self.outcome

    def stop(self):
        self.stop_calls += 1
        self.active = False


@pytest.mark.parametrize("surface", ["jupyter", "zeppelin", "script"])
@pytest.mark.parametrize("outcome", [True, False, RuntimeError("owned query failed")])
def test_streaming_completion_stops_owned_query_and_rejects_timeout(surface, outcome):
    query, verified = _CompletionQuery(outcome), []

    def table(name):
        verified.append(name)
        return SimpleNamespace(show=lambda **kwargs: None, count=lambda: 2)

    namespace = {"query": query, "spark": SimpleNamespace(table=table),
                 "stream_table": "owned-test-table"}
    code = compile(_streaming_completion_tail(_streaming_completion_source(surface)), surface, "exec")
    if outcome is True:
        exec(code, namespace)
        assert len(verified) == 1
    else:
        expected = TimeoutError if outcome is False else RuntimeError
        with pytest.raises(expected):
            exec(code, namespace)
        assert verified == []
    assert query.waits == [120] and query.stop_calls == 1 and not query.active
