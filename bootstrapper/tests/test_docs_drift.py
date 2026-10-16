"""CI gate: committed README deps sections + architecture artifacts must
match what `python -m docs.regen --all --check` would produce.

Parallels test_env_example_consistency. Fails if any manifest change leaves
generated artifacts stale.
"""

from __future__ import annotations

from dataclasses import replace
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _assert_text_contract(text, *, contains=(), excludes=()):
    missing = tuple(fragment for fragment in contains if fragment not in text)
    unexpected = tuple(fragment for fragment in excludes if fragment in text)
    assert (missing, unexpected) == ((), ())


_REVIEWED_README_CONTRACTS = {
    "lightrag": {
        "contains": ("does not select file-backed storage automatically",),
        "excludes": (
            "transparently falls back to in-process file backends",
            "In-process fallback (when source disabled)",
        ),
    },
    "supabase": {
        "contains": (
            "DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` protect the Kong",
            "Studio is reached only through that route",
        )
    },
    "iceberg-rest": {
        "contains": ("host-published API runs without Atlas authentication",),
        "excludes": ("intentionally internal-only",),
    },
    "redis": {
        "contains": ("defaults to `volatile-lru`",),
        "excludes": ("Currently unset — Redis runs with the default `noeviction`",),
    },
    "vllm-metal": {
        "contains": (
            "warning floor",
            "an unreadable Python version warns without blocking",
            "stop the existing process before restarting Atlas",
        ),
        "excludes": ("Minimum unified memory the preflight requires",),
    },
    "n8n": {
        "contains": (
            "legacy bundled POST `/research` fixture has no webhook authentication",
            "Only `BACKEND_N8N_API_TOKEN` is route-scoped",
            "LiteLLM master key and provider-level credentials",
        )
    },
    "mcp-servers": {
        "contains": (
            "not the RLS-bypassing `supabase_admin` owner",
            "no schema, table, or column allowlist or output redaction",
            "privileged SQL functions and Neo4j procedures can still cause",
            "backend-network containers can call the unauthenticated",
        )
    },
    "airflow": {
        "contains": (
            "Only trusted authors may supply DAGs",
            "LocalExecutor does not sandbox DAG code",
        )
    },
    "jupyterhub": {
        "contains": (
            "MCP_SERVERS_URL` only when `MCP_SERVERS_SOURCE=container`",
            "leaves it empty when the curated package is disabled",
            "including `MCP_SERVERS_URL`",
            "bypasses Kong authentication",
            "receives high-privilege database and service credentials",
        ),
        "excludes": (
            "receives powerful database and service credentials",
            "MCP_SERVERS_URL` is not injected by Compose",
            "reports the MCP service as disabled",
            "optional gaps such as the current MCP endpoint remain explicit",
        ),
    },
    "weaviate": {
        "contains": ("`POST /api/rag/ingestions`", "`vectorizer=none`"),
        "excludes": (
            "today nothing routes that output into Weaviate",
            "n8n/backend re-implement chunking ad hoc",
        ),
    },
}


def test_capability_readme_exception_and_aggregate_sets_are_closed():
    from docs.capabilities_resolver import CAPABILITY_SECTION_EXCEPTIONS

    manifest_folders = {
        folder.name
        for folder in (REPO_ROOT / "services").iterdir()
        if (folder / "service.yml").is_file()
    }
    readme_folders = {
        folder.name
        for folder in (REPO_ROOT / "services").iterdir()
        if (folder / "README.md").is_file()
    }

    assert readme_folders - manifest_folders == {
        "doc-processor",
        "stt-provider",
        "multi2vec-clip",
    }
    assert CAPABILITY_SECTION_EXCEPTIONS == frozenset({"multi2vec-clip"})


def test_generated_capability_output_changes_when_a_contract_changes():
    from docs.capabilities_resolver import resolve_capability_rows
    from docs.capabilities_section_writer import render_capabilities_section
    from services.manifests import load_manifests

    manifests = load_manifests(REPO_ROOT / "services")
    redis = next(manifest for manifest in manifests if manifest.name == "redis")
    original = render_capabilities_section(
        resolve_capability_rows("redis", manifests),
        position=10,
        aggregate=False,
    )
    changed_capability = replace(
        redis.capabilities[0],
        note=redis.capabilities[0].note + " Contract mutation sentinel.",
    )
    changed_manifest = replace(
        redis,
        capabilities=[changed_capability, *redis.capabilities[1:]],
    )
    changed = render_capabilities_section(
        resolve_capability_rows(
            "redis",
            [changed_manifest, *(m for m in manifests if m.name != "redis")],
        ),
        position=10,
        aggregate=False,
    )

    assert changed != original
    assert "Contract mutation sentinel." in changed


def test_canonical_readmes_reconcile_reviewed_capability_boundaries():
    readmes = {
        name: (REPO_ROOT / f"services/{name}/README.md").read_text(encoding="utf-8")
        for name in _REVIEWED_README_CONTRACTS
    }
    for name, contract in _REVIEWED_README_CONTRACTS.items():
        _assert_text_contract(readmes[name], **contract)


def test_task4_reviewed_contract_wording_is_published():
    required_rows = {
        "chatterbox": "GPU voice-cloning text-to-speech",
        "cloud-providers": "Live cloud completion validation",
        "comfyui": "Authenticated ComfyUI ingress",
        "docling-lightrag-adapter": "Docling credential isolation",
        "fal": "LiteLLM text-to-image passthrough",
        "tei-reranker": "Arbitrary reranker model portability",
        "vllm-metal": "Single-model host lifecycle",
    }

    for service, capability in required_rows.items():
        readme = (REPO_ROOT / f"services/{service}/README.md").read_text(
            encoding="utf-8"
        )
        assert "Capabilities & limitations" in readme, service
        assert capability in readme, service

    env_reference = (REPO_ROOT / "docs/reference/env-vars.md").read_text(
        encoding="utf-8"
    )
    assert "Minimum unified memory (GB) the preflight requires" not in env_reference
    assert "Unified-memory warning floor (GB) checked before lifecycle work" in env_reference


def test_no_drift_between_manifests_and_committed_artifacts():
    cmd = [sys.executable, "-m", "docs.regen", "--all", "--check"]
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "bootstrapper")}
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, env=env)
    if result.returncode == 2:
        pytest.fail(
            "Drift between committed docs and current manifests. Run:\n"
            "  python -m bootstrapper.docs.regen --all\n"
            "and commit the result.\n\n" + result.stdout
        )
    assert result.returncode == 0, result.stdout + result.stderr


# Import the existing configured-render fixture; keep all existing tests intact.
from tests.test_three_surface_build import env_reference_config

_LEGACY_SERVICE_TOPICS = {'airflow': (7, 9, 'Capabilities & limitations'),
 'asset-baker': (6, 8, 'Capabilities & limitations'),
 'asset-worker': (6, 8, 'Capabilities & limitations'),
 'backend': (6, 8, 'Capabilities & limitations'),
 'backup': (5, 7, 'Capabilities & limitations'),
 'blender-mcp': (5, 9, 'Capabilities & limitations'),
 'celery': (6, 7, 'Capabilities & limitations'),
 'chatterbox': (2, 3, 'Capabilities & limitations'),
 'cloud-providers': (11, 12, 'Capabilities & limitations'),
 'cloudflared': (5, 6, 'Capabilities & limitations'),
 'comfyui': (5, 12, 'Capabilities & limitations'),
 'crawl4ai': (5, 8, 'Capabilities & limitations'),
 'doc-processor': (13, 15, 'Capabilities & limitations'),
 'docling': (2, 3, 'Capabilities & limitations'),
 'docling-lightrag-adapter': (5, 7, 'Capabilities & limitations'),
 'fal': (5, 6, 'Capabilities & limitations'),
 'globals': (11, 12, 'Capabilities & limitations'),
 'grafana': (5, 7, 'Capabilities & limitations'),
 'hermes': (10, 12, 'Capabilities & limitations'),
 'iceberg-rest': (5, 7, 'Capabilities & limitations'),
 'jenkins': (6, 8, 'Capabilities & limitations'),
 'jupyterhub': (15, 17, 'Capabilities & limitations'),
 'kong': (13, 15, 'Capabilities & limitations'),
 'label-studio': (5, 7, 'Capabilities & limitations'),
 'langfuse': (5, 7, 'Capabilities & limitations'),
 'lightrag': (5, 9, 'Capabilities & limitations'),
 'litellm': (14, 15, 'Capabilities & limitations'),
 'llm-graph-builder': (10, 11, 'Capabilities & limitations'),
 'local-deep-researcher': (5, 9, 'Capabilities & limitations'),
 'loki': (5, 7, 'Capabilities & limitations'),
 'mcp-servers': (5, 8, 'Capabilities & limitations'),
 'minio': (10, 12, 'Capabilities & limitations'),
 'mlflow': (5, 7, 'Capabilities & limitations'),
 'multi2vec-clip': (5, 8, 'Performance notes'),
 'n8n': (6, 8, 'Capabilities & limitations'),
 'neo4j': (13, 15, 'Capabilities & limitations'),
 'ollama': (6, 8, 'Capabilities & limitations'),
 'open-webui': (5, 7, 'Capabilities & limitations'),
 'openclaw': (12, 14, 'Capabilities & limitations'),
 'otel-collector': (5, 7, 'Capabilities & limitations'),
 'parakeet': (2, 3, 'Capabilities & limitations'),
 'prometheus': (5, 7, 'Capabilities & limitations'),
 'ray': (5, 7, 'Capabilities & limitations'),
 'redis': (6, 12, 'Capabilities & limitations'),
 'redpanda': (5, 8, 'Capabilities & limitations'),
 'searxng': (5, 11, 'Capabilities & limitations'),
 'spark': (5, 7, 'Capabilities & limitations'),
 'speaches': (2, 3, 'Capabilities & limitations'),
 'stt-provider': (9, 11, 'Capabilities & limitations'),
 'supabase': (9, 11, 'Capabilities & limitations'),
 'supavisor': (5, 8, 'Capabilities & limitations'),
 'tei-reranker': (5, 8, 'Capabilities & limitations'),
 'tempo': (5, 7, 'Capabilities & limitations'),
 'tika': (6, 8, 'Capabilities & limitations'),
 'trino': (5, 7, 'Capabilities & limitations'),
 'trueforge': (5, 7, 'Capabilities & limitations'),
 'tts-provider': (9, 11, 'Capabilities & limitations'),
 'verba': (5, 8, 'Capabilities & limitations'),
 'vllm-metal': (6, 8, 'Capabilities & limitations'),
 'weaviate': (5, 7, 'Capabilities & limitations'),
 'zeppelin': (6, 8, 'Capabilities & limitations')}


def _original_service_topic_headings(service):
    dependency, current, topic = _LEGACY_SERVICE_TOPICS[service]
    labels = ["Current — Upstream (this service calls)",
              "Current — Downstream (services that call this)", "Architecture diagram",
              "Future — Missing pair integrations", "Future — Candidate new services",
              "Future — Unused features in this service"]
    return [(2, f"{current}. {topic}"), (2, f"{dependency}. Dependencies & Integrations"),
            *[(3, f"{dependency}.{index}. {label}") for index, label in enumerate(labels, 1)]]


def _service_document_ids(document):
    return [node.attrib["id"] for node in document.iter() if "id" in node.attrib]


def _service_heading_text(node):
    return (node.text or "") + "".join(
        "".join(child.itertext()) + (child.tail or "")
        for child in node if child.get("class") != "headerlink"
    )


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
@pytest.mark.parametrize("service", sorted(p.parent.name for p in (REPO_ROOT / "services").glob("*/README.md")))
def test_current_service_capabilities_precede_future(service, surface, env_reference_config):
    import re
    from tests.test_three_surface_build import _candidate_document
    from tests.three_surface_test_utils import surface_text

    source = f"services/{service}/README.md"
    text = (REPO_ROOT / source).read_text() if surface == "repo" else surface_text(source, surface)
    document = _candidate_document(text, env_reference_config)
    nodes = list(document.iter())
    current = [nodes.index(node) for node in document.iter("h2") if re.fullmatch(r"\d+\. Capabilities & limitations", _service_heading_text(node).strip())]
    future = [nodes.index(node) for node in document.iter("h3") if re.match(r"\d+\.[456]\. Future", _service_heading_text(node).strip())]
    assert len(current) == 1 and len(future) == 3, (service, surface, current, future)
    assert current[0] < min(future), (service, surface)


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
@pytest.mark.parametrize("service", sorted(_LEGACY_SERVICE_TOPICS))
def test_original_service_topic_fragments_remain_usable(service, surface, env_reference_config, tmp_path):
    from scripts.check_doc_links import _heading_anchors, _slugify
    from tests.test_three_surface_build import _candidate_document
    from tests.three_surface_test_utils import surface_text

    headings = _original_service_topic_headings(service)
    previous = "\n\n".join("#" * level + " " + title for level, title in headings)
    expected = set(_service_document_ids(_candidate_document(previous, env_reference_config)))
    source = f"services/{service}/README.md"
    text = (REPO_ROOT / source).read_text() if surface == "repo" else surface_text(source, surface)
    document = _candidate_document(text, env_reference_config)
    rendered_ids = _service_document_ids(document)
    actual = set(rendered_ids)
    if surface != "site":
        host_source = tmp_path / "reader.md"
        host_source.write_text(text, encoding="utf-8")
        actual = _heading_anchors(host_source)
        expected = {_slugify(title) for level, title in headings}
    assert expected <= actual, (service, surface, sorted(expected - actual))
    assert len(rendered_ids) == len(set(rendered_ids)), (service, surface)


def _alias_owner_fixture():
    from docs.capabilities_resolver import resolve_capability_rows, resolve_support_lines
    from docs.capabilities_section_writer import render_capabilities_section
    from docs.deps_resolver import build_doc_graph
    from docs.deps_section_writer import render_section
    from services.manifests import load_manifests

    manifests = load_manifests(REPO_ROOT / "services")
    graph = build_doc_graph("redis", REPO_ROOT / "services")
    return ("# Service\n\n" + render_capabilities_section(
        resolve_capability_rows("redis", manifests), position=2, aggregate=False,
        support=resolve_support_lines("redis", manifests))
            + "\n" + render_section(graph, position=3))


@pytest.mark.parametrize("owner", ["capability", "dependencies"])
def test_generated_topic_aliases_survive_regeneration(owner, tmp_path):
    import re
    from docs.regen import _extract_future_blocks, _process, _slice_deps_section

    source = _alias_owner_fixture()
    headings = re.findall(r"^#{2,3} .+$", source, re.MULTILINE)
    selected = headings[:1] if owner == "capability" else headings[1:]
    for index, heading in enumerate(selected):
        aliases = f'<a id="old-topic-{index}"></a>\n<a name=\'old-name-{index}\'></a>'
        source = source.replace(heading + "\n", heading + "\n\n" + aliases + "\n", 1)
    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(source)
    start, end = _slice_deps_section(source)
    future = _extract_future_blocks(source[start:end])
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    updated = path.read_text()
    for index, heading in enumerate(selected):
        aliases = f'<a id="old-topic-{index}"></a>\n<a name=\'old-name-{index}\'></a>'
        assert heading + "\n\n" + aliases in updated
        assert updated.count(f'id="old-topic-{index}"') == 1
    start, end = _slice_deps_section(updated)
    assert _extract_future_blocks(updated[start:end]) == future
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    assert path.read_text() == updated


@pytest.mark.parametrize("owner", ["capability", "dependencies"])
@pytest.mark.parametrize("opener,closer", [("```markdown", "```"), ("- item\n  ```markdown", "  ```")])
def test_fenced_topic_aliases_are_not_promoted(owner, opener, closer, tmp_path):
    from docs.regen import _process

    heading = "## 2. Capabilities & limitations" if owner == "capability" else "## 3. Dependencies & Integrations"
    indent = "  " if opener.startswith("-") else ""
    example = opener + "\n" + indent + heading + '\n' + indent + '<a id="fenced-topic"></a>\n' + closer + "\n\n"
    source = "# Service\n\n" + example + _alias_owner_fixture().split("\n\n", 1)[1]
    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(source)
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    updated = path.read_text()
    assert updated.startswith("# Service\n\n" + example)
    assert updated.count('id="fenced-topic"') == 1


def test_unaliased_generation_remains_exact(tmp_path):
    from docs.regen import _process

    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(_alias_owner_fixture())
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    expected = path.read_text()
    assert expected == _alias_owner_fixture()
    assert '<a ' not in expected
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    assert path.read_text() == expected


def _assert_single_alias_topic(document, target, heading):
    nodes = list(document.iter())
    aliases = [node for node in nodes if target in (node.get("id"), node.get("name"))]
    assert len(aliases) == 1, (target, len(aliases))
    preceding = [node for node in nodes[:nodes.index(aliases[0])] if node.tag in ("h2", "h3")]
    assert _service_heading_text(preceding[-1]).strip() == heading.lstrip("# ")


@pytest.mark.parametrize("case", [
    (heading, f"  <a {attribute}={quote}indented-topic{quote}></a>")
    for heading in ["## 2. Capabilities & limitations",
                    "### 3.1. Current — Upstream (this service calls)",
                    "### 3.4. Future — Missing pair integrations",
                    "### 3.5. Future — Candidate new services",
                    "### 3.6. Future — Unused features in this service"]
    for attribute, quote in [("id", '"'), ("id", "'"), ("name", '"'), ("name", "'")]
])
def test_indented_generated_alias_identity(case, tmp_path, env_reference_config):
    from docs.regen import _process
    from tests.test_three_surface_build import _candidate_document

    heading, alias = case
    source = _alias_owner_fixture().replace(heading + "\n", heading + "\n\n" + alias + "\n", 1)
    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(source)
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    updated = path.read_text()
    _assert_single_alias_topic(_candidate_document(updated, env_reference_config), "indented-topic", heading)
    assert updated.count("indented-topic") == 1
    if "Future" not in heading:
        assert heading + "\n\n" + alias in updated
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    assert path.read_text() == updated


@pytest.mark.parametrize("heading", [
    "## 2. Capabilities & limitations",
    "### 3.1. Current — Upstream (this service calls)",
    "### 3.4. Future — Missing pair integrations",
])
def test_owned_span_fenced_alias_is_not_promoted(heading, tmp_path, env_reference_config):
    from docs.regen import _process
    from tests.test_three_surface_build import _candidate_document

    example = '```markdown\n' + heading + '\n<a id="owned-fence-pseudo"></a>\n```'
    source = _alias_owner_fixture().replace(heading + "\n", heading + "\n\n" + example + "\n", 1)
    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(source)
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    updated = path.read_text()
    document = _candidate_document(updated, env_reference_config)
    assert "owned-fence-pseudo" not in _service_document_ids(document)
    if "Future" in heading:
        assert example in updated
        assert any('id="owned-fence-pseudo"' in "".join(node.itertext()) for node in document.iter("code"))
    else:
        assert "owned-fence-pseudo" not in updated
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    assert path.read_text() == updated


@pytest.mark.parametrize("case", [
    (canonical, authored, attribute, quote)
    for canonical, authored in ([
    ("## 2. Capabilities & limitations", heading)
    for heading in ["## 2. Capabilities & limitations",
                    "## 2. Capabilities & limitations ###",
                    "## 2. Capabilities & limitations  ",
                    "##  2. Capabilities & limitations",
                    "##\t2.\tCapabilities\t&\tlimitations\t##"]
] + [
    ("## 3. Dependencies & Integrations", heading)
    for heading in ["## 3. Dependencies & Integrations",
                    "## 3. Dependencies & Integrations ###",
                    "## 3. Dependencies & Integrations  ",
                    "##  3. Dependencies & Integrations"]
] + [
    ("## 2. Capabilities & limitations", "## 02. Capabilities & limitations"),
    ("## 3. Dependencies & Integrations", "## 03. Dependencies & Integrations"),
    ("## 3. Dependencies & Integrations", "## 3. Dependencies&Integrations"),
])
    for attribute, quote in [("id", '"'), ("id", "'"), ("name", '"'), ("name", "'")]
])
def test_accepted_heading_format_preserves_alias(case, tmp_path, env_reference_config):
    from docs.regen import _process
    from tests.test_three_surface_build import _candidate_document

    canonical, authored, attribute, quote = case
    alias = f"  <a {attribute}={quote}formatted-topic{quote}></a>  "
    source = _alias_owner_fixture().replace(canonical + "\n", authored + "\n\n" + alias + "\n", 1)
    original = _candidate_document(source, env_reference_config)
    assert sum("formatted-topic" in (node.get("id"), node.get("name")) for node in original.iter()) == 1
    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(source)
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    updated = path.read_text()
    assert canonical + "\n\n" + alias in updated
    _assert_single_alias_topic(_candidate_document(updated, env_reference_config), "formatted-topic", canonical)
    assert updated.count("formatted-topic") == 1
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    assert path.read_text() == updated


@pytest.mark.parametrize("case", [
    (heading, f"  <a {attribute}={quote}future-format-topic{quote}></a>  ")
    for heading in ["### 3.4. Future — Missing pair integrations",
                    "### 3.5. Future — Candidate new services",
                    "### 3.6. Future — Unused features in this service"]
    for attribute, quote in [("id", '"'), ("id", "'"), ("name", '"'), ("name", "'")]
])
def test_future_heading_format_preserves_alias_topic(case, tmp_path, env_reference_config):
    from docs.regen import _process
    from tests.test_three_surface_build import _candidate_document

    heading, alias = case
    example = "```html\n" + alias + "\n```"
    indented = "    " + alias.lstrip(" ")
    tabbed = "\t" + alias.lstrip(" ")
    example += "\n\n" + indented + "\n\n" + tabbed
    prose = "Keep this existing Future qualification exactly."
    source = _alias_owner_fixture().replace(heading + "\n", heading + " ###\n\n" + alias + "\n\n" + prose + "\n\n" + example + "\n", 1)
    _assert_single_alias_topic(_candidate_document(source, env_reference_config), "future-format-topic", heading)
    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(source)
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    updated = path.read_text()
    _assert_single_alias_topic(_candidate_document(updated, env_reference_config), "future-format-topic", heading)
    assert heading + "\n\n" + alias in updated
    assert updated.count("future-format-topic") == 4
    assert prose in updated and example in updated
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    assert path.read_text() == updated


@pytest.mark.parametrize("case", [
    (heading, closing)
    for heading in ["### 3.4. Future — Missing pair integrations",
                    "### 3.5. Future — Candidate new services",
                    "### 3.6. Future — Unused features in this service"]
    for closing in ["", " ###"]
])
def test_future_closing_hash_body_without_aliases(case, tmp_path, env_reference_config):
    from docs.regen import _process
    from tests.test_three_surface_build import _candidate_document

    heading, closing = case
    prose = "Keep this authored Future qualification exactly."
    example = "```text\n### Literal example heading\nKeep every example byte.\n```"
    source = _alias_owner_fixture().replace(heading + "\n", heading + closing + "\n\n" + prose + "\n\n" + example + "\n", 1)
    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(source)
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    updated = path.read_text()
    assert prose + "\n\n" + example in updated
    assert '<a ' not in updated
    assert prose in "".join(_candidate_document(updated, env_reference_config).itertext())
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    assert path.read_text() == updated
    document = _candidate_document(updated, env_reference_config)
    nodes = list(document.iter())
    current = next(node for node in document.iter("h3") if _service_heading_text(node).strip() == heading.lstrip("# "))
    start = nodes.index(current)
    end = next((i for i in range(start + 1, len(nodes)) if nodes[i].tag in ("h2", "h3")), len(nodes))
    assert prose in "".join("".join(node.itertext()) for node in nodes[start:end])


@pytest.mark.parametrize("heading", [
    "### 3.4. Future — Missing pair integrations",
    "### 3.5. Future — Candidate new services",
    "### 3.6. Future — Unused features in this service",
])
def test_future_trailing_annotation_keeps_existing_body_contract(heading):
    from docs.regen import _extract_future_blocks

    source = heading + " ### trailing annotation\n\nPreserve this existing body.\n"
    topic = heading.split("Future — ", 1)[1]
    assert _extract_future_blocks(source)[topic] == "### trailing annotation\n\nPreserve this existing body."


@pytest.mark.parametrize("case", [
    (heading, 2, literal)
    for heading in ["### 3.4. Future — Missing pair integrations",
                    "### 3.5. Future — Candidate new services",
                    "### 3.6. Future — Unused features in this service"]
    for literal in ["four-space", "tab", "prose-separated", "fenced"]
] + [("### 3.4. Future — Missing pair integrations", indent, "four-space")
     for indent in [0, 3]])
def test_future_alias_prefix_stops_at_code_literal(case, tmp_path, env_reference_config):
    from docs.regen import _process
    from tests.test_three_surface_build import _candidate_document

    heading, indent, literal = case
    anchor = '<a id="prefix-code-topic"></a>'
    alias = " " * indent + anchor + "  "
    example = "\t" + anchor if literal == "tab" else "    " + anchor
    if literal == "fenced":
        example = "```html\n" + anchor + "\n```"
    barrier = "Preserve this prose barrier.\n\n" if literal == "prose-separated" else ""
    body = alias + "\n\n" + barrier + example + "\n"
    source = _alias_owner_fixture().replace(heading + "\n", heading + "\n\n" + body, 1)
    original = _candidate_document(source, env_reference_config)
    _assert_single_alias_topic(original, "prefix-code-topic", heading)
    assert ["".join(node.itertext()) for node in original.iter("code") if "prefix-code-topic" in "".join(node.itertext())] == [anchor + "\n"]
    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(source)
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    updated = path.read_text()
    document = _candidate_document(updated, env_reference_config)
    _assert_single_alias_topic(document, "prefix-code-topic", heading)
    assert ["".join(node.itertext()) for node in document.iter("code") if "prefix-code-topic" in "".join(node.itertext())] == [anchor + "\n"]
    assert heading + "\n\n" + body.rstrip("\n") in updated
    assert updated.count("prefix-code-topic") == 2
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    assert path.read_text() == updated


def _first_literal_document_body(document, heading):
    assert "first-literal-topic" not in _service_document_ids(document)
    rendered = ["".join(node.itertext()) for node in document.iter("code") if "first-literal-topic" in "".join(node.itertext())]
    nodes = list(document.iter())
    code = next(node for node in document.iter("code") if "first-literal-topic" in "".join(node.itertext()))
    preceding = [node for node in nodes[:nodes.index(code)] if node.tag in ("h2", "h3")]
    assert _service_heading_text(preceding[-1]).strip() == heading.lstrip("# ")
    return rendered


@pytest.mark.parametrize("case", [
    (heading, indent)
    for heading in ["### 3.4. Future — Missing pair integrations",
                    "### 3.5. Future — Candidate new services",
                    "### 3.6. Future — Unused features in this service"]
    for indent in ["    ", "\t"]
])
def test_future_first_indented_literal_keeps_body(case, tmp_path, env_reference_config):
    from docs.regen import _extract_future_blocks, _process
    from tests.test_three_surface_build import _candidate_document

    heading, indent = case
    anchor = '<a id="first-literal-topic"></a>'
    body = indent + anchor + "  \n\n" + indent + "Keep final literal padding.  "
    source = _alias_owner_fixture().replace(heading + "\n\n_No high-confidence opportunities identified._", heading + "\n\n" + body, 1)
    original = _candidate_document(source, env_reference_config)
    expected = _first_literal_document_body(original, heading)
    assert len(expected) == 1 and anchor in expected[0]
    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(source)
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    updated = path.read_text()
    document = _candidate_document(updated, env_reference_config)
    assert _first_literal_document_body(document, heading) == expected
    assert heading + "\n\n" + body + "\n" in updated
    assert _extract_future_blocks(updated)[heading.split("Future — ", 1)[1]] == body
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    assert path.read_text() == updated


@pytest.mark.parametrize("case", [
    (heading, body)
    for heading in ["### 3.4. Future — Missing pair integrations",
                    "### 3.5. Future — Candidate new services",
                    "### 3.6. Future — Unused features in this service"]
    for body in [" \t \n\t  ", "  _No high-confidence opportunities identified._ \t"]
])
def test_future_empty_padded_body_keeps_placeholder(case, tmp_path, env_reference_config):
    from docs.regen import _extract_future_blocks, _process
    from tests.test_three_surface_build import _candidate_document

    heading, body = case
    placeholder = "_No high-confidence opportunities identified._"
    source = _alias_owner_fixture().replace(heading + "\n\n" + placeholder, heading + "\n\n" + body, 1)
    assert _extract_future_blocks(source)[heading.split("Future — ", 1)[1]] == ""
    path = tmp_path / "redis" / "README.md"
    path.parent.mkdir()
    path.write_text(source)
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    updated = path.read_text()
    assert updated == _alias_owner_fixture()
    assert '<a ' not in updated
    document = _candidate_document(updated, env_reference_config)
    assert sum("No high-confidence opportunities identified." == node.text for node in document.iter("em")) == 3
    _process("redis", tmp_path, False, True, False, closed_candidates=frozenset())
    assert path.read_text() == updated


def _consumer_action_labels_and_links(action):
    from urllib.parse import urlsplit

    labels = ["".join(node.itertext()).strip().rstrip(".:").casefold()
              for node in action.iter("strong")]
    links = {urlsplit(node.get("href", "")).fragment for node in action.iter("a")}
    return labels, links


@pytest.mark.parametrize("surface", ["repo", "site", "wiki"])
def test_consumer_entry_choose_configure_verify(surface, env_reference_config):
    from tests.test_three_surface_build import _candidate_document
    from tests.three_surface_test_utils import surface_text

    source = "docs/operations/reusing-atlas.md"
    text = (REPO_ROOT / source).read_text() if surface == "repo" else surface_text(source, surface)
    document = _candidate_document(text, env_reference_config)
    nodes = list(document.iter())
    headings = list(document.iter("h2"))
    opening = nodes[nodes.index(headings[0]):nodes.index(headings[1])]
    routes = [node for node in opening if node.tag == "ol"]
    assert routes, (surface, "opening lacks a choose/configure/verify route")
    actions = list(routes[0].findall("li"))
    assert len(actions) == 3, surface
    expected = [("choose", "2-choose-your-reuse-method"),
                ("configure", "41-stand-up-a-consumer-from-scratch-the-ordered-walkthrough"),
                ("verify", "77-post-launch-verification")]
    for action, (label, target) in zip(actions, expected):
        labels, links = _consumer_action_labels_and_links(action)
        assert labels and labels[0] == label, (surface, label, labels)
        assert target in links, (surface, label, links)
    assert "1-tldr" in {node.get("id") for node in document.iter()}, surface
