"""Compare LiteLLM model aliases on your own task set.

Run it as described in docs/operations/reusing-atlas.md (section 6.9).
It reuses the fixture file, prompt rendering, deterministic checks and
evaluator client of regress.py, its sibling in this directory.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import sys

import regress

# Atlas .env.example variables that take a LiteLLM alias.
ALIAS_ENV_KEYS = ("LITELLM_DEFAULT_MODEL", "LIGHTRAG_QUERY_LLM_MODEL",
                  "LANGMEM_EXTRACTION_MODEL", "HERMES_DEFAULT_MODEL")


@dataclass
class Plan:
    """The paid-request cap and the optional evaluator for one run.

    Every gateway and evaluator request counts against the cap, because the
    example cannot tell which aliases are billed. No gateway-side cap exists."""

    max_requests: int
    scorer: object = None


def _counted(function, used):
    def wrapped(*args):
        used[0] += 1
        return function(*args)

    return wrapped


def _quality(row, scorer):
    if row["error"]:
        return {"score": None, "status": "no answer"}
    if scorer is None:
        return {"score": None, "status": "unavailable"}
    values = [v for v in (row["scores"] or {}).values() if v is not None]
    if not values:
        return {"score": None, "status": "evaluator error" if row["scores"] is None else "not evaluable"}
    return {"score": round(sum(values) / len(values), 4), "status": "scored"}


def compare(doc, models, gateway, plan):
    """Run every task against every alias, in order, until the cap stops it.

    Each task's prompt is rendered once, so every alias gets the same bytes."""
    used = [0]
    gateway = _counted(gateway, used)
    scorer = None if plan.scorer is None else _counted(plan.scorer, used)
    per_task = 1 + (scorer is not None)
    prompts = [(task, regress.render(doc["prompt"], task)) for task in doc["fixtures"]]
    rows, stopped = [], False
    for model in models:
        respond = regress.gateway_responder(gateway, model)
        for task, prompt in prompts:
            if used[0] + per_task > plan.max_requests:
                stopped = True
                break
            row = regress.run_fixture(task, prompt, respond, scorer)
            rows.append({"model": model, "task": row.pop("id"), **row, "quality": _quality(row, scorer)})
        if stopped:
            break
    return {
        "schema": "atlas-model-compare/1",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": regress.SETTINGS,
        "prompt_sha256": regress.digest(doc["prompt"]),
        "tasks_sha256": regress.digest(doc["fixtures"]),
        "max_requests": plan.max_requests,
        "requests_used": used[0],
        "stopped_at_cap": stopped,
        "rows": rows,
        "candidates": [summarize(model, rows, len(prompts)) for model in models],
    }


def _mean(values):
    return round(sum(values) / len(values), 4) if values else None


def _status(mine, errors, task_count):
    if not mine:
        return "not run"
    if errors == len(mine):
        return "all errored"
    if len(mine) < task_count:
        return "incomplete"
    return "some errors" if errors else "ok"


def summarize(model, rows, task_count):
    """One candidate's totals. "all errored" never looks like a zero score:
    its mean quality is null, not 0."""
    mine = [row for row in rows if row["model"] == model]
    answered = [row for row in mine if not row["error"]]
    errors = len(mine) - len(answered)
    return {
        "model": model,
        "status": _status(mine, errors, task_count),
        "tasks_run": len(mine),
        "errors": errors,
        "checks_passed": sum(1 for row in mine if not row["failures"]),
        **_answer_totals(answered),
    }


def _answer_totals(answered):
    scores = [row["quality"]["score"] for row in answered]
    tokens = [value or 0 for row in answered for value in (row["tokens"] or {}).values()]
    return {
        "mean_quality": _mean([score for score in scores if score is not None]),
        "mean_latency_ms": _mean([row["latency_ms"] for row in answered]),
        "total_tokens": sum(tokens),
    }


def pick_winner(record):
    """A sample selection policy; replace it with your own.

    Only candidates that ran every task without an error are eligible."""
    eligible = [c for c in record["candidates"] if c["status"] == "ok"]
    if not eligible:
        return None
    best = max(eligible, key=lambda c: (-1 if c["mean_quality"] is None else c["mean_quality"],
                                         c["checks_passed"], -(c["mean_latency_ms"] or 0)))
    return best["model"]


def env_line(model, key="LITELLM_DEFAULT_MODEL"):
    if key not in ALIAS_ENV_KEYS:
        raise ValueError(f"{key} is not one of {', '.join(ALIAS_ENV_KEYS)}")
    return f"{key}={model}"


def _fmt(value):
    return "-" if value is None else str(value)


def print_summary(record, env_key, out=None):
    out = out or sys.stdout
    print("| Model | Status | Tasks | Errors | Checks passed | Mean quality | Mean latency ms | Tokens |", file=out)
    print("|---|---|---|---|---|---|---|---|", file=out)
    for c in record["candidates"]:
        cells = [c["model"], c["status"], c["tasks_run"], c["errors"], c["checks_passed"],
                 _fmt(c["mean_quality"]), _fmt(c["mean_latency_ms"]), c["total_tokens"]]
        print("| " + " | ".join(str(cell) for cell in cells) + " |", file=out)
    if record["stopped_at_cap"]:
        print(f"Stopped: the paid-request cap of {record['max_requests']} was reached "
              f"after {record['requests_used']} requests.", file=out)
    winner = record.get("winner")
    if winner is None:
        print("No winner: no candidate ran every task without an error.", file=out)
    else:
        print(f"Winner: {winner}. Paste into your Atlas .env:\n{env_line(winner, env_key)}", file=out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", nargs="+", required=True, help="LiteLLM aliases to compare")
    parser.add_argument("--tasks", default=str(regress.FIXTURES), help="a fixtures.json-shaped task file")
    parser.add_argument("--max-requests", type=int, default=50, help="paid-request cap for the whole run")
    parser.add_argument("--score", action="store_true", help="score answers through POST /api/rag/evaluate")
    parser.add_argument("--evaluator-model")
    parser.add_argument("--usd-per-call", type=float)
    parser.add_argument("--env-key", choices=ALIAS_ENV_KEYS, default="LITELLM_DEFAULT_MODEL")
    parser.add_argument("--out", help="write the run record here")
    args = parser.parse_args(argv)
    doc = regress.load_fixtures(args.tasks)
    endpoint = regress.require_env("ATLAS_LITELLM_HOST_ENDPOINT")
    gateway = regress.http_client(endpoint, os.environ.get("LITELLM_API_KEY"))
    record = compare(doc, args.models, gateway, Plan(args.max_requests, regress.scorer_from_args(args, doc)))
    record.update(gateway=endpoint, evaluator="atlas-ragas" if args.score else None, winner=pick_winner(record))
    if args.out:
        regress.write_output(args.out, json.dumps(record, indent=2) + "\n")
    print_summary(record, args.env_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
