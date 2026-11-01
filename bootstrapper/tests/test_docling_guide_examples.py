"""Execute Docling guide fences against their real source configuration owners."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from tests.test_three_surface_build import env_reference_config
from tests.three_surface_test_utils import ROOT, native_executable, surface_text, _credential_select_script

SOURCES = {
    "container": "services/doc-processor/README.md",
    "aggregate-native": "services/doc-processor/README.md",
    "native-api": "services/docling/provider/localhost/README.md",
    "native-settings": "services/docling/provider/localhost/README.md",
}
COMMON = {
    "plain": ("DOCLING_API_TOKEN=owned-plain\n", "owned-plain"),
    "single-quoted": ("DOCLING_API_TOKEN='owned-single'\n", "owned-single"),
    "double-quoted": ('DOCLING_API_TOKEN="owned-double"\n', "owned-double"),
    "exported": ("export DOCLING_API_TOKEN = owned-export\n", "owned-export"),
    "dollar": ("DOCLING_API_TOKEN='owned$literal'\n", "owned$literal"),
    "backslash": ("DOCLING_API_TOKEN='owned\\literal'\n", "owned\\literal"),
    "quote": ("DOCLING_API_TOKEN='owned\"quote'\n", 'owned"quote'),
    "space-hash": ('DOCLING_API_TOKEN="owned space #1"\n', "owned space #1"),
    "padded": ("DOCLING_API_TOKEN='  owned padded  '\n", "  owned padded  "),
    "missing-token": ("", ""),
    "blank-token": ("DOCLING_API_TOKEN=\n", ""),
    "whitespace-token": ("DOCLING_API_TOKEN='   '\n", "   "),
    "missing-file": (None, ""),
}
CONTAINER = ("relative-file", "absolute-file", "missing-selected", "invalid-selected",
             "directory-selected", "missing-port", "blank-port", "whitespace-port",
             "inherited-token", "literal-port")
NATIVE = ("shell-token", "missing-file-shell-token", "blank-shell-token",
          "whitespace-shell-token", "interpolate-single", "interpolate-double",
          "custom-port", "shell-port", "empty-file-port", "empty-shell-port", "ignored-selector")
CASES = [pytest.param((surface, context, variant), id=f"{surface}-{context}-{variant}")
         for surface in ("repo", "site", "wiki") for context in SOURCES
         for variant in (*COMMON, *(CONTAINER if context == "container" else NATIVE))]
NATIVE_PATH = Path("services/docling/provider/localhost")


def _document(surface, context, config):
    import html
    import re
    import xml.etree.ElementTree as ET
    import markdown

    source = SOURCES[context]
    text = (ROOT / source).read_text() if surface == "repo" else surface_text(source, surface)
    rendered = markdown.markdown(text, extensions=config.markdown_extensions,
                                 extension_configs=config.mdx_configs)
    rendered = re.sub(r"&([A-Za-z][A-Za-z0-9]+);",
                      lambda match: html.escape(html.unescape(match.group(0))), rendered)
    return ET.fromstring("<div>" + rendered + "</div>")


def _script(document, context):
    blocks = ["".join(node.itertext()) for node in document.findall(".//pre/code")]
    if context == "native-settings":
        return _credential_select_script(blocks, ("export DOCLING_DEVICE=cpu",))
    if context == "native-api":
        return _credential_select_script(blocks, ("file=@document.pdf", "table_mode=accurate"))
    aggregate = [block for block in blocks if "file=@document.pdf" in block]
    if len(aggregate) == 1:
        # Baseline combines both sources: retain each complete curl and its shared setup.
        setup, requests = aggregate[0].split("# Container GPU source", 1)
        container, native = requests.split("# Localhost source: use the native provider port instead", 1)
        return setup + (container if context == "container" else native)
    marker = "table_mode=accurate"
    return _credential_select_script(aggregate, (marker,) if context == "container" else (),
                                     () if context == "container" else (marker,))


def _fixture_spec(variant):
    text, token = COMMON.get(variant, COMMON["plain"])
    spec = {"text": text, "token": token, "port": 18159, "shell": {}, "selector": None}
    native = {
        "shell-token": ({"DOCLING_API_TOKEN": "shell-owned"}, "shell-owned"),
        "missing-file-shell-token": ({"DOCLING_API_TOKEN": "shell-no-file"}, "shell-no-file"),
        "blank-shell-token": ({"DOCLING_API_TOKEN": ""}, ""),
        "whitespace-shell-token": ({"DOCLING_API_TOKEN": "   "}, "   "),
    }
    if variant in native:
        spec["shell"], spec["token"] = native[variant]
    if variant == "missing-file-shell-token":
        spec["text"] = None
    if variant.startswith("interpolate-"):
        quote = "'" if variant.endswith("single") else '"'
        spec.update(text=f"EXPAND=expanded\nDOCLING_API_TOKEN={quote}owned-${{EXPAND}}{quote}\n",
                    token="owned-expanded")
    return spec


def _fixture_files(checkout, variant, spec):
    text = spec["text"]
    if text is not None:
        port_rows = {"missing-port": "", "blank-port": "DOC_PROCESSOR_PORT=\n",
                     "whitespace-port": "DOC_PROCESSOR_PORT='   '\n",
                     "literal-port": "DOC_PROCESSOR_PORT='literal-port'\n"}
        text += port_rows.get(variant, "DOC_PROCESSOR_PORT=63051\n")
        native_port = {"custom-port": "55021", "shell-port": "55021",
                       "empty-file-port": "", "empty-shell-port": "55021"}.get(variant)
        if native_port is not None:
            text += f"DOCLING_LOCALHOST_PORT={native_port}\n"
        (checkout / ".env").write_text(text)
    if variant in {"custom-port", "shell-port"}:
        spec["port"] = 55021 if variant == "custom-port" else 55022
    if variant in {"shell-port", "empty-shell-port"}:
        spec["shell"]["DOCLING_LOCALHOST_PORT"] = "55022" if variant == "shell-port" else ""
    if variant in {"relative-file", "absolute-file", "missing-selected", "invalid-selected", "directory-selected"}:
        _selected_file(checkout, variant, spec)
    if variant == "ignored-selector":
        (checkout / "inactive.env").write_text("DOCLING_API_TOKEN=inactive\nDOCLING_LOCALHOST_PORT=55023\n")
        spec["selector"] = "inactive.env"


def _selected_file(checkout, variant, spec):
    selected = checkout / "selected active.env"
    if variant in {"relative-file", "absolute-file"}:
        selected.write_text("DOCLING_API_TOKEN='owned-selected'\nDOC_PROCESSOR_PORT=64051\n")
        spec["token"] = "owned-selected"
    if variant == "invalid-selected":
        selected.write_text("DOCLING_API_TOKEN=selected\nDOC_PROCESSOR_PORT=64051\nBROKEN: invalid\n")
    if variant == "directory-selected":
        selected.mkdir()
    spec["selector"] = str(selected) if variant == "absolute-file" else selected.name


def _fixture(case, directory):
    _, context, variant = case
    checkout = directory / "owned checkout"
    native = checkout / NATIVE_PATH
    native.mkdir(parents=True)
    (checkout / "bootstrapper").symlink_to(ROOT / "bootstrapper", target_is_directory=True)
    for name in ("pyproject.toml", "uv.lock"):
        (native / name).symlink_to(ROOT / NATIVE_PATH / name)
    spec = _fixture_spec(variant)
    _fixture_files(checkout, variant, spec)
    spec["cwd"] = str(native if context == "native-settings" else checkout)
    return checkout, spec


def _environment(directory, spec):
    tools = directory / "tools"
    tools.mkdir()
    for name in ("uv", "sed"):
        (tools / name).symlink_to(native_executable(name))
    recorder = tools / "curl"
    recorder.write_text("#!" + sys.executable + '\nimport json,os,sys\nfrom pathlib import Path\n'
                        'with Path(os.environ["DOCLING_GUIDE_CALLS"]).open("a") as stream:\n'
                        '    stream.write(json.dumps(sys.argv[1:])+"\\n")\n'
                        'print("owned conversion response")\n')
    recorder.chmod(0o700)
    environment = {"PATH": str(tools), "UV_PROJECT_ENVIRONMENT": sys.prefix,
                   "UV_PYTHON": sys.executable, "UV_NO_SYNC": "1", "UV_FROZEN": "1",
                   "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", "VIRTUAL_ENV": sys.prefix,
                   "UV_CACHE_DIR": str(directory / "uv-cache"), "PYTHONDONTWRITEBYTECODE": "1",
                   "DOCLING_GUIDE_CALLS": str(directory / "calls.jsonl"), **spec["shell"]}
    if spec["selector"] is not None:
        environment["ATLAS_ENV_FILE"] = spec["selector"]
    return environment


def _native_owner(checkout):
    """Select actual dotenv statements and port expression without importing the server."""
    source = (ROOT / NATIVE_PATH / "server.py").read_text()
    tree = ast.parse(source)
    early = next(node for node in tree.body if isinstance(node, ast.Try))
    assert isinstance(early.body[0], ast.ImportFrom) and early.body[0].module == "dotenv"
    port = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "port" for target in node.targets))
    token = _boundary_token_expression()
    return ("import os,json\nfrom pathlib import Path\nprefix='DOCLING'\n"
            + f"__file__={str(checkout / NATIVE_PATH / 'server.py')!r}\n"
            + ast.get_source_segment(source, early) + "\n"
            + "print(json.dumps({'token':" + ast.unparse(token) + ", 'port':"
            + ast.unparse(port.value) + "}))\n")


def _boundary_token_expression():
    source = (ROOT / "services/docling/provider/provider_boundary.py").read_text()
    return next(node.value for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.keyword) and node.arg == "token")


def _container_owner():
    return '''import json
from core.config_parser import ConfigParser
parser=ConfigParser(root_dir=".")
try:
    if not parser.env_file_path.is_file(): raise ValueError("selected file missing")
    values=parser.parse_env_file()
    print(json.dumps({"token":values.get("DOCLING_API_TOKEN", ""),
                      "port":values.get("DOC_PROCESSOR_PORT", "")}))
except (ValueError,OSError) as exc:
    print(json.dumps({"error":type(exc).__name__}))
'''


def _run(script, cwd, environment):
    return subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", script], cwd=cwd,
                          env=environment, input="", capture_output=True, text=True, timeout=30)


def _owner_probe(case, checkout, environment):
    context = case[1]
    payload = _container_owner() if context == "container" else _native_owner(checkout)
    project = "bootstrapper" if context == "container" else str(NATIVE_PATH)
    prefix = "PYTHONPATH=bootstrapper " if context == "container" else ""
    script = prefix + f"uv run --project {project} python - <<'PY'\n" + payload + "PY\n"
    result = _run(script, checkout, environment)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), script


def _owner_expected(case, spec):
    _, context, variant = case
    if context != "container":
        return {"token": spec["token"], "port": spec["port"]}
    if variant in {"missing-file", "missing-selected", "directory-selected", "invalid-selected"}:
        return {"error": "ValueError"}
    port = {"relative-file": "64051", "absolute-file": "64051", "missing-port": "",
            "blank-port": "", "whitespace-port": "   ", "literal-port": "literal-port"}.get(variant, "63051")
    return {"token": spec["token"], "port": port}


def _expected(case, owner):
    _, context, _ = case
    if context == "native-settings":
        return {"success": True, "calls": [], "stdout": json.dumps({"token": owner["token"], "port": 18159}) + "\n"}
    valid = "error" not in owner and bool(owner["token"].strip()) and bool(str(owner["port"]).strip())
    if not valid:
        return {"success": False, "calls": [], "stdout": ""}
    forms = ["file=@document.pdf", "output_format=markdown"]
    if context == "container":
        forms += ["use_ocr=auto", "table_mode=accurate"]
    if context == "native-api":
        forms += ["table_mode=accurate"]
    argv = ["-X", "POST", f"http://localhost:{owner['port']}/v1/document/convert",
            "-H", "Authorization: Bearer " + owner["token"]]
    for form in forms:
        argv += ["-F", form]
    return {"success": True, "calls": [argv], "stdout": "owned conversion response\n"}


def probe(case, directory, config):
    checkout, spec = _fixture(case, directory)
    environment = _environment(directory, spec)
    if case[2] == "inherited-token":
        environment.update(DOCLING_API_TOKEN="stale-shell", DOC_PROCESSOR_PORT="stale-shell-port")
    owner, owner_script = _owner_probe(case, checkout, environment)
    assert owner == _owner_expected(case, spec), (case, owner, spec)
    script = _script(_document(case[0], case[1], config), case[1])
    if case[1] == "native-settings":
        script += "\nuv run python - <<'PY'\n" + _native_owner(checkout) + "PY\n"
    result = _run(script, spec["cwd"], environment)
    log = directory / "calls.jsonl"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    observed = {"success": result.returncode == 0, "calls": calls, "stdout": result.stdout}
    expected = _expected(case, owner)
    (directory / "extracted-fence.sh").write_text(script)
    (directory / "owner-probe.sh").write_text(owner_script)
    (directory / "result.json").write_text(json.dumps({"case": case, "spec": spec,
        "environment": environment, "owner": owner, "observed": observed, "expected": expected,
        "exit": result.returncode, "stderr": result.stderr}, indent=2) + "\n")
    return observed, expected


@pytest.mark.parametrize("case", CASES)
def test_documented_docling_uses_source_values(case, tmp_path, env_reference_config):
    observed, expected = probe(case, tmp_path, env_reference_config)
    assert observed == expected
