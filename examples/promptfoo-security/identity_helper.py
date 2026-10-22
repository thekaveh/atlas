"""Actual Backend ownership seam, with synthetic effects and a four-call budget."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


class BudgetExceeded(RuntimeError):
    """A duplicate or fifth attempt must never reach authorization/effects."""


def read_json(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_counter(path, value):
    temporary = path.with_suffix(".next")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def append(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def admitted(root, case_id):
    # The permanent lock inode is NEVER replaced or unlinked.
    with (root / "budget.lock").open("r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        counter_path = root / "budget.json"
        budget = read_json(counter_path)
        if len(budget["admissions"]) >= 4 or case_id in budget["admissions"]:
            budget["rejections"] += 1
            write_counter(counter_path, budget)
            raise BudgetExceeded("retry or hard four-admission budget exceeded")
        budget["admissions"].append(case_id)
        write_counter(counter_path, budget)  # Before import, authorization, effects.
        yield


def load_identity(bindings):
    identity = Path(bindings["backend_identity"])
    if digest(identity) != bindings["file_hashes"][str(identity)]:
        raise ValueError("Backend identity source changed")
    spec = importlib.util.spec_from_file_location("_atlas_fixture_identity", identity)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixed_case(bindings, case_id):
    path = Path(bindings["cases"])
    if digest(path) != bindings["file_hashes"][str(path)]:
        raise ValueError("case input changed")
    return next(item for item in read_json(path) if item["id"] == case_id)


def decide(module, case):
    principal = module.BackendPrincipal(**case["principal"])
    try:
        if case["variant"] == "protected":
            owner = module.authorize_user_id(principal, case["requested_user"])
        elif (case["variant"], case["id"]) == ("vulnerable-fixture-only", "vulnerable-foreign"):
            owner = case["requested_user"]  # Deliberate fixture-only bypass.
        else:
            raise ValueError("unknown fixed variant")
    except module.HTTPException as exc:
        return {"decision": "denied", "decision_code": exc.status_code, "resolved_owner": None}
    # Local synthetic decision code, not an HTTP request/response.
    return {"decision": "allowed", "decision_code": 200, "resolved_owner": owner}


def record_decision(root, case, decision):
    count = 0
    if decision["decision"] == "allowed":
        append(root / "effects.jsonl", {"case_id": case["id"], "action": case["action"],
                                       "owner": decision["resolved_owner"]})
        count = 1
    audit = {"case_id": case["id"], "principal": case["principal"],
             "requested_user": case["requested_user"], "action": case["action"],
             **decision, "side_effect_count": count}
    append(root / "authorization.jsonl", audit)
    return {**audit, "model_text": case["model_text"]}


def evaluate(bindings, case_id):
    root = Path(bindings["run_root"])
    case = fixed_case(bindings, case_id)
    with admitted(root, case_id):
        module = load_identity(bindings)
        return record_decision(root, case, decide(module, case))


def qualify(bindings, request):
    nonce = request["nonce"]
    if set(request) != {"nonce"} or not isinstance(nonce, str) or not 16 <= len(nonce) <= 128:
        raise ValueError("bounded qualification nonce required")
    module = load_identity(bindings)  # Same real import path as evaluation.
    return {"mode": "qualification", "nonce": nonce,
            "backend_identity_sha256": digest(bindings["backend_identity"]),
            "module_file": module.__file__,
            "backend_principal": callable(module.BackendPrincipal),
            "authorize_user_id": callable(module.authorize_user_id)}


def main():
    request = sys.stdin.buffer.read(4097)
    if len(request) > 4096:
        raise ValueError("oversized request")
    request = json.loads(request)
    if len(sys.argv) == 3 and sys.argv[1] == "--qualify":
        output = qualify(read_json(sys.argv[2]), request)
    elif len(sys.argv) == 2:
        output = evaluate(read_json(sys.argv[1]), request["case_id"])
    else:
        raise ValueError("expected bindings path, optionally preceded by --qualify")
    print(json.dumps(output))


if __name__ == "__main__":
    main()
