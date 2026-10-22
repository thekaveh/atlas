"""Drift guard: the auto-managed endpoint consumer-bridging pattern must stay
documented in reusing-atlas.md (issue #349).

A downstream consumer joining the Atlas network needs to know how to bridge
Atlas's computed endpoint variables (COMFYUI_ENDPOINT, OLLAMA_ENDPOINT,
LITELLM_BASE_URL, MINIO_ENDPOINT) into its own service variables. This test
asserts the documentation exists so it can't be accidentally deleted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from markdown_it import MarkdownIt
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
REUSING_ATLAS = REPO_ROOT / "docs" / "operations" / "reusing-atlas.md"
SUBMODULE_USAGE = REPO_ROOT / "docs" / "operations" / "submodule-usage.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
CO_LAUNCHED_EXAMPLES = (
    ("docs/operations/reusing-atlas.md", "rag-indexer"),
    ("docs/CONTRIBUTING-services.md", "my-extra-service"),
)
BIND_CASES = (None, "", "0.0.0.0:")


def test_reusing_atlas_documents_comfyui_endpoint_bridging() -> None:
    """reusing-atlas.md must mention COMFYUI_ENDPOINT and the consumer
    bridging pattern — so downstream consumers know to use it instead of
    hard-coding URLs."""
    text = REUSING_ATLAS.read_text(encoding="utf-8")
    assert "COMFYUI_ENDPOINT" in text, (
        "reusing-atlas.md must document COMFYUI_ENDPOINT as the canonical "
        "consumer URL variable (issue #349). The endpoint-bridging section "
        "appears to have been removed."
    )
    assert "${COMFYUI_ENDPOINT" in text, (
        "reusing-atlas.md must show the consumer-bridging pattern "
        "(${CONSUMER_VAR:-${COMFYUI_ENDPOINT:-<default>}})."
    )


def test_reusing_atlas_documents_other_auto_managed_endpoints() -> None:
    """The bridging pattern section should also cover at least one other
    auto-managed endpoint beyond ComfyUI, so the pattern is documented as
    general (not ComfyUI-specific)."""
    text = REUSING_ATLAS.read_text(encoding="utf-8")
    assert "LITELLM_BASE_URL" in text or "OLLAMA_ENDPOINT" in text, (
        "reusing-atlas.md must document the bridging pattern for at least "
        "one other auto-managed endpoint (LITELLM_BASE_URL or "
        "OLLAMA_ENDPOINT), per issue #349 acceptance criteria."
    )


def test_env_example_comfyui_endpoint_mentions_consumer() -> None:
    """The .env.example comment for COMFYUI_ENDPOINT must mention the
    consumer/overlay use case, so operators discover the pattern from .env
    without reading the full reusing-atlas.md guide."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    # Find the COMFYUI_ENDPOINT block
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "COMFYUI_ENDPOINT=":
            # The description comment is above the var (the block comment)
            context = "\n".join(lines[max(0, i - 3):i + 1])
            assert "consumer" in context.lower() or "overlay" in context.lower(), (
                f"The .env.example comment for COMFYUI_ENDPOINT must mention "
                f"the consumer/overlay pattern (issue #349). Found:\n{context}"
            )
            return
    # If we reach here, COMFYUI_ENDPOINT= wasn't found at all
    raise AssertionError("COMFYUI_ENDPOINT= not found in .env.example")


def test_submodule_docs_cover_parent_repo_reference_layout() -> None:
    """Issue #421: submodule consumers need the parent-owned overlay layout,
    force-set SOURCE gotcha, track override rule, and validation checklist."""
    from tests.three_surface_test_utils import surface_text

    reusing = REUSING_ATLAS.read_text(encoding="utf-8")
    submodule = SUBMODULE_USAGE.read_text(encoding="utf-8")

    assert "parent-repo consumer reference layout" in submodule.lower()
    assert "compose/<name>-overlay.yml" in submodule
    assert "services/_user/<name>/compose.yml" in submodule
    assert "setup-overlay.sh" in submodule
    assert "start-infra.sh" in submodule
    assert "set_env_default" in submodule
    assert "force-set" in submodule
    assert "Explicit `--<service>-source` flags override the selected `--track`" in submodule
    assert "Validation checklist" in submodule
    assert "RAG-showcase-style" in submodule
    assert "DayDreams-style" in submodule
    assert "Explicit `--<service>-source` flags override track membership" in reusing

    for rendered in (
        surface_text("docs/development.md", "site"),
        surface_text("docs/development.md", "wiki"),
    ):
        assert "Parent-Repo Consumer Layout" in rendered
        assert "compose/<name>-overlay.yml" in rendered
        assert "scripts/setup-overlay.sh" in rendered
        assert "force-set" in rendered


def _consumer_compose_example(relative_path, marker):
    tokens = MarkdownIt("commonmark").parse(
        (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    )
    examples = [
        token.content
        for token in tokens
        if token.type == "fence"
        and token.info.strip() in {"yaml", "yml"}
        and marker in token.content.splitlines()
    ]
    assert len(examples) == 1, f"Expected one YAML example: {relative_path}: {marker}"
    data = yaml.safe_load(examples[0])
    assert isinstance(data, dict)
    return examples[0], data


def _assert_consumer_compose_input(data, service):
    """Admit only inert fields before passing canonical text to Compose."""
    assert set(data) <= {"services", "networks"}
    assert set(data["services"]) == {service}
    _assert_consumer_service_input(data["services"][service])
    networks = data.get("networks", {})
    assert set(networks) <= {"backend-network"}
    network = networks.get("backend-network", {})
    assert set(network) <= {"name", "driver", "external"}
    assert all(isinstance(value, (str, bool)) for value in network.values())


def _assert_consumer_service_input(definition):
    assert set(definition) <= {
        "image", "container_name", "restart", "environment", "ports", "networks",
    }
    assert definition["networks"] == ["backend-network"]
    assert all(
        isinstance(definition[key], str)
        for key in {"image", "container_name", "restart"} & definition.keys()
    )
    assert all(isinstance(port, str) for port in definition.get("ports", []))
    environment = definition.get("environment", {})
    assert isinstance(environment, dict)
    assert all(isinstance(value, str) for value in environment.values())


def _managed_consumer_root(service):
    network = {"name": "${PROJECT_NAME}-network", "driver": "bridge"}
    actual = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    actual_network = actual["networks"]["backend-network"]
    assert {key: actual_network[key] for key in network} == network
    assert actual_network.get("external", False) is False
    return {
        "services": {
            service: {
                "image": "synthetic.invalid/never-run:fixture",
                "networks": ["backend-network"],
            },
        },
        "networks": {"backend-network": network},
    }


def _consumer_compose_environment(tmp_path, bind_case):
    tmp_path.chmod(0o700)
    for name in ("home", "tmp", "docker-config"):
        (tmp_path / name).mkdir(mode=0o700)
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "tmp"),
        "DOCKER_CONFIG": str(tmp_path / "docker-config"),
        "DOCKER_HOST": f"unix://{tmp_path / 'nonexistent.sock'}",
        "COMPOSE_DISABLE_ENV_FILE": "1",
        "PROJECT_NAME": "atlas-consumer-doc-fixture",
    }
    if bind_case is not None:
        environment["HOST_BIND_IP"] = bind_case
    return environment


def _merged_consumer_example(tmp_path, example, bind_case):
    plugin_setting = os.environ.get("ATLAS_DOCS_COMPOSE_PLUGIN")
    if plugin_setting is None:
        pytest.skip("Set ATLAS_DOCS_COMPOSE_PLUGIN for daemon-free config coverage")
    plugin = Path(plugin_setting)
    assert plugin.is_absolute(), "ATLAS_DOCS_COMPOSE_PLUGIN must be absolute"
    assert plugin.is_file() and os.access(plugin, os.X_OK), "Compose plugin unavailable"
    relative_path, service = example
    source, data = _consumer_compose_example(
        relative_path, f"# services/_user/{service}/compose.yml"
    )
    _assert_consumer_compose_input(data, service)
    environment = _consumer_compose_environment(tmp_path, bind_case)
    root_path = tmp_path / "managed-root.yml"
    root_path.write_text(yaml.safe_dump(_managed_consumer_root(service)))
    overlay_path = tmp_path / "overlay.yml"
    overlay_path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [
            str(plugin), "--project-directory", str(tmp_path),
            "-p", "atlas-consumer-doc-fixture", "-f", str(root_path),
            "-f", str(overlay_path), "config", "--format", "json",
        ],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr[:4000]
    assert len(result.stdout) <= 65536, "Unexpectedly large Compose config"
    return json.loads(result.stdout)


@pytest.mark.parametrize("example", CO_LAUNCHED_EXAMPLES)
def test_co_launched_consumer_examples_attach_without_external_ownership(example):
    relative_path, service = example
    _, data = _consumer_compose_example(
        relative_path, f"# services/_user/{service}/compose.yml"
    )
    _assert_consumer_compose_input(data, service)
    assert data.get("networks", {}).get("backend-network", {}).get("external", False) is False
    if service == "rag-indexer":
        assert data["services"][service]["ports"] == [
            "${HOST_BIND_IP-127.0.0.1:}8090:8090",
        ]


def test_separate_project_consumer_keeps_external_network_ownership():
    _, data = _consumer_compose_example(
        "docs/operations/reusing-atlas.md", "# your-rag-project/docker-compose.yml"
    )
    assert data["services"]["rag-app"]["networks"] == ["atlas"]
    assert data["networks"]["atlas"]["external"] is True
    assert data["networks"]["atlas"]["name"] == "atlas-network"


@pytest.mark.parametrize("example", CO_LAUNCHED_EXAMPLES)
@pytest.mark.parametrize("bind_case", BIND_CASES, ids=("unset", "empty", "explicit"))
def test_co_launched_consumer_examples_keep_managed_network(tmp_path, example, bind_case):
    rendered = _merged_consumer_example(tmp_path, example, bind_case)
    network = rendered["networks"]["backend-network"]
    assert network["name"] == "atlas-consumer-doc-fixture-network"
    assert network["driver"] == "bridge"
    assert network.get("external", False) is False
    service = rendered["services"][example[1]]
    assert "backend-network" in service["networks"]
    if example[1] == "rag-indexer":
        _assert_consumer_published_port(service, bind_case)


def _assert_consumer_published_port(service, bind_case):
    port, = service["ports"]
    assert port["target"] == 8090
    assert port["published"] == "8090"
    expected_bind = {None: "127.0.0.1", "": "", "0.0.0.0:": "0.0.0.0"}
    assert port.get("host_ip", "") == expected_bind[bind_case]
