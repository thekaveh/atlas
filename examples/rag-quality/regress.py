"""Consumer-owned RAG quality regression check.

Run it as described in docs/operations/reusing-atlas.md (section 6.8).
It uses only the standard library. Scored metrics come from Atlas's
POST /api/rag/evaluate, so the template carries no evaluator of its own.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures.json"
EVALUATE_PATH = "/api/rag/evaluate"
CHAT_PATH = "/v1/chat/completions"
# Identical for every model and every run; the run record states them once.
SETTINGS = {"temperature": 0, "max_tokens": 512}
CITATION = re.compile(r"\[([A-Za-z0-9_.:-]+)\]")
SOURCE_LINE = re.compile(r"^\[([A-Za-z0-9_.:-]+)\] (.+)$", re.M)
FIXTURE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ResponderError(RuntimeError):
    pass


def http_client(base_url, token=None, timeout=120):
    """``call(method, path, body) -> (status | None, payload)``; None when
    the service cannot be reached."""

    def call(method, path, body=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(base_url.rstrip("/") + path, data, headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, _json(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _json(exc.read())
        except OSError:
            return None, {}

    return call


def _json(raw):
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {}


def load_fixtures(path=FIXTURES):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [fixture["id"] for fixture in doc["fixtures"]]
    if len(set(ids)) != len(ids) or not all(FIXTURE_ID.match(i) for i in ids):
        raise ValueError("fixture ids must be unique lowercase slugs")
    for fixture in doc["fixtures"]:
        for key in ("question", "contexts", "expected_traits", "expected_sources"):
            if key not in fixture:
                raise ValueError(f"fixture {fixture['id']} has no {key}")
    return doc


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def render(prompt, fixture):
    """The exact prompt text a model receives for one fixture."""
    context = "\n".join(f"[{c['source']}] {c['text']}" for c in fixture["contexts"])
    return prompt.replace("{context}", context).replace("{question}", fixture["question"])


def chat_payload(model, prompt):
    return {"model": model, "messages": [{"role": "user", "content": prompt}], **SETTINGS}


def offline_responder(prompt):
    """A deterministic stand-in for a model, for the sample run and CI.

    It answers with the source lines the prompt carries. It cites them only
    when the prompt asks for citations, so a broken prompt fails the check."""
    sources = SOURCE_LINE.findall(prompt)
    cite = re.search(r"\bcite\b", prompt, re.I) is not None
    if not sources:
        return "The sources do not answer this question.", None
    return " ".join(f"{text} [{source}]" if cite else text for source, text in sources), None


def gateway_responder(call, model):
    """Send the prompt to a LiteLLM alias; returns ``(answer, tokens)``."""

    def respond(prompt):
        status, payload = call("POST", CHAT_PATH, chat_payload(model, prompt))
        if status != 200:
            raise ResponderError("gateway unreachable" if status is None else f"gateway HTTP {status}")
        try:
            answer = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ResponderError("gateway returned no answer") from exc
        usage = payload.get("usage") or {}
        return answer, {"prompt": usage.get("prompt_tokens"), "completion": usage.get("completion_tokens")}

    return respond


def backend_scorer(call, metrics, evaluator_model=None, usd_per_call=None):
    """Score one answer through POST /api/rag/evaluate (opt-in, may be paid)."""

    def score(fixture, answer):
        record = {
            "question": fixture["question"],
            "answer": answer,
            "contexts": [c["text"] for c in fixture["contexts"]],
            "ground_truth": fixture.get("ground_truth"),
        }
        body = {"records": [record], "metrics": list(metrics)}
        if evaluator_model:
            body["evaluator_model"] = evaluator_model
        status, payload = call("POST", EVALUATE_PATH, body)
        outcome = {"scores": None, "cost": {"calls": 1, "usd": usd_per_call},
                   "evaluator": f"atlas-ragas/{evaluator_model or 'default'}", "error": None}
        if status != 200:
            outcome["error"] = "evaluator unreachable" if status is None else f"evaluator HTTP {status}"
            return outcome
        outcome["scores"] = payload["results"][0]["scores"]
        outcome["evaluator"] = f"atlas-ragas/{payload.get('evaluator_model') or 'default'}"
        return outcome

    return score


def check_answer(fixture, answer):
    """Deterministic checks: citation presence, expected sources, traits."""
    cited = set(CITATION.findall(answer))
    known = {c["source"] for c in fixture["contexts"]}
    failures = [] if cited else ["no citation"]
    failures += [f"missing expected source {s}" for s in fixture["expected_sources"] if s not in cited]
    failures += [f"cites unknown source {s}" for s in sorted(cited - known)]
    lowered = answer.lower()
    failures += [f"missing trait {t!r}" for t in fixture["expected_traits"] if t.lower() not in lowered]
    return failures


def run_fixture(fixture, prompt, respond, scorer=None):
    """One result row; a responder failure becomes a row, never an exception."""
    row = {"id": fixture["id"], "latency_ms": None, "tokens": None, "answer": None,
           "failures": [], "scores": None, "evaluator": "deterministic",
           "evaluator_cost": {"calls": 0, "usd": 0.0}, "error": None}
    started = time.perf_counter()
    try:
        answer, row["tokens"] = respond(prompt)
    except ResponderError as exc:
        row["error"] = str(exc)
    row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    if row["error"]:
        row["failures"] = [row["error"]]
        return row
    row["answer"], row["failures"] = answer, check_answer(fixture, answer)
    if scorer is not None:
        outcome = scorer(fixture, answer)
        row.update(scores=outcome["scores"], evaluator=outcome["evaluator"], evaluator_cost=outcome["cost"])
        if outcome["error"]:
            row["failures"].append(outcome["error"])
    return row


def run(doc, respond, responder_name, scorer=None):
    results = [run_fixture(f, render(doc["prompt"], f), respond, scorer) for f in doc["fixtures"]]
    return {
        "schema": "atlas-rag-quality-run/1",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "responder": responder_name,
        "settings": SETTINGS,
        "prompt_sha256": digest(doc["prompt"]),
        "fixtures_sha256": digest(doc["fixtures"]),
        "results": results,
        "passed": not any(row["failures"] for row in results),
    }


def _cost(row):
    cost = row["evaluator_cost"]
    calls = f"{cost['calls']} call" + ("" if cost["calls"] == 1 else "s")
    if cost["calls"] and cost["usd"] is None:
        return f"{calls}, cost n/a"
    return f"{calls}, ${cost['usd'] or 0:.4f}"


def _scores(row):
    if not row or not row["scores"]:
        return "-"
    return ", ".join(f"{k} {'n/a' if v is None else f'{v:.2f}'}" for k, v in row["scores"].items())


def _report_line(fixture_id, old, new):
    def cell(row, render_cell):
        return render_cell(row) if row else "-"

    latency = f"{cell(old, lambda r: r['latency_ms'])} -> {cell(new, lambda r: r['latency_ms'])}"
    cost = f"{cell(old, _cost)} -> {cell(new, _cost)}"
    scores = f"{_scores(old)} -> {_scores(new)}"
    failures = "; ".join(new["failures"]) if new and new["failures"] else "none"
    if new and new["failures"] and old and not old["failures"]:
        failures = "REGRESSED: " + failures
    return f"| {fixture_id} | {latency} | {cost} | {scores} | {failures} |"


def report(baseline, current):
    """Markdown comparison of two run records, one row per fixture."""
    old = {row["id"]: row for row in baseline["results"]}
    new = {row["id"]: row for row in current["results"]}
    ids = list(new) + [i for i in old if i not in new]
    failed = sum(1 for row in current["results"] if row["failures"])
    lines = [
        "# RAG quality report",
        "",
        f"- Baseline: {baseline['started_at']} ({baseline['responder']})",
        f"- Current: {current['started_at']} ({current['responder']})",
        f"- Prompt changed: {'yes' if baseline['prompt_sha256'] != current['prompt_sha256'] else 'no'}",
        f"- Failing fixtures: {failed} of {len(current['results'])}",
        "",
        "| Fixture | Latency ms | Evaluator cost | Scores | Failures |",
        "|---|---|---|---|---|",
    ]
    lines += [_report_line(i, old.get(i), new.get(i)) for i in ids]
    return "\n".join(lines) + "\n"


def export_mlflow(call, record, experiment="atlas-rag-quality"):
    """Log one run record to MLflow's REST API; returns the MLflow run id."""
    query = urllib.parse.urlencode({"experiment_name": experiment})
    status, payload = call("GET", f"/api/2.0/mlflow/experiments/get-by-name?{query}")
    if status == 200:
        experiment_id = payload["experiment"]["experiment_id"]
    else:
        status, payload = call("POST", "/api/2.0/mlflow/experiments/create", {"name": experiment})
        if status != 200:
            raise RuntimeError(f"MLflow experiment create failed: HTTP {status}")
        experiment_id = payload["experiment_id"]
    now = int(time.time() * 1000)
    status, payload = call("POST", "/api/2.0/mlflow/runs/create",
                           {"experiment_id": experiment_id, "start_time": now, "run_name": record["started_at"]})
    if status != 200:
        raise RuntimeError(f"MLflow run create failed: HTTP {status}")
    run_id = payload["run"]["info"]["run_id"]
    status, _ = call("POST", "/api/2.0/mlflow/runs/log-batch", {"run_id": run_id, **_mlflow_batch(record, now)})
    if status != 200:
        raise RuntimeError(f"MLflow log-batch failed: HTTP {status}")
    call("POST", "/api/2.0/mlflow/runs/update",
         {"run_id": run_id, "status": "FINISHED" if record["passed"] else "FAILED", "end_time": now})
    return run_id


def _mlflow_batch(record, now):
    metrics = [("fixtures_failed", sum(1 for r in record["results"] if r["failures"]))]
    for row in record["results"]:
        metrics += [(f"latency_ms.{row['id']}", row["latency_ms"]),
                    (f"failures.{row['id']}", len(row["failures"])),
                    (f"evaluator_calls.{row['id']}", row["evaluator_cost"]["calls"])]
        metrics += [(f"score.{k}.{row['id']}", v) for k, v in (row["scores"] or {}).items() if v is not None]
    params = {"responder": record["responder"], "prompt_sha256": record["prompt_sha256"],
              "fixtures_sha256": record["fixtures_sha256"], "settings": json.dumps(record["settings"])}
    return {
        "metrics": [{"key": k, "value": v, "timestamp": now, "step": 0} for k, v in metrics if v is not None],
        "params": [{"key": k, "value": v} for k, v in params.items()],
    }


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set; see docs/operations/reusing-atlas.md section 6.8")
    return value


def _responder(args):
    if args.responder == "offline":
        return offline_responder, "offline"
    if not args.model:
        raise SystemExit("--responder gateway needs --model <alias>")
    gateway = http_client(require_env("ATLAS_LITELLM_HOST_ENDPOINT"), os.environ.get("LITELLM_API_KEY"))
    return gateway_responder(gateway, args.model), f"gateway:{args.model}"


def scorer_from_args(args, doc):
    if not args.score:
        return None
    backend = http_client(require_env("ATLAS_BACKEND_HOST_ENDPOINT"), require_env("ATLAS_BACKEND_TOKEN"))
    return backend_scorer(backend, doc.get("metrics", ["faithfulness"]), args.evaluator_model, args.usd_per_call)


def write_output(path, text):
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="run the fixtures and fail on any deterministic failure")
    check.add_argument("--fixtures", default=str(FIXTURES))
    check.add_argument("--out", help="write the run record here")
    check.add_argument("--responder", choices=("offline", "gateway"), default="offline")
    check.add_argument("--model", help="LiteLLM alias for --responder gateway")
    check.add_argument("--score", action="store_true", help="also score through POST /api/rag/evaluate")
    check.add_argument("--evaluator-model")
    check.add_argument("--usd-per-call", type=float, help="your evaluator price per call, for the report")
    compare = commands.add_parser("report", help="compare a baseline run record with a new one")
    compare.add_argument("baseline")
    compare.add_argument("current")
    compare.add_argument("--out")
    export = commands.add_parser("export", help="log a run record to MLflow (MLFLOW_TRACKING_URI)")
    export.add_argument("record")
    export.add_argument("--experiment", default="atlas-rag-quality")
    args = parser.parse_args(argv)
    if args.command == "report":
        load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
        write_output(args.out, report(load(args.baseline), load(args.current)))
        return 0
    if args.command == "export":
        record = json.loads(Path(args.record).read_text(encoding="utf-8"))
        tracking = http_client(require_env("MLFLOW_TRACKING_URI"))
        print(f"MLflow run {export_mlflow(tracking, record, args.experiment)}")
        return 0
    doc = load_fixtures(args.fixtures)
    respond, name = _responder(args)
    record = run(doc, respond, name, scorer_from_args(args, doc))
    write_output(args.out, json.dumps(record, indent=2) + "\n")
    for row in record["results"]:
        if row["failures"]:
            print(f"FAIL {row['id']}: {'; '.join(row['failures'])}", file=sys.stderr)
    print(f"{'PASS' if record['passed'] else 'FAIL'}: {len(record['results'])} fixtures", file=sys.stderr)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
