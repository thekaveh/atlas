"""Execute the configured Crawl4AI guide with its actual literal file owner."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from tests.test_three_surface_build import env_reference_config, _candidate_document
from tests.three_surface_test_utils import ROOT, surface_text, _credential_select_script

SOURCE = "services/crawl4ai/README.md"
KEYS = ("DASHBOARD_USERNAME", "DASHBOARD_PASSWORD", "CRAWL4AI_API_TOKEN", "KONG_HTTP_PORT")
PLAIN = dict(zip(KEYS, ("owned-user", "owned-password", "owned-token", "63100")))
LITERALS = {
    "single-quoted": ("'{}'", PLAIN),
    "double-quoted": ('"{}"', PLAIN),
    "export-spaced": ("{}", PLAIN),
    "dollar": ("'{}'", dict(zip(KEYS, ("u$d", "p${LITERAL}", "t$d", "$port")))),
    "backslash": ("'{}'", dict(zip(KEYS, ("u\\d", "p\\word", "t\\d", "p\\ort")))),
    "double-quote": ("'{}'", dict(zip(KEYS, ('u"d', 'p"word', 't"d', 'p"ort')))),
    "single-quote": ('"{}"', dict(zip(KEYS, ("u'd", "p'word", "t'd", "p'ort")))),
    "space-hash": ('"{}"', dict(zip(KEYS, ("u #d", "p #word", "t #d", "p #ort")))),
    "padded": ("'{}'", dict(zip(KEYS, ("  user  ", "  password  ", "  token  ", "  port  ")))),
    "unicode-long": ("'{}'", dict(zip(KEYS, ("ü" * 100, "密" * 100, "tökén", "63100")))),
}
VARIANTS = ("plain", *LITERALS, "relative-selected", "absolute-selected", "stale-inherited",
            "missing-file", "missing-selected", "directory-selected", "invalid-selected",
            "selected-default-invalid", "unsupported-command", "unsupported-expansion",
            "repeated-setup", *(f"{kind}-{key}" for key in KEYS
                                 for kind in ("missing", "blank", "whitespace")))
CASES = [pytest.param((surface, variant), id=f"{surface}-{variant}")
         for surface in ("repo", "site", "wiki") for variant in VARIANTS]
OWNER = '''import json
from core.config_parser import ConfigParser
parser = ConfigParser(root_dir=".")
try:
    if not parser.env_file_path.is_file(): raise ValueError("selected file missing")
    values = parser.parse_env_file()
    print(json.dumps({key: values.get(key, "") for key in
        ("DASHBOARD_USERNAME", "DASHBOARD_PASSWORD", "CRAWL4AI_API_TOKEN", "KONG_HTTP_PORT")}))
except (ValueError, OSError) as exc:
    print(json.dumps({"error": type(exc).__name__}))
'''


def _rows(values, template="{}", exported=False):
    prefix, separator = ("export ", " = ") if exported else ("", "=")
    return "".join(prefix + key + separator + template.format(value) + "\n"
                   for key, value in values.items())


def _spec(variant):
    template, values = LITERALS.get(variant, ("{}", PLAIN))
    spec = {"values": dict(values), "selector": None, "shell": {}, "error": False}
    spec["text"] = _rows(values, template, variant == "export-spaced")
    _unusable_value(spec, variant)
    if variant in {"missing-file", "missing-selected", "directory-selected", "invalid-selected"}:
        spec["error"] = True
    if variant == "missing-file":
        spec["text"] = None
    if variant.startswith("unsupported-"):
        spec["text"] += {"unsupported-command": "UNRELATED=$(printf executed > shell-executed)\n",
                          "unsupported-expansion": "UNRELATED=$INHERITED_EXPANSION\n"}[variant]
        spec["error"] = True
    if variant == "stale-inherited" or variant.startswith(("missing-", "blank-", "whitespace-")):
        spec["shell"] = {key: "stale-" + key for key in KEYS}
    return spec


def _unusable_value(spec, variant):
    for key in KEYS:
        if variant == "missing-" + key:
            spec["values"].pop(key)
        if variant in {"blank-" + key, "whitespace-" + key}:
            spec["values"][key] = "   " if variant.startswith("whitespace-") else ""
    if variant.startswith(("missing-", "blank-", "whitespace-")) and variant.split("-", 1)[1] in KEYS:
        spec["text"] = _rows(spec["values"], "'{}'")


def _fixture(directory, variant):
    checkout = directory / "owned checkout"
    checkout.mkdir()
    (checkout / "bootstrapper").symlink_to(ROOT / "bootstrapper", target_is_directory=True)
    spec = _spec(variant)
    if spec["text"] is not None:
        (checkout / ".env").write_text(spec["text"])
    selected_cases = {"relative-selected", "absolute-selected", "missing-selected",
                      "directory-selected", "invalid-selected", "selected-default-invalid"}
    selected = checkout / "selected active.env"
    if variant in selected_cases:
        spec["selector"] = str(selected) if variant == "absolute-selected" else selected.name
        spec["values"] = dict(zip(KEYS, ("selected-user", "selected-password", "selected-token", "64100")))
        if variant not in {"missing-selected", "directory-selected"}:
            selected.write_text(_rows(spec["values"]) + ("BROKEN: invalid\n" if variant == "invalid-selected" else ""))
        if variant == "directory-selected":
            selected.mkdir()
        if variant == "selected-default-invalid":
            (checkout / ".env").write_text("BROKEN: invalid\n")
    (checkout / "inactive.env").write_text(_rows({key: "inactive-" + key for key in KEYS}))
    _capture_inputs(checkout, directory)
    (directory / "fixture.json").write_text(json.dumps(spec, indent=2) + "\n")
    return checkout, spec


def _capture_inputs(checkout, directory):
    destination = directory / "raw-inputs"
    destination.mkdir()
    for path in (checkout / ".env", checkout / "selected active.env", checkout / "inactive.env"):
        if path.is_file():
            (destination / path.name).write_bytes(path.read_bytes())


def _environment(directory, spec):
    tools = directory / "tools"
    tools.mkdir()
    for name in ("uv", "base64"):
        executable = shutil.which(name)
        assert executable, f"Required real executable missing: {name}"
        (tools / name).symlink_to(Path(executable).resolve())
    recorder = tools / "curl"
    recorder.write_text("#!" + sys.executable + '\nimport json,os,sys\nfrom pathlib import Path\n'
                        'with Path(os.environ["CRAWL4AI_GUIDE_CALLS"]).open("a") as stream:\n'
                        '    stream.write(json.dumps(sys.argv[1:])+"\\n")\n'
                        'print("owned health response")\n')
    recorder.chmod(0o700)
    environment = {"PATH": str(tools), "UV_PROJECT_ENVIRONMENT": sys.prefix,
                   "UV_PYTHON": sys.executable, "UV_NO_SYNC": "1", "UV_FROZEN": "1",
                   "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", "VIRTUAL_ENV": sys.prefix,
                   "UV_CACHE_DIR": str(directory / "uv-cache"), "PYTHONDONTWRITEBYTECODE": "1",
                   "CRAWL4AI_GUIDE_CALLS": str(directory / "calls.jsonl"),
                   "INHERITED_EXPANSION": "shell-expanded", **spec["shell"]}
    if spec["selector"] is not None:
        environment["ATLAS_ENV_FILE"] = spec["selector"]
    return environment


def _run(script, checkout, environment):
    return subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", script], cwd=checkout,
                          env=environment, input="", capture_output=True, text=True, timeout=30)


def _record_run(directory, name, script, result):
    (directory / (name + ".sh")).write_text(script)
    (directory / (name + ".stdout")).write_text(result.stdout)
    (directory / (name + ".stderr")).write_text(result.stderr)
    (directory / (name + ".exit")).write_text(str(result.returncode) + "\n")


def _owner(checkout, environment, directory):
    script = "PYTHONPATH=bootstrapper uv run --project bootstrapper python - <<'PY'\n" + OWNER + "PY\n"
    result = _run(script, checkout, environment)
    _record_run(directory, "owner", script, result)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _script(surface, config):
    text = (ROOT / SOURCE).read_text() if surface == "repo" else surface_text(SOURCE, surface)
    document = _candidate_document(text, config)
    blocks = ["".join(node.itertext()) for node in document.findall(".//pre/code")]
    return _credential_select_script(blocks, ("Proxy-Authorization:", "crawl4ai.localhost:"))


def _expected(values):
    if "error" in values or not all(values.get(key, "").strip() for key in KEYS):
        return {"success": False, "calls": [], "stdout": "", "no_shell_effect": True}
    basic = base64.b64encode((values[KEYS[0]] + ":" + values[KEYS[1]]).encode("utf-8")).decode("ascii")
    argv = ["-H", "Proxy-Authorization: Basic " + basic,
            "-H", "Authorization: Bearer " + values[KEYS[2]],
            "http://crawl4ai.localhost:" + values[KEYS[3]] + "/health"]
    return {"success": True, "calls": [argv], "stdout": "owned health response\n", "no_shell_effect": True}


def _refresh_script(checkout, values):
    path = checkout / ".env"
    payload = f"from pathlib import Path\nPath({str(path)!r}).write_text({_rows(values)!r})\n"
    return "uv run --project bootstrapper python - <<'PY'\n" + payload + "PY\n"


def probe(case, directory, config):
    surface, variant = case
    checkout, spec = _fixture(directory, variant)
    environment = _environment(directory, spec)
    owner = _owner(checkout, environment, directory)
    owner_expected = {"error": "ValueError"} if spec["error"] else {key: spec["values"].get(key, "") for key in KEYS}
    expected = _expected(owner_expected)
    script = _script(surface, config)
    if variant == "repeated-setup":
        changed = dict(zip(KEYS, ("new-user", "new-password", "new-token", "64200")))
        (checkout / ".env").write_text(_rows(changed))
        second = directory / "second-snapshot"
        second.mkdir()
        _capture_inputs(checkout, second)
        second_owner = _owner(checkout, environment, second)
        assert second_owner == changed
        (checkout / ".env").write_text(spec["text"])
        script += "\n" + _refresh_script(checkout, changed) + script
        expected["calls"] += _expected(changed)["calls"]
        expected["stdout"] += _expected(changed)["stdout"]
    result = _run(script, checkout, environment)
    _record_run(directory, "extracted-fence", script, result)
    log = directory / "calls.jsonl"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    observed = {"success": result.returncode == 0, "calls": calls, "stdout": result.stdout,
                "no_shell_effect": not (checkout / "shell-executed").exists()}
    (directory / "result.json").write_text(json.dumps({"case": case, "spec": spec,
        "environment": environment, "owner": owner, "owner_expected": owner_expected,
        "observed": observed, "expected": expected, "exit": result.returncode,
        "stderr": result.stderr}, indent=2) + "\n")
    assert owner == owner_expected
    return observed, expected


@pytest.mark.parametrize("case", CASES)
def test_documented_crawl4ai_uses_selected_literal_values(case, tmp_path, env_reference_config):
    observed, expected = probe(case, tmp_path, env_reference_config)
    assert observed == expected
