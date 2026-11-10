"""Contracts for the consumer-side model comparison example (#1197).

The gateway and the Backend evaluator are recording fakes; nothing here
needs a running stack.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "examples/rag-quality"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def compare(monkeypatch):
    monkeypatch.setitem(sys.modules, "regress", _load("regress"))
    return _load("compare")


class Gateway:
    """Answers like LiteLLM; ``failing`` aliases get a provider error."""

    def __init__(self, failing=()):
        self.failing, self.bodies = set(failing), []

    def __call__(self, method, path, body=None):
        assert (method, path) == ("POST", "/v1/chat/completions")
        self.bodies.append(body)
        if body["model"] in self.failing:
            return 500, {"error": {"message": "provider down"}}
        answer = "BASE_PORT defaults to 63000. [atlas-ports]"
        return 200, {"choices": [{"message": {"content": answer}}],
                     "usage": {"prompt_tokens": 40, "completion_tokens": 9}}


def _tasks(compare, count=2):
    doc = compare.regress.load_fixtures()
    return {**doc, "fixtures": doc["fixtures"][:count]}


def _scorer(score):
    def scorer(fixture, answer):
        return {"scores": {"faithfulness": score}, "cost": {"calls": 1, "usd": None},
                "evaluator": "atlas-ragas/judge", "error": None}

    return scorer


def test_candidates_get_identical_payloads_except_the_alias(compare):
    gateway = Gateway()
    record = compare.compare(_tasks(compare), ["small", "large"], gateway, compare.Plan(10))
    small, large = gateway.bodies[:2], gateway.bodies[2:]
    for a, b in zip(small, large):
        assert (a.pop("model"), b.pop("model")) == ("small", "large")
        assert a == b
    assert record["settings"] == compare.regress.SETTINGS
    assert all("settings" not in row for row in record["rows"])


def test_the_request_cap_stops_before_the_next_candidate(compare):
    gateway = Gateway()
    record = compare.compare(_tasks(compare, 1), ["first", "second"], gateway, compare.Plan(1))
    assert [body["model"] for body in gateway.bodies] == ["first"]
    assert record["stopped_at_cap"] and record["requests_used"] == 1
    assert record["candidates"][1]["status"] == "not run"
    out = io.StringIO()
    compare.print_summary({**record, "winner": None}, "LITELLM_DEFAULT_MODEL", out)
    assert "Stopped: the paid-request cap of 1 was reached" in out.getvalue()


def test_evaluator_calls_count_against_the_cap(compare):
    gateway = Gateway()
    plan = compare.Plan(3, _scorer(0.8))
    record = compare.compare(_tasks(compare), ["only"], gateway, plan)
    assert len(gateway.bodies) == 1 and record["requests_used"] == 2 and record["stopped_at_cap"]


def test_every_row_records_latency_tokens_quality_alias_and_evaluator(compare):
    plan = compare.Plan(10, _scorer(0.8))
    record = compare.compare(_tasks(compare), ["small"], Gateway(), plan)
    for row in record["rows"]:
        assert row["model"] == "small" and row["latency_ms"] is not None
        assert row["tokens"] == {"prompt": 40, "completion": 9}
        assert row["quality"] == {"score": 0.8, "status": "scored"}
        assert row["evaluator"] == "atlas-ragas/judge"


def test_provider_errors_are_rows_and_differ_from_a_zero_score(compare):
    plan = compare.Plan(10, _scorer(0.0))
    record = compare.compare(_tasks(compare), ["broken", "weak"], Gateway(["broken"]), plan)
    broken = [row for row in record["rows"] if row["model"] == "broken"]
    assert len(broken) == 2 and all(row["error"] == "gateway HTTP 500" for row in broken)
    by_model = {c["model"]: c for c in record["candidates"]}
    assert by_model["broken"]["status"] == "all errored" and by_model["broken"]["mean_quality"] is None
    assert by_model["weak"]["status"] == "ok" and by_model["weak"]["mean_quality"] == 0.0


def test_no_evaluator_still_records_latency_and_tokens(compare):
    record = compare.compare(_tasks(compare), ["small"], Gateway(), compare.Plan(10))
    for row in record["rows"]:
        assert row["latency_ms"] is not None and row["tokens"]["prompt"] == 40
        assert row["quality"] == {"score": None, "status": "unavailable"}
    assert record["candidates"][0]["mean_quality"] is None


def test_the_winner_line_names_an_existing_env_variable(compare):
    record = compare.compare(_tasks(compare), ["broken", "good"], Gateway(["broken"]), compare.Plan(10))
    winner = compare.pick_winner(record)
    key, _, value = compare.env_line(winner).partition("=")
    assert value == "good"
    declared = {line.split("=", 1)[0] for line in (ROOT / ".env.example").read_text().splitlines() if "=" in line}
    assert key in declared and set(compare.ALIAS_ENV_KEYS) <= declared
    with pytest.raises(ValueError):
        compare.env_line("good", "NOT_A_VARIABLE")


def test_command_writes_the_record_and_prints_the_env_line(compare, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ATLAS_LITELLM_HOST_ENDPOINT", "http://localhost:63040")
    monkeypatch.setattr(compare.regress, "http_client", lambda *_args: Gateway())
    out = tmp_path / "compare.json"
    assert compare.main(["--models", "a", "b", "--max-requests", "40", "--out", str(out)]) == 0
    record = compare.json.loads(out.read_text(encoding="utf-8"))
    assert record["gateway"] == "http://localhost:63040" and record["evaluator"] is None
    assert len(record["rows"]) == 20 and record["winner"] == "a"
    assert "LITELLM_DEFAULT_MODEL=a" in capsys.readouterr().out
