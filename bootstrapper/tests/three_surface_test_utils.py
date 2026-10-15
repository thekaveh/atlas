"""Helpers for feature tests that assert generated documentation content."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.docs.build_docs import render_site, render_wiki
from scripts.docs.manifest import Manifest, Page, load_manifest
from scripts.docs.render_diagrams import render_all


ROOT = Path(__file__).resolve().parents[2]
_PROJECTION_TEMP = TemporaryDirectory(prefix="atlas-test-docs-")
PROJECTION_ROOT = Path(_PROJECTION_TEMP.name)


@lru_cache(maxsize=1)
def _manifest() -> Manifest:
    manifest_path = ROOT / "docs" / "manifest.yaml"
    manifest = load_manifest(manifest_path, ROOT)
    render_site(manifest, ROOT, PROJECTION_ROOT / "site")
    render_wiki(manifest, ROOT, PROJECTION_ROOT / "wiki")
    render_all(
        manifest,
        ROOT,
        PROJECTION_ROOT / "site" / "assets" / "img",
        ROOT / "docs" / "diagrams" / "img",
        PROJECTION_ROOT / "wiki" / "img",
        check_png=True,
    )
    return manifest


def ensure_generated_docs() -> Manifest:
    """Build both generated surfaces once and return their manifest."""
    return _manifest()


def _page_for_source(source: str) -> Page:
    matches = [page for page in _manifest().pages if page.source == source]
    if len(matches) != 1:
        raise AssertionError(f"Expected one manifest page for {source!r}, found {len(matches)}")
    return matches[0]


def surface_text(source: str, surface: str) -> str:
    """Return a canonical page as rendered for ``site`` or ``wiki``."""
    page = _page_for_source(source)
    if surface == "site":
        path = PROJECTION_ROOT / "site" / page.site_path
    elif surface == "wiki":
        path = PROJECTION_ROOT / "wiki" / page.wiki_path
    else:
        raise ValueError(f"Unknown documentation surface: {surface}")
    return path.read_text(encoding="utf-8")


def markdown_block_shape(text: str) -> list[tuple[str, int]]:
    """Record list, quote and code-block containers without their prose."""
    from html.parser import HTMLParser

    class Blocks(HTMLParser):
        def __init__(self):
            super().__init__()
            self.shape = []

        def handle_starttag(self, tag, attrs):
            if tag in {"ul", "ol", "li", "pre", "blockquote"}:
                self.shape.append((tag, 1))

        def handle_endtag(self, tag):
            if tag in {"ul", "ol", "li", "pre", "blockquote"}:
                self.shape.append((tag, -1))

    parser = Blocks()
    parser.feed(text)
    parser.close()
    return parser.shape


def documented_baseline_commands(markdown: str) -> list[str]:
    """Find baseline command examples without fixing headings or fence grouping."""
    import re
    from markdown_it import MarkdownIt

    snippets = [
        token.content for token in MarkdownIt("commonmark").parse(markdown)
        if token.type == "fence" and token.info.strip() in {"bash", "sh"}
        and (
            re.search(r"\bpytest\s+tests/test_fragment_equivalence\.py(?:\s|$)", token.content)
            or "BASELINE.write_text" in token.content
        )
    ]
    assert snippets, "No documented baseline commands found"
    return snippets


def probe_documented_shell_commands(snippets: list[str], tmp_path: Path):
    """Record command directories and arguments without executing their payloads."""
    import json
    import shlex
    import subprocess
    import sys

    checkout = tmp_path / "checkout with spaces"
    (checkout / "bootstrapper").mkdir(parents=True)
    tools = tmp_path / "owned-tools"
    tools.mkdir()
    log = tmp_path / "uv-calls.jsonl"
    recorder = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['ATLAS_DOC_COMMAND_LOG']).open('a') as stream:\n"
        "    stream.write(json.dumps({'cwd': os.getcwd(), 'args': sys.argv[1:]}) + '\\n')\n"
    )
    uv = tools / "uv"
    uv.write_text(
        "#!/bin/sh\nexec "
        + shlex.join([sys.executable, "-I", "-S", "-c", recorder])
        + ' "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-c",
         "\n".join(snippets) + '\nprintf "%s" "$PWD"\n'],
        cwd=checkout,
        env={"PATH": str(tools), "ATLAS_DOC_COMMAND_LOG": str(log)},
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls, checkout


def memory_size_bytes(value: str | int) -> float:
    """Interpret the binary size literals used by Neo4j and Compose examples."""
    import re

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT]?)(?:i?B)?", str(value).strip(), re.I)
    assert match is not None, value
    number, unit = match.groups()
    powers = {"": 0, "K": 1, "M": 2, "G": 3, "T": 4}
    return float(number) * 1024 ** powers[unit.upper()]


def documented_neo4j_memory_budgets(markdown: str) -> list[tuple[float, ...]]:
    """Read actual YAML tuning examples and their declared container budget."""
    from markdown_it import MarkdownIt
    import yaml

    manifest = yaml.safe_load((ROOT / "services/neo4j/service.yml").read_text())
    default_limit = next(
        entry["default"] for entry in manifest["env"] if entry["name"] == "NEO4J_MEMORY_LIMIT"
    )
    heap_key = "NEO4J_server_memory_heap_max__size"
    cache_key = "NEO4J_server_memory_pagecache_size"
    budgets = []
    for token in MarkdownIt("commonmark").parse(markdown):
        if token.type != "fence" or token.info.strip() not in {"yaml", "yml"}:
            continue
        data = yaml.safe_load(token.content) or {}
        spec = data.get("services", {}).get("neo4j-graph-db", {})
        environment = spec.get("environment", {})
        if not {heap_key, cache_key} <= environment.keys():
            continue
        limit = spec.get("deploy", {}).get("resources", {}).get("limits", {}).get(
            "memory", default_limit
        )
        initial = environment.get("NEO4J_server_memory_heap_initial__size", environment[heap_key])
        budgets.append(tuple(memory_size_bytes(value) for value in (
            initial, environment[heap_key], environment[cache_key], limit
        )))
    return budgets


_CREDENTIAL_EXAMPLES = {
    "hermes-airflow": ("hermes", "AIRFLOW_ADMIN_PASSWORD"),
    "hermes-diagnostics": ("hermes", "HERMES_API_KEY"),
    "openclaw-onboard": ("openclaw", "PROJECT_NAME"),
    "openclaw-cli": ("openclaw", "OPENCLAW_GATEWAY_TOKEN"),
    "openclaw-models": ("openclaw", "LITELLM_MASTER_KEY"),
    "stt-gpu": ("stt-provider", "PARAKEET_API_TOKEN"),
    "litellm-password": ("litellm", "LITELLM_MASTER_KEY"),
}


_REQUEST_EXAMPLES = {
    "tei-direct": ("tei-reranker", "TEI_RERANKER_PORT"),
    "tei-litellm": ("tei-reranker", "LITELLM_MASTER_KEY"),
    "tei-health": ("tei-reranker", "TEI_RERANKER_PORT"),
    "searxng-search": ("searxng", "SEARXNG_PORT"),
}


_CREDENTIAL_BAD_TOKENS = {
    "numeric-token": 123,
    "object-token": {"unexpected": "value"},
    "whitespace-token": " \t ",
    "spaced-token": "owned returned jwt",
}
_CREDENTIAL_AUTH_FAILURES = ("auth-failure", "empty-token", "invalid-auth-json", *_CREDENTIAL_BAD_TOKENS)


def documented_credential_cases():
    variants = ["plain", "dollar", "backslash", "double-quote", "space-hash", "exported",
                "relative-file", "absolute-file", "missing-file", "invalid-file",
                "missing-key", "blank-key"]
    cases = [(surface, context, variant) for surface in ["repo", "site", "wiki"]
             for context in _CREDENTIAL_EXAMPLES for variant in variants]
    auth_cases = [(surface, "hermes-airflow", variant) for surface in ["repo", "site", "wiki"]
                  for variant in _CREDENTIAL_AUTH_FAILURES[:3]]
    original = cases + auth_cases + [(surface, "hermes-airflow", variant) for surface in ["repo", "site", "wiki"]
                                    for variant in _CREDENTIAL_BAD_TOKENS]
    return original + _request_cases(variants)


def _credential_document(surface, context, config):
    import html
    import re
    import xml.etree.ElementTree as ET
    import markdown

    source = f"services/{(_CREDENTIAL_EXAMPLES | _REQUEST_EXAMPLES)[context][0]}/README.md"
    text = (ROOT / source).read_text() if surface == "repo" else surface_text(source, surface)
    rendered = markdown.markdown(text, extensions=config.markdown_extensions,
                                 extension_configs=config.mdx_configs)
    rendered = re.sub(r"&([A-Za-z][A-Za-z0-9]+);",
                      lambda match: html.escape(html.unescape(match.group(0))), rendered)
    document = ET.fromstring("<div>" + rendered + "</div>")
    if context == "openclaw-models":
        assert any(len(node.findall("li")) == 5 for node in document.findall(".//ol")
                   if "models.providers.litellm" in "".join(node.itertext()))
    return document


def _credential_select_script(blocks, required, excluded=()):
    selected = [block for block in blocks
                if all(marker in block for marker in required)
                and all(marker not in block for marker in excluded)]
    assert len(selected) == 1, (required, excluded, selected)
    return selected[0]


def _credential_example_script(document, context):
    nodes = document.findall(".//code" if context == "openclaw-models" else ".//pre/code")
    blocks = ["".join(node.itertext()) for node in nodes]
    markers = {
        "tei-direct": (("/rerank", "What is graph-augmented RAG?"), ()),
        "tei-litellm": (("/v1/rerank",), ()),
        "tei-health": (("/health",), ()),
        "searxng-search": (("/search?q=test&format=json",), ()),
        "hermes-airflow": (("example_etl_with_llm/dagRuns",), ()),
        "hermes-diagnostics": (("hermes-init", "/v1/models"), ()),
        "openclaw-cli": (("models.providers.openai.baseUrl",), ()),
        "openclaw-onboard": (("docker exec", "openclaw onboard"), ("models.providers.openai.baseUrl",)),
        "openclaw-models": (("/v1/models", "LITELLM_MASTER_KEY"), ()),
        "stt-gpu": (("file=@sample.wav",), ()),
        "litellm-password": (("LITELLM_MASTER_KEY",), ("curl",)),
    }
    script = _credential_select_script(blocks, *markers[context])
    if context == "openclaw-cli":
        script += "\n" + _credential_select_script(blocks, ("openclaw.mjs health",))
    if context == "stt-gpu":
        script = "\n".join(line for line in script.splitlines()
                           if not line.startswith("./start.sh --stt-provider-source parakeet-container-gpu"))
    return script


def _credential_fixture_values(variant):
    values = {"plain": "sk-owned-plain", "dollar": "sk-owned$literal",
              "backslash": "sk-owned\\literal", "double-quote": 'sk-owned"quote',
              "space-hash": "sk-owned space #1"}
    secret = values.get(variant, "sk-owned-plain")
    env = {name: secret for name in ["AIRFLOW_ADMIN_PASSWORD", "HERMES_API_KEY",
           "LITELLM_MASTER_KEY", "OPENCLAW_GATEWAY_TOKEN", "PARAKEET_API_TOKEN"]}
    env.update(PROJECT_NAME="DocProject", KONG_HTTP_PORT="63000", HERMES_API_PORT="63072",
               LITELLM_PORT="63040", OPENCLAW_GATEWAY_PORT="63076", STT_PROVIDER_PORT="63055")
    if variant in {"relative-file", "absolute-file"}:
        env.update(PROJECT_NAME="SelectedTEAM", KONG_HTTP_PORT="64000", HERMES_API_PORT="64072",
                   LITELLM_PORT="64040", OPENCLAW_GATEWAY_PORT="64076", STT_PROVIDER_PORT="64055")
        env.update({key: "sk-owned-selected" for key in env if key.endswith(("KEY", "TOKEN", "PASSWORD"))})
    return env


def _credential_env_text(values, variant):
    from utils.atomic_write import render_env_assignment

    rows = []
    for key, value in values.items():
        rendered = render_env_assignment(key, value)
        if variant in {"dollar", "backslash", "single-quoted"}:
            rendered = f"'{value}'"
        if variant in {"space-hash", "double-quoted"}:
            rendered = f'"{value}"'
        prefix = "export " if variant == "exported" else ""
        separator = " = " if variant == "exported" else "="
        rows.append(prefix + key + separator + rendered)
    return "\n".join(rows) + "\n"


def _credential_fixture(case, directory):
    surface, context, variant = case
    checkout = directory / "owned checkout"
    checkout.mkdir()
    (checkout / "bootstrapper").symlink_to(ROOT / "bootstrapper", target_is_directory=True)
    expected = _credential_fixture_values(variant)
    actual = dict(expected)
    key = _CREDENTIAL_EXAMPLES[context][1]
    if variant == "missing-key":
        actual.pop(key)
    if variant == "blank-key":
        actual[key] = ""
    valid = variant not in {"missing-file", "invalid-file", "missing-key", "blank-key"}
    if context == "openclaw-onboard" and variant in {"missing-key", "blank-key"}:
        expected["PROJECT_NAME"] = "atlas"
        valid = True
    selected = checkout / ".env"
    custom = variant in {"relative-file", "absolute-file"}
    if custom:
        (checkout / ".env").write_text(_credential_env_text(_credential_fixture_values("plain"), "plain"))
        selected = checkout / "selected active file.env"
    if variant != "missing-file":
        selected.write_text(_credential_env_text(actual, variant)
                            + ("BROKEN: invalid\n" if variant == "invalid-file" else ""))
    selector = str(selected) if variant == "absolute-file" else selected.name
    return checkout, expected, valid, selector if custom else None


def _credential_boundary_tools(directory):
    import json
    import shutil
    import sys

    tools = directory / "owned-tools"
    tools.mkdir()
    recorder = 'token_values = ' + repr(_CREDENTIAL_BAD_TOKENS) + '\n' + '''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
kind=Path(sys.argv[0]).name
body=sys.stdin.read() if "--data-binary" in args and "@-" in args else ""
with Path(os.environ["ATLAS_DOC_CAPTURE"]).open("a") as stream:
    stream.write(json.dumps({"kind":kind,"args":args,"stdin":body})+"\\n")
if kind=="docker":
    sys.exit(0)
url=next((arg for arg in args if arg.startswith("http://")),"")
mode=os.environ["ATLAS_DOC_AUTH_MODE"]
if url.endswith("/auth/token"):
    if mode=="auth-failure": sys.exit(22)
    if mode=="empty-token": print("{}");sys.exit(0)
    if mode=="invalid-auth-json": print("invalid-json");sys.exit(0)
    if mode in token_values: print(json.dumps({"access_token":token_values[mode]}));sys.exit(0)
    print(json.dumps({"access_token":"owned-returned-jwt"}))
elif url.endswith("/v1/models"): print(json.dumps({"data":[{"id":"hermes-agent"}]}))
else: print(json.dumps({"owned":"response"}))
'''
    for name in ["curl", "docker"]:
        p = tools / name
        p.write_text("#!" + sys.executable + "\n" + recorder)
        p.chmod(0o700)
    for name in ["uv", "jq", "grep", "cut", "sed"]:
        path = shutil.which(name)
        assert path is not None, f"Required real executable not found: {name}"
        (tools / name).symlink_to(Path(path).resolve(strict=True))
    return tools


def _credential_shell_environment(directory, tools, selector, variant):
    import sys

    environment = {"PATH": str(tools), "VIRTUAL_ENV": sys.prefix,
                   "UV_PROJECT_ENVIRONMENT": sys.prefix, "UV_PYTHON": sys.executable,
                   "UV_NO_SYNC": "1", "UV_FROZEN": "1", "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never",
                   "UV_CACHE_DIR": str(directory / "uv-cache"),
                   "PYTHONPYCACHEPREFIX": str(directory / "pycache"),
                   "ATLAS_DOC_CAPTURE": str(directory / "calls.jsonl"),
                   "ATLAS_DOC_AUTH_MODE": variant, "PROJECT_NAME": "wrong-shell-project",
                   "LITELLM_MASTER_KEY": "sk-wrong-shell", "HERMES_API_KEY": "wrong-shell-key"}
    environment.update(TEI_RERANKER_PORT="wrong-shell-tei", SEARXNG_PORT="wrong-shell-search",
                       LITELLM_PORT="wrong-shell-litellm")
    if selector is not None:
        environment["ATLAS_ENV_FILE"] = selector
    return environment


def _credential_option_values(args, flag):
    return [args[index + 1] for index, value in enumerate(args) if value == flag]


def _credential_curl_observation(row):
    import json

    args = row["args"]
    headers = _credential_option_values(args, "-H")
    body = row["stdin"]
    if "-d" in args:
        body = args[args.index("-d") + 1]
    try:
        data = json.loads(body) if body else None
    except json.JSONDecodeError:
        data = {"invalid_json": body}
    return {"url": next((a for a in args if a.startswith("http://")), ""),
            "headers": headers, "data": data,
            "method": args[args.index("-X") + 1] if "-X" in args else ("POST" if "-d" in args else "GET"),
            "forms": _credential_option_values(args, "-F")}


def _credential_request(url, key=None, data=None, method="GET"):
    headers = ([f"Authorization: Bearer {key}"] if key is not None else [])
    if data is not None:
        headers.append("Content-Type: application/json")
    return {"url": url, "headers": headers, "data": data, "method": method, "forms": []}


def _credential_expected_requests(context, values, variant):
    request = _credential_request
    if context in _REQUEST_EXAMPLES:
        return _request_expected(context, values)
    if context == "hermes-airflow":
        base = "http://airflow.localhost:" + values["KONG_HTTP_PORT"]
        rows = [request(base + "/auth/token", data={"username": "admin",
                "password": values["AIRFLOW_ADMIN_PASSWORD"]}, method="POST")]
        if variant not in _CREDENTIAL_AUTH_FAILURES:
            rows.append(request(base + "/api/v2/dags/example_etl_with_llm/dagRuns",
                                "owned-returned-jwt", {"logical_date": None, "conf": {}}, "POST"))
        return rows
    if context == "hermes-diagnostics":
        return [request("http://localhost:" + values["HERMES_API_PORT"] + "/v1/models", values["HERMES_API_KEY"]),
                request("http://localhost:" + values["LITELLM_PORT"] + "/v1/models", values["LITELLM_MASTER_KEY"])]
    if context == "openclaw-models":
        return [request("http://localhost:" + values["LITELLM_PORT"] + "/v1/models", values["LITELLM_MASTER_KEY"])]
    if context == "openclaw-cli":
        return [request("http://localhost:" + values["OPENCLAW_GATEWAY_PORT"] + "/healthz")]
    if context == "stt-gpu":
        row = request("http://localhost:" + values["STT_PROVIDER_PORT"] + "/v1/audio/transcriptions",
                      values["PARAKEET_API_TOKEN"], method="POST")
        row["forms"] = ["file=@sample.wav", "model=whisper-1"]
        return [row]
    return []


def _credential_expected_docker(context, values):
    if context == "searxng-search":
        return [["compose", "ps", "searxng"], ["compose", "logs", "-f", "searxng"]]
    name = values["PROJECT_NAME"] + "-openclaw-gateway"
    if context == "openclaw-onboard":
        return [["exec", "-it", name, "openclaw", "onboard"]]
    if context == "hermes-diagnostics":
        return [["compose", "ps", "hermes", "hermes-init"], ["compose", "logs", "-f", "hermes"],
                ["compose", "logs", "hermes-init"], ["compose", "exec", "hermes", "cat", "/opt/data/config.yaml"]]
    if context != "openclaw-cli":
        return []
    return [["exec", "-it", name, "openclaw", "onboard"],
            ["exec", "-it", name, "openclaw", "config", "set", "models.providers.openai.baseUrl", "http://litellm:4000/v1"],
            ["exec", "-it", name, "sh", "-c", 'openclaw config set models.providers.openai.apiKey "$LITELLM_API_KEY"'],
            ["exec", "-it", name, "openclaw", "doctor"],
            ["exec", "-it", name, "openclaw", "gateway", "probe"],
            ["exec", name, "sh", "-c", 'node openclaw.mjs health --token "$OPENCLAW_GATEWAY_TOKEN"']]


def _credential_result(result, rows, context):
    printed = None
    if context in {"openclaw-cli", "litellm-password"}:
        printed = result.stdout.splitlines()[0] if result.stdout else None
    observed = {"success": result.returncode == 0,
            "requests": [_credential_curl_observation(row) for row in rows if row["kind"] == "curl"],
            "docker": [row["args"] for row in rows if row["kind"] == "docker"], "printed": printed}
    if context in _REQUEST_EXAMPLES:
        observed["argv"] = [row["args"] for row in rows if row["kind"] == "curl"]
    return observed


def probe_documented_credential_case(case, directory, config):
    import json
    import subprocess

    surface, context, variant = case
    document = _credential_document(surface, context, config)
    script = _credential_example_script(document, context)
    fixture = dict.fromkeys(_REQUEST_EXAMPLES, _request_fixture).get(context, _credential_fixture)
    checkout, values, valid, selector = fixture(case, directory)
    tools = _credential_boundary_tools(directory)
    environment = _credential_shell_environment(directory, tools, selector, variant)
    result = subprocess.run(["/bin/bash", "-c", script], cwd=checkout, env=environment,
                            input="", capture_output=True, text=True, timeout=30)
    log = directory / "calls.jsonl"
    rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    failure = variant in _CREDENTIAL_AUTH_FAILURES
    expected = {"success": valid and not failure,
                "requests": _credential_expected_requests(context, values, variant) if valid else [],
                "docker": _credential_expected_docker(context, values) if valid else [], "printed": None}
    if context in _REQUEST_EXAMPLES:
        expected["argv"] = _request_argv(context, values) if valid else []
    if valid and context in {"openclaw-cli", "litellm-password"}:
        expected["printed"] = values[_CREDENTIAL_EXAMPLES[context][1]]
    (directory / "extracted-fence.txt").write_text(script)
    (directory / "result.json").write_text(json.dumps({"case": case, "script": script,
        "environment": environment, "values": values, "valid": valid, "selector": selector,
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        "calls": rows, "actual": _credential_result(result, rows, context), "expected": expected}, indent=2) + "\n")
    return _credential_result(result, rows, context), expected


def _hermes_oauth_guide_section(document, marker):
    sections = []
    for node in document:
        if node.tag == "h2" or not sections:
            sections.append([])
        sections[-1].append(node)
    matches = [section for section in sections
               if any(marker in "".join(node.itertext()) for node in section)]
    assert len(matches) == 1, "missing or ambiguous dashboard OAuth guidance"
    return " ".join(" ".join(node.itertext()) for node in matches[0])


def _hermes_oauth_guidance_claim(document, context):
    key = "HERMES_DASHBOARD_INSECURE"
    if context == "guide":
        return _hermes_oauth_guide_section(document, key + "=false")
    rows = [row for row in document.iter("tr") if row.find("td") is not None
            and "".join(row.find("td").itertext()).strip() == key]
    assert len(rows) == 1, "missing or ambiguous dashboard setting reference"
    return " ".join(rows[0].findall("td")[-1].itertext())


def documented_hermes_oauth_prerequisites(document, context):
    """Check setup and fail-closed meaning without fixing wording or paragraph layout."""
    import re

    claim = _hermes_oauth_guidance_claim(document, context)
    patterns = [
        r"\b(?:requires?|needs?)\b[^.!?]*\bprovider\b[^.!?]*\bprovisioned\b[^.!?]*\bclient\b",
        r"\bAtlas\b[^.!?]*\bdoes not\b[^.!?]*\bprovision\b[^.!?]*\bpass through\b",
        r"\bfalse\b[^.!?]*\b(?:no|without)\b[^.!?]*\bprovider\b[^.!?]*\brefus\w*\b[^.!?]*\bstartup\b",
    ]
    facts = [bool(re.search(pattern, claim, re.I)) for pattern in patterns]
    facts.append(all(key in claim for key in ["HERMES_DASHBOARD_OAUTH_CLIENT_ID", "dashboard.oauth.client_id"]))
    return facts, claim


def _redis_recovery_script(surface, config):
    from tests.test_three_surface_build import _candidate_document

    source = "services/redis/README.md"
    text = (ROOT / source).read_text() if surface == "repo" else surface_text(source, surface)
    document = _candidate_document(text, config)
    blocks = ["".join(code.itertext()) for code in document.findall(".//pre/code")]
    extracted = _credential_select_script(blocks, ("redis-check-aof --fix",))
    # These exact lifecycle lines are deliberately outside the owned recorder boundary.
    script = "\n".join(line for line in extracted.splitlines() if line not in {"./stop.sh", "./start.sh"})
    return extracted, script.replace("VOLUME=<project>-redis-data", "VOLUME=owned-redis-data")


def _redis_recovery_fixture(variant, directory):
    checkout = directory / "owned checkout"
    checkout.mkdir()
    (checkout / "bootstrapper").symlink_to(ROOT / "bootstrapper", target_is_directory=True)
    image = "redis:7.2.14-alpine" if variant == "plain" else "redis:owned-" + variant
    content = "REDIS_IMAGE=" + image + "\n"
    quoted = {"single-quoted": "'", "double-quoted": '"'}
    if variant in quoted:
        content = "REDIS_IMAGE=" + quoted[variant] + image + quoted[variant] + "\n"
    if variant == "exported":
        content = "export REDIS_IMAGE=" + image + "\n"
    content = {"missing-image": "OTHER=owned\n", "empty-image": "REDIS_IMAGE=\n",
               "whitespace-image": "REDIS_IMAGE=' \t '"}.get(variant, content)
    if variant.startswith("invalid-"):
        content += "BROKEN: invalid\n"
    custom = variant in {"relative-file", "absolute-file", "missing-selected", "invalid-selected"}
    selected = checkout / ("selected active file.env" if custom else ".env")
    if custom:
        (checkout / ".env").write_text("REDIS_IMAGE=redis:inactive-default\n")
    if variant not in {"missing-default", "missing-selected"}:
        selected.write_text(content)
    selector = str(selected) if variant == "absolute-file" else selected.name
    valid = variant in {"plain", "single-quoted", "double-quoted", "exported", "relative-file",
                        "absolute-file", "backup-failure"}
    return checkout, {"image": image, "selector": selector if custom else None, "valid": valid}


def _redis_recovery_tools(directory):
    import shutil
    import sys

    tools = directory / "owned-tools"
    tools.mkdir()
    recorder = """import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with Path(os.environ["ATLAS_DOC_CAPTURE"]).open("a") as stream:
    stream.write(json.dumps(args)+"\\n")
print("owned docker boundary: "+("backup" if "tar" in args else "fix"))
if "tar" in args and os.environ["ATLAS_DOC_AUTH_MODE"]=="backup-failure": sys.exit(37)
"""
    docker = tools / "docker"
    docker.write_text("#!" + sys.executable + "\n" + recorder)
    docker.chmod(0o700)
    for name in ["uv", "grep", "cut"]:
        executable = shutil.which(name)
        assert executable is not None, f"Required real executable not found: {name}"
        (tools / name).symlink_to(Path(executable).resolve(strict=True))
    return tools


def _run_redis_recovery(script, directory, variant, selected):
    import json
    import subprocess

    checkout, settings = selected
    tools = _redis_recovery_tools(directory)
    environment = _credential_shell_environment(directory, tools, settings["selector"], variant)
    environment["REDIS_IMAGE"] = "redis:wrong-inherited-shell"
    path = directory / "recovery.sh"
    path.write_text(script)
    result = subprocess.run(["/bin/bash", str(path)], cwd=checkout, env=environment,
                            capture_output=True, text=True, timeout=20)
    capture = directory / "calls.jsonl"
    calls = [json.loads(line) for line in capture.read_text().splitlines()] if capture.exists() else []
    observation = {"returncode": result.returncode, "stdout": result.stdout,
                   "stderr": result.stderr, "calls": calls}
    (directory / "result.json").write_text(json.dumps(observation, indent=2) + "\n")
    return observation


def _redis_recovery_expected(checkout, settings, variant):
    image = settings["image"]
    backup = ["run", "--rm", "-v", "owned-redis-data:/data", "-v", str(checkout) + ":/backup",
              image, "tar", "czf", "/backup/redis-aof-backup.tgz", "-C", "/data", "appendonlydir"]
    fix = ["run", "--rm", "-it", "-v", "owned-redis-data:/data", "-w", "/data/appendonlydir",
           image, "redis-check-aof", "--fix", "appendonly.aof.manifest"]
    if not settings["valid"]:
        return []
    return [backup] if variant == "backup-failure" else [backup, fix]


def probe_documented_redis_recovery(case, directory, config):
    """Execute actual rendered recovery fences using synthetic files and an owned Docker recorder."""
    surface, variant = case
    extracted, script = _redis_recovery_script(surface, config)
    (directory / "extracted-fence.txt").write_text(extracted)
    selected = _redis_recovery_fixture(variant, directory)
    observed = _run_redis_recovery(script, directory, variant, selected)
    checkout, settings = selected
    expected = _redis_recovery_expected(checkout, settings, variant)
    success = settings["valid"] and variant != "backup-failure"
    assert observed["calls"] == expected, (case, observed, expected)
    assert (observed["returncode"] == 0) == success, (case, observed)



def _request_fixture(case, directory):
    surface, context, variant = case
    checkout = directory / "owned checkout"
    checkout.mkdir()
    (checkout / "bootstrapper").symlink_to(ROOT / "bootstrapper", target_is_directory=True)
    expected = _credential_fixture_values(variant)
    offset = {"relative-file": "642", "absolute-file": "642"}.get(variant, "631")
    expected.update(TEI_RERANKER_PORT=offset + "41", SEARXNG_PORT=offset + "56", LITELLM_PORT=offset + "40")
    actual = dict(expected)
    key = "LITELLM_PORT" if variant.endswith("-port") else _REQUEST_EXAMPLES[context][1]
    if variant in {"missing-key", "missing-port"}:
        actual.pop(key)
    blanks = {"blank-key": "", "blank-port": "", "whitespace-key": " \t "}
    if variant in blanks:
        actual[key] = blanks[variant]
    custom = variant in {"relative-file", "absolute-file", "missing-selected", "invalid-selected"}
    selected = checkout / ("selected active file.env" if custom else ".env")
    if custom:
        (checkout / ".env").write_text(_credential_env_text(_credential_fixture_values("plain"), "plain")
                                      + "TEI_RERANKER_PORT=62241\nSEARXNG_PORT=62256\n")
    if variant not in {"missing-file", "missing-selected"}:
        selected.write_text(_credential_env_text(actual, variant)
                            + ("BROKEN: invalid\n" if variant.startswith("invalid-") else ""))
    selector = str(selected) if variant == "absolute-file" else selected.name
    valid = variant not in {"missing-file", "invalid-file", "missing-key", "blank-key", "whitespace-key",
                            "missing-port", "blank-port", "missing-selected", "invalid-selected"}
    return checkout, expected, valid, selector if custom else None


def _request_expected(context, values):
    request = _credential_request
    if context == "tei-direct":
        return [request("http://localhost:" + values["TEI_RERANKER_PORT"] + "/rerank", data={
            "query": "What is graph-augmented RAG?", "texts": [
                "LightRAG combines knowledge graphs with dense vector retrieval.", "GraphQL is a query language.",
                "Reranking improves RAG quality by ordering retrieved passages."]}, method="POST")]
    if context == "tei-litellm":
        return [request("http://localhost:" + values["LITELLM_PORT"] + "/v1/rerank", values["LITELLM_MASTER_KEY"],
                        {"model": "tei-rerank", "query": "…", "documents": ["…", "…"]}, "POST")]
    if context == "tei-health":
        return [request("http://localhost:" + values["TEI_RERANKER_PORT"] + "/health")]
    return [request("http://localhost:" + values["SEARXNG_PORT"] + "/search?q=test&format=json")]



def _request_cases(variants):
    ports = ["plain", "single-quoted", "double-quoted", "exported", "relative-file", "absolute-file",
             "missing-file", "invalid-file", "missing-key", "blank-key", "whitespace-key"]
    return [(surface, context, variant) for surface in ["repo", "site", "wiki"]
                       for context in _REQUEST_EXAMPLES
                       for variant in (variants + ["single-quoted", "double-quoted", "whitespace-key",
                                                   "missing-port", "blank-port"] if context == "tei-litellm" else ports)]



def _request_argv(context, values):
    row = _request_expected(context, values)[0]
    args = ["-fs" if context == "tei-health" else "-s", row["url"]]
    for header in row["headers"]:
        args += ["-H", header]
    bodies = {
        "tei-direct": '{\n    "query": "What is graph-augmented RAG?",\n    "texts": [\n'
                      '      "LightRAG combines knowledge graphs with dense vector retrieval.",\n'
                      '      "GraphQL is a query language.",\n'
                      '      "Reranking improves RAG quality by ordering retrieved passages."\n    ]\n  }',
        "tei-litellm": '{"model":"tei-rerank","query":"…","documents":["…","…"]}',
    }
    if context in bodies:
        args += ["-d", bodies[context]]
    return [args]
