"""Run the two bounded synthetic evaluations after automatic host qualification."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

CAP = 1024 * 1024
CASE_IDS = ["refusal-own", "hostile-foreign", "service-delegation", "vulnerable-foreign"]
ASSERTION = """const o = typeof output === 'string' ? JSON.parse(output) : output;
const e = context.vars.expected;
const pass = o.case_id === context.vars.case_id && o.decision === e.decision &&
  o.decision_code === e.decision_code && o.resolved_owner === e.resolved_owner &&
  o.side_effect_count === e.side_effect_count;
return {pass, score: pass ? 1 : 0, reason: 'OWNERSHIP_AND_EFFECTS ' + context.vars.case_id};"""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def verify_hashes(files):
    for filename, expected in files.items():
        if digest(filename) != expected:
            raise ValueError("qualified input changed: " + filename)


def verify_required_files(bindings):
    keys = ("cases", "provider", "helper", "backend_identity", "python", "node", "cli",
            "package_json", "package_lock", "python_lock", "python_inventory", "npm_inventory",
            "python_base_inventory", "runtime_inventory")
    required = [str(Path(__file__).resolve())] + [bindings[key] for key in keys]
    sandbox = bindings["sandbox"]
    prefix = sandbox["launch_prefix"]
    if not prefix:
        raise ValueError("qualified OS containment launcher required")
    required += [prefix[0], sandbox["profile"], sandbox["qualification_receipt"]]
    for filename in required:
        if not Path(filename).is_absolute() or filename not in bindings["file_hashes"]:
            raise ValueError("missing exact absolute input binding")
    verify_hashes(bindings["file_hashes"])


def installed_entries(root):
    entries = list(root.rglob("*"))
    files = {str(path) for path in entries if path.is_file() and not path.is_symlink()}
    symlinks = {str(path): os.readlink(path) for path in entries if path.is_symlink()}
    return files, symlinks


def verify_inventory(path, expected_root):
    inventory = load(path)
    root = Path(inventory["root"])
    if root != expected_root or not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("installed inventory has the wrong trusted root")
    files, symlinks = installed_entries(root)
    if files != set(inventory["files"]) or symlinks != inventory["symlinks"]:
        raise ValueError("installed file set or literal symlink targets changed")
    verify_hashes(inventory["files"])  # Never hash an unexpected added symlink target.


def verify_inventories(bindings):
    python_root = Path(bindings["python"]).parent.parent
    npm_root = Path(bindings["package_json"]).parent / "node_modules"
    if not Path(bindings["cli"]).is_relative_to(npm_root):
        raise ValueError("CLI must belong to the actual isolated install")
    base_root = Path(bindings["python"]).resolve(strict=True).parent.parent
    verify_inventory(bindings["python_inventory"], python_root)
    verify_inventory(bindings["python_base_inventory"], base_root)
    verify_inventory(bindings["npm_inventory"], npm_root)
    verify_runtime_aliases(bindings["runtime_inventory"])


def verify_runtime_aliases(path):
    for filename, target in load(path)["runtime_symlinks"].items():
        alias = Path(filename)
        if not alias.is_absolute() or not alias.is_symlink() or os.readlink(alias) != target:
            raise ValueError("qualified runtime alias target changed: " + filename)


def verify_qualification(bindings):
    sandbox = bindings["sandbox"]
    qualification = load(sandbox["qualification_receipt"])
    if qualification["status"] != "QUALIFIED":
        raise ValueError("actual automatic qualification required")
    if qualification["launch_prefix"] != sandbox["launch_prefix"]:
        raise ValueError("OS containment launcher mismatch")
    if qualification["profile_sha256"] != digest(sandbox["profile"]):
        raise ValueError("OS profile changed")
    if qualification["write_root"] != bindings["output_base"]:
        raise ValueError("qualified kernel write root changed")
    verify_qualification_checks(qualification)
    for key in ("node", "python", "cli"):
        if qualification["runtime_sha256"][key] != digest(bindings[key]):
            raise ValueError("qualified runtime changed: " + key)


def verify_qualification_checks(qualification):
    checks = ("profile_applied", "direct_tcp_denied", "direct_dns_denied", "direct_udp_denied",
              "child_network_denied", "local_python_pipes_work")
    for check in checks:
        if qualification["checks"].get(check) is not True:
            raise ValueError("missing actual isolation prerequisite: " + check)


def require_private_directory(path):
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError("owned absolute private directory required")
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError("directory must be owned and private")


def prepare_evaluation(bindings, binding_path):
    base, root = Path(bindings["output_base"]), Path(bindings["run_root"])
    require_private_directory(base)
    require_private_directory(root)
    if root != base / "evaluation" or any(root.iterdir()):
        raise ValueError("fresh evaluation directory under qualified output base required")
    inputs = [binding_path] + [Path(path) for path in bindings["file_hashes"]]
    if any(path.resolve().is_relative_to(base.resolve()) for path in inputs):
        raise ValueError("generated inputs must be outside the kernel write grant")
    for name in ("home", "tmp", "config", "cache", "work", "results"):
        (root / name).mkdir(mode=0o700)
    (root / "budget.lock").touch(mode=0o600, exist_ok=False)
    save(root / "budget.json", {"admissions": [], "rejections": 0})
    return root


def context_for(binding_path):
    bindings = load(binding_path)
    if bindings["state"] != "QUALIFIED_FOR_EXACT_INPUTS":
        raise RuntimeError("automatic host/runtime qualification remains pending")
    verify_required_files(bindings)
    verify_inventories(bindings)
    verify_qualification(bindings)
    root = prepare_evaluation(bindings, binding_path)
    return {"bindings": bindings, "binding_path": binding_path, "root": root}


def private_environment(root):
    return {"HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
            "XDG_CACHE_HOME": str(root / "cache"), "LANG": "C", "TZ": "UTC",
            "PROMPTFOO_CONFIG_DIR": str(root / "config"),
            "PROMPTFOO_DISABLE_TELEMETRY": "1", "PROMPTFOO_DISABLE_UPDATE": "1",
            "PROMPTFOO_DISABLE_SHARING": "1", "PROMPTFOO_DISABLE_REMOTE_GENERATION": "1",
            "PROMPTFOO_TRACING_ENABLED": "false", "PROMPTFOO_FAILED_TEST_EXIT_CODE": "100"}


def owned_bytes(root):
    count = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                count += path.stat().st_size
        except FileNotFoundError:
            continue
    return count


def kill_and_reap(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    process.stdout.close()
    process.stderr.close()


def drain_ready(selector, output, count):
    for key, _ in selector.select(0.1):
        chunk = os.read(key.fd, 16384)
        if not chunk:
            selector.unregister(key.fileobj)
        else:
            count += len(chunk)
            if count > CAP:
                raise RuntimeError("CLI combined output cap")
            output.write(chunk)
    return count


def bounded(argv, cwd, env, log):
    started = time.monotonic()
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               close_fds=True, start_new_session=True)
    selector = selectors.DefaultSelector()
    for stream in (process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    try:
        count = 0
        with log.open("xb") as output:
            while selector.get_map() or process.poll() is None:
                if time.monotonic() - started > 60:
                    raise TimeoutError("CLI process-group deadline")
                count = drain_ready(selector, output, count)
                if owned_bytes(cwd.parent) > 8 * CAP:
                    raise RuntimeError("owned output directory cap")
        return process.wait(timeout=1)
    finally:
        kill_and_reap(process)
        selector.close()


def provider_config(context):
    bindings, root = context["bindings"], context["root"]
    return {"id": bindings["provider"], "config": {"python": bindings["python"],
            "helper": bindings["helper"], "bindings": str(context["binding_path"]),
            "diagnostics": str(root / "results" / "provider-diagnostics.jsonl")}}


def make_config(context, name, selected):
    path = context["root"] / "work" / (name + ".json")
    save(path, {"description": "Synthetic Atlas ownership boundary; no model",
        "prompts": ["fixture {{case_id}}"], "providers": [provider_config(context)],
        "evaluateOptions": {"timeoutMs": 10000, "maxEvalTimeMs": 30000,
                            "maxConcurrency": 1, "repeat": 1, "cache": False},
        "tests": [{"description": case["id"], "vars": {"case_id": case["id"], "expected": case["expected"]},
                   "assert": [{"type": "javascript", "value": ASSERTION}]} for case in selected]})
    return path


def cli_command(context, config_path, result_path):
    bindings = context["bindings"]
    return bindings["sandbox"]["launch_prefix"] + [bindings["node"], bindings["cli"], "eval",
            "--config", str(config_path), "--no-share", "--no-write", "--no-cache", "--repeat", "1",
            "-j", "1", "--output", str(result_path)]


def check_seeded_assertion(row):
    reason = "OWNERSHIP_AND_EFFECTS vulnerable-foreign"
    grade = row["gradingResult"]
    if row["score"] != 0:
        raise RuntimeError("seeded assertion row must have the published zero score")
    if grade["pass"] is not False:
        raise RuntimeError("seeded assertion must have a false boolean verdict")
    if (row["failureReason"], row.get("error"), grade["reason"]) != (1, reason, reason):
        raise RuntimeError("missing exact intended assertion failure")
    components = grade["componentResults"]
    if len(components) != 1:
        raise RuntimeError("wrong assertion component count")
    component = components[0]
    if component["pass"] is not False:
        raise RuntimeError("seeded component must have a false boolean verdict")
    if (component["reason"], component["assertion"]["type"]) != (reason, "javascript"):
        raise RuntimeError("wrong seeded assertion component")


def actual_expected(case, mode):
    if mode == "vulnerable":
        return {"decision": "allowed", "decision_code": 200,
                "resolved_owner": case["requested_user"], "side_effect_count": 1}
    return case["expected"]


def check_observation(observed, case, mode):
    identity = {"case_id": case["id"], "principal": case["principal"],
                "requested_user": case["requested_user"], "action": case["action"]}
    if any(observed[key] != value for key, value in identity.items()):
        raise RuntimeError("observed case, principal or requested action mismatch")
    if any(observed[key] != value for key, value in actual_expected(case, mode).items()):
        raise RuntimeError("actual authorization/effect evidence mismatch")
    if observed["model_text"] != case["model_text"]:
        raise RuntimeError("deterministic model text mismatch")


def check_row(row, case, mode):
    if row["vars"]["case_id"] != case["id"] or row["response"].get("error"):
        raise RuntimeError("wrong case/provider error")
    observed = row["response"]["output"]
    if isinstance(observed, str):
        observed = json.loads(observed)
    check_observation(observed, case, mode)
    if row["success"] is not (mode == "protected"):
        raise RuntimeError("wrong per-case assertion result")
    if mode == "protected":
        if row.get("error") or row["failureReason"] != 0:
            raise RuntimeError("unexpected positive-case error")
    else:
        check_seeded_assertion(row)
    return observed


def check_summary(path, selected, mode):
    summary = load(path)["results"]
    rows, stats = summary["results"], summary["stats"]
    if len(rows) != len(selected) or stats["errors"] != 0:
        raise RuntimeError("provider/startup/timeout/retry is not seeded-assertion evidence")
    totals = (3, 0) if mode == "protected" else (0, 1)
    if (stats["successes"], stats["failures"]) != totals:
        raise RuntimeError("wrong actual assertion totals")
    observations = [check_row(row, case, mode) for row, case in zip(rows, selected)]
    return stats, observations


def execute_run(context, mode, selected, expected_exit):
    root = context["root"]
    config_path = make_config(context, mode, selected)
    result_path = root / "results" / (mode + ".json")
    code = bounded(cli_command(context, config_path, result_path), root / "work",
                   private_environment(root), root / "results" / (mode + ".log"))
    if code != expected_exit or not result_path.is_file() or result_path.stat().st_size > CAP:
        raise RuntimeError("real CLI exit/output mismatch; not seeded-assertion evidence")
    stats, observations = check_summary(result_path, selected, mode)
    outcome = {"name": mode, "exit_code": code, "result": str(result_path),
               "sha256": digest(result_path), "successes": stats["successes"], "failures": stats["failures"]}
    return outcome, observations


def read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def audit_events(context, cases, observations):
    root = context["root"]
    budget = load(root / "budget.json")
    if budget != {"admissions": [case["id"] for case in cases], "rejections": 0}:
        raise RuntimeError("unexpected retry/admission/budget result")
    effects = read_events(root / "effects.jsonl")
    expected = [{"case_id": case["id"], "action": case["action"], "owner": case["requested_user"]}
                for case in cases if case["id"] != "hostile-foreign"]
    if effects != expected:
        raise RuntimeError("independent append-file audit failed")
    audit = read_events(root / "authorization.jsonl")
    if audit != [{key: value for key, value in row.items() if key != "model_text"} for row in observations]:
        raise RuntimeError("independent authorization audit mismatch")
    return {"budget": budget, "effects": effects, "authorization": audit}


def report_limits():
    return ["Model text is deterministic fixture data; this does not measure model injection resistance.",
            "Principals are constructed locally; bearer authentication is not exercised.",
            "Trusted-service delegation is intentional; this fixture does not establish tenant-isolated MCP authorization.",
            "No viewer starts. The community viewer lacks built-in authentication, SSO and team RBAC.",
            "Upstream does not recommend the community viewer for production.",
            "CLI startup performs local SQLite/libsql migrations despite --no-write; the database stays under the private output base."]


def write_report(context, outcomes, observations, audit):
    root = context["root"]
    save(root / "results" / "report.json", {"status": "QUALIFIED", "synthetic_only": True,
         "cli_runs": outcomes, **audit, "observations": observations, "limitations": report_limits(),
         "effects_sha256": digest(root / "effects.jsonl"), "seeded_assertion_failure_observed": True,
         "binding_sha256": digest(context["binding_path"]), "viewer_started": False,
         "model_security_claim": False})


def main():
    context = context_for(Path(sys.argv[1]).absolute())
    cases = load(context["bindings"]["cases"])
    if [case["id"] for case in cases] != CASE_IDS:
        raise ValueError("exact four fixed cases required")
    outcomes, observations = [], []
    runs = (("protected", cases[:3], 0), ("vulnerable", cases[3:], 100))
    for mode, selected, expected_exit in runs:
        outcome, observed = execute_run(context, mode, selected, expected_exit)
        outcomes.append(outcome)
        observations.extend(observed)
    audit = audit_events(context, cases, observations)
    verify_hashes(context["bindings"]["file_hashes"])
    verify_inventories(context["bindings"])
    write_report(context, outcomes, observations, audit)


if __name__ == "__main__":
    main()
