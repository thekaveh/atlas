from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION = REPO_ROOT / "docs" / "strategy" / "zeppelin-spark-backend-decision.md"
README = REPO_ROOT / "services" / "zeppelin" / "README.md"
COMPOSE = REPO_ROOT / "services" / "zeppelin" / "compose.yml"
MANIFEST = REPO_ROOT / "services" / "zeppelin" / "service.yml"


def _compact(text: str) -> str:
    return " ".join(text.split())


def test_zeppelin_backend_decision_selects_standalone_spark() -> None:
    text = DECISION.read_text()
    compact = _compact(text)

    assert "Decision issue: [#247]" in text
    assert "Implementation issue unblocked by this decision: [#211]" in text
    assert "spark.master=spark://spark-master:7077" in text
    assert "spark.remote=sc://spark-connect:15002" in text
    assert "Spark Connect remains the right path for JupyterHub" in compact
    assert "Rejected for #211" in text
    assert "Livy would be a new service admission" in text


def test_zeppelin_backend_decision_covers_service_admission_contract() -> None:
    text = DECISION.read_text()
    compact = _compact(text)

    required_fragments = [
        "Track: `data-eng`",
        "Category: existing `zeppelin` service remains `apps`",
        "keep `ZEPPELIN_SOURCE=container|disabled`",
        "no new host ports",
        "unchanged `zeppelin.localhost`",
        "`iceberg-rest` in `depends_on.optional` and `data_flow.calls`",
        "`zeppelin-init` one-shot container",
        "use existing MinIO root/S3A values and scoped Iceberg MinIO credentials",
        "Do not present `spark.remote=sc://spark-connect:15002` as the happy path for Zeppelin",
    ]

    for fragment in required_fragments:
        assert fragment in compact


def test_zeppelin_manifest_currently_stays_existing_service_shape() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text())

    assert manifest["category"] == "apps"
    assert manifest["sources"]["var"] == "ZEPPELIN_SOURCE"
    assert [option["id"] for option in manifest["sources"]["options"]] == [
        "container",
        "disabled",
    ]
    assert manifest["depends_on"]["required"] == ["spark"]
    assert manifest["rows"][0]["alias"] == "zeppelin.localhost"
    assert manifest["runtime_sc"]["zeppelin"]["container"]["environment"][
        "SPARK_MASTER"
    ] == "spark://spark-master:7077"


def test_zeppelin_readme_no_longer_claims_connect_is_happy_path() -> None:
    readme = README.read_text()
    compact = _compact(readme)

    assert "Zeppelin Backend Decision" in readme
    assert "standalone Spark interpreter path" in readme
    assert "JupyterHub remains the Spark Connect notebook path" in readme
    assert "The interpreter config is **not** seeded automatically" not in readme
    assert "The stack should not require `%spark` Scala to use Spark Connect" in compact


def test_zeppelin_compose_comments_follow_backend_decision() -> None:
    compose = COMPOSE.read_text()
    compact = _compact(compose)

    assert "The #247 backend" in compact
    assert "decision keeps Zeppelin on the documented spark-submit" in compact
    assert "#211 must provide SPARK_HOME" in compact
    assert "Do not treat `spark.remote` /" in compact
    assert "Spark Connect as the Zeppelin happy path" in compact
    assert "supported out-of-the-box route is Spark Connect" not in compose
    assert "Users who want to drive Connect from Zeppelin" not in compose
