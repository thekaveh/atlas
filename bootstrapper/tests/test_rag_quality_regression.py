"""Contracts for the consumer-owned RAG quality regression starter (#1196).

Everything runs offline: the gateway, the Backend evaluator and MLflow are
replaced by recording fakes, and urllib is blocked to prove it.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import urllib.request

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "examples/rag-quality"
CREDENTIALS = ("ATLAS_BACKEND_TOKEN", "ATLAS_BACKEND_HOST_ENDPOINT", "LITELLM_API_KEY",
               "ATLAS_LITELLM_HOST_ENDPOINT", "MLFLOW_TRACKING_URI", "OPENAI_API_KEY")


@pytest.fixture
def regress(monkeypatch):
    spec = importlib.util.spec_from_file_location("regress", SOURCE / "regress.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "regress", module)
    for name in CREDENTIALS:
        monkeypatch.delenv(name, raising=False)

    def no_network(*_args, **_kwargs):
        raise AssertionError("the offline check must not open a connection")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    return module


def _fixtures():
    return json.loads((SOURCE / "fixtures.json").read_text(encoding="utf-8"))


def _check(regress, tmp_path, doc, name="run.json"):
    fixtures = tmp_path / ("fixtures-" + name)
    fixtures.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / name
    code = regress.main(["check", "--fixtures", str(fixtures), "--out", str(out)])
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_template_pins_at_least_ten_complete_fixtures(regress):
    doc = regress.load_fixtures()
    assert len(doc["fixtures"]) >= 10
    for fixture in doc["fixtures"]:
        assert fixture["question"] and fixture["expected_traits"] and fixture["expected_sources"]
        sources = {context["source"] for context in fixture["contexts"]}
        assert set(fixture["expected_sources"]) <= sources, fixture["id"]


def test_unmodified_fixtures_pass_without_any_evaluator_credentials(regress, tmp_path):
    code, record = _check(regress, tmp_path, _fixtures())
    assert code == 0 and record["passed"]
    assert {row["evaluator"] for row in record["results"]} == {"deterministic"}
    assert all(row["evaluator_cost"]["calls"] == 0 for row in record["results"])


@pytest.mark.parametrize("broken", [
    " Cite every source you use as [source-id].",  # the citation instruction
    "{context}",                                     # the sources themselves
])
def test_a_broken_prompt_fails_the_check(regress, tmp_path, broken):
    doc = _fixtures()
    assert broken in doc["prompt"]
    doc["prompt"] = doc["prompt"].replace(broken, "")
    code, record = _check(regress, tmp_path, doc)
    assert code == 1 and not record["passed"]
    assert all(row["failures"] for row in record["results"])


def test_a_removed_citation_source_fails_the_check(regress, tmp_path):
    doc = _fixtures()
    fixture = next(f for f in doc["fixtures"] if len(f["contexts"]) > 1)
    removed = fixture["contexts"].pop()
    code, record = _check(regress, tmp_path, doc)
    row = next(r for r in record["results"] if r["id"] == fixture["id"])
    assert code == 1
    assert f"missing expected source {removed['source']}" in row["failures"]


def test_deterministic_checks_catch_unknown_sources_and_missing_traits(regress):
    fixture = _fixtures()["fixtures"][0]
    failures = regress.check_answer(fixture, "It is 63001. [atlas-ports] [made-up]")
    assert failures == ["cites unknown source made-up", "missing trait '63000'"]
    assert regress.check_answer(fixture, "No sources.")[0] == "no citation"


def test_scored_metrics_go_only_through_the_backend_route():
    text = "\n".join(p.read_text(encoding="utf-8") for p in SOURCE.glob("*.py"))
    assert "/api/rag/evaluate" in text
    assert "import ragas" not in text and "from ragas" not in text


def test_opt_in_scoring_posts_one_record_per_fixture(regress):
    calls = []

    def backend(method, path, body=None):
        calls.append((method, path, body))
        return 200, {"evaluator_model": "judge", "results": [{"scores": {"faithfulness": 0.9}}]}

    doc = regress.load_fixtures()
    scorer = regress.backend_scorer(backend, doc["metrics"], "judge", 0.002)
    record = regress.run(doc, regress.offline_responder, "offline", scorer)
    assert len(calls) == len(doc["fixtures"])
    method, path, body = calls[0]
    assert (method, path, len(body["records"])) == ("POST", "/api/rag/evaluate", 1)
    assert body["metrics"] == doc["metrics"] and body["evaluator_model"] == "judge"
    row = record["results"][0]
    assert row["scores"] == {"faithfulness": 0.9} and row["evaluator"] == "atlas-ragas/judge"
    assert row["evaluator_cost"] == {"calls": 1, "usd": 0.002}


def test_an_evaluator_outage_is_a_failure_row_not_a_crash(regress):
    doc = regress.load_fixtures()
    scorer = regress.backend_scorer(lambda *_: (503, {}), doc["metrics"])
    record = regress.run(doc, regress.offline_responder, "offline", scorer)
    assert not record["passed"]
    assert all(row["failures"] == ["evaluator HTTP 503"] for row in record["results"])


def test_report_compares_latency_cost_scores_and_failures(regress, tmp_path, capsys):
    _check(regress, tmp_path, _fixtures(), "base.json")
    broken = _fixtures()
    broken["prompt"] = broken["prompt"].replace("{context}", "")
    _check(regress, tmp_path, broken, "new.json")
    assert regress.main(["report", str(tmp_path / "base.json"), str(tmp_path / "new.json")]) == 0
    text = capsys.readouterr().out
    assert "| Fixture | Latency ms | Evaluator cost | Scores | Failures |" in text
    assert "Prompt changed: yes" in text and "Failing fixtures: 10 of 10" in text
    line = next(line for line in text.splitlines() if line.startswith("| base-port |"))
    assert "0 calls, $0.0000 -> 0 calls, $0.0000" in line and "REGRESSED: no citation" in line


def test_report_shows_scores_and_unpriced_evaluator_cost(regress):
    base = {"started_at": "a", "responder": "offline", "prompt_sha256": "x", "results": [
        {"id": "f", "latency_ms": 5.0, "failures": [], "scores": {"faithfulness": 0.5},
         "evaluator_cost": {"calls": 1, "usd": None}}]}
    new = copy.deepcopy(base)
    new["results"][0]["scores"] = {"faithfulness": None}
    line = regress.report(base, new).splitlines()[-1]
    assert line == ("| f | 5.0 -> 5.0 | 1 call, cost n/a -> 1 call, cost n/a | "
                    "faithfulness 0.50 -> faithfulness n/a | none |")


def test_mlflow_export_logs_one_run_through_the_rest_api(regress):
    calls = []
    answers = {
        "/api/2.0/mlflow/experiments/create": {"experiment_id": "7"},
        "/api/2.0/mlflow/runs/create": {"run": {"info": {"run_id": "r1"}}},
    }

    def mlflow(method, path, body=None):
        calls.append((method, path.split("?")[0], body))
        return (404, {}) if method == "GET" else (200, answers.get(path, {}))

    record = regress.run(regress.load_fixtures(), regress.offline_responder, "offline")
    assert regress.export_mlflow(mlflow, record) == "r1"
    assert [path.rsplit("/", 1)[-1] for _, path, _ in calls] == [
        "get-by-name", "create", "create", "log-batch", "update"]
    batch = calls[3][2]
    keys = {metric["key"] for metric in batch["metrics"]}
    assert {"fixtures_failed", "latency_ms.base-port", "failures.base-port"} <= keys
    assert calls[4][2]["status"] == "FINISHED"


def test_live_commands_name_the_missing_endpoint(regress, tmp_path):
    with pytest.raises(SystemExit, match="ATLAS_LITELLM_HOST_ENDPOINT"):
        regress.main(["check", "--responder", "gateway", "--model", "m", "--out", str(tmp_path / "x")])
    with pytest.raises(SystemExit, match="ATLAS_BACKEND_HOST_ENDPOINT"):
        regress.main(["check", "--score", "--out", str(tmp_path / "x")])
