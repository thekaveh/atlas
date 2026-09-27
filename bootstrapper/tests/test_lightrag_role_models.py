"""LightRAG role-specific LLM model configuration tests."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
LIGHTRAG_MANIFEST = REPO_ROOT / "services" / "lightrag" / "service.yml"
LIGHTRAG_COMPOSE = REPO_ROOT / "services" / "lightrag" / "compose.yml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke-lightrag-role-models.sh"

ROLE_INPUTS = {
    "LIGHTRAG_EXTRACT_LLM_MODEL": {"native": "EXTRACT_LLM_MODEL", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_MODEL": {"native": "KEYWORD_LLM_MODEL", "secret": False},
    "LIGHTRAG_QUERY_LLM_MODEL": {"native": "QUERY_LLM_MODEL", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_BINDING": {"native": "EXTRACT_LLM_BINDING", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_BINDING": {"native": "KEYWORD_LLM_BINDING", "secret": False},
    "LIGHTRAG_QUERY_LLM_BINDING": {"native": "QUERY_LLM_BINDING", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_BINDING_HOST": {"native": "EXTRACT_LLM_BINDING_HOST", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_BINDING_HOST": {"native": "KEYWORD_LLM_BINDING_HOST", "secret": False},
    "LIGHTRAG_QUERY_LLM_BINDING_HOST": {"native": "QUERY_LLM_BINDING_HOST", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY": {
        "native": "EXTRACT_LLM_BINDING_API_KEY",
        "secret": True,
        # #796: EXTRACT joins KEYWORD/QUERY (#721) on the LiteLLM master key.
        "compose": "${LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY:-${LITELLM_MASTER_KEY}}",
    },
    "LIGHTRAG_KEYWORD_LLM_BINDING_API_KEY": {
        "native": "KEYWORD_LLM_BINDING_API_KEY",
        "secret": True,
        # #721: falls back to the in-network LiteLLM master key when unset.
        "compose": "${LIGHTRAG_KEYWORD_LLM_BINDING_API_KEY:-${LITELLM_MASTER_KEY}}",
    },
    "LIGHTRAG_QUERY_LLM_BINDING_API_KEY": {
        "native": "QUERY_LLM_BINDING_API_KEY",
        "secret": True,
        "compose": "${LIGHTRAG_QUERY_LLM_BINDING_API_KEY:-${LITELLM_MASTER_KEY}}",
    },
    "LIGHTRAG_EXTRACT_MAX_ASYNC_LLM": {"native": "EXTRACT_MAX_ASYNC_LLM", "secret": False},
    "LIGHTRAG_KEYWORD_MAX_ASYNC_LLM": {"native": "KEYWORD_MAX_ASYNC_LLM", "secret": False},
    "LIGHTRAG_QUERY_MAX_ASYNC_LLM": {"native": "QUERY_MAX_ASYNC_LLM", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_TIMEOUT": {"native": "EXTRACT_LLM_TIMEOUT", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_TIMEOUT": {"native": "KEYWORD_LLM_TIMEOUT", "secret": False},
    "LIGHTRAG_QUERY_LLM_TIMEOUT": {"native": "QUERY_LLM_TIMEOUT", "secret": False},
}

# #796: EXTRACT generation caps for a native-Ollama EXTRACT binding. LightRAG
# 1.5.4 forwards a non-integer value (even "") to Ollama as a string, which
# rejects every call, so each default and Compose fallback must be an integer.
EXTRACT_OLLAMA_CAPS = {
    "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT": {
        "native": "EXTRACT_OLLAMA_LLM_NUM_PREDICT",
        "default": "4096",
        "compose": "${LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT:-4096}",
    },
    "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_CTX": {
        "native": "EXTRACT_OLLAMA_LLM_NUM_CTX",
        "default": "16384",
        "compose": "${LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_CTX:-16384}",
    },
}

QUERY_INPUTS = {
    "LIGHTRAG_QUERY_ENABLE_RERANK": {
        "native": "RERANK_BY_DEFAULT",
        "default": "false",
        "compose": "${LIGHTRAG_QUERY_ENABLE_RERANK:-false}",
    },
    "LIGHTRAG_QUERY_TOP_K": {
        "native": "TOP_K",
        "default": "10",
        "compose": "${LIGHTRAG_QUERY_TOP_K:-10}",
    },
    "LIGHTRAG_QUERY_CHUNK_TOP_K": {
        "native": "CHUNK_TOP_K",
        "default": "5",
        "compose": "${LIGHTRAG_QUERY_CHUNK_TOP_K:-5}",
    },
    "LIGHTRAG_QUERY_MAX_TOTAL_TOKENS": {
        "native": "MAX_TOTAL_TOKENS",
        "default": "12000",
        "compose": "${LIGHTRAG_QUERY_MAX_TOTAL_TOKENS:-12000}",
    },
}


def _manifest_env_by_name() -> dict[str, dict]:
    data = yaml.safe_load(LIGHTRAG_MANIFEST.read_text(encoding="utf-8"))
    return {entry["name"]: entry for entry in data["env"]}


def _compose_lightrag_environment() -> dict[str, str]:
    data = yaml.safe_load(LIGHTRAG_COMPOSE.read_text(encoding="utf-8"))
    return data["services"]["lightrag"]["environment"]


def test_lightrag_manifest_declares_role_llm_inputs():
    env_by_name = _manifest_env_by_name()

    for atlas_name, meta in ROLE_INPUTS.items():
        assert atlas_name in env_by_name
        assert env_by_name[atlas_name].get("default", "") == ""
        if meta["secret"]:
            assert env_by_name[atlas_name].get("secret") is True


def test_lightrag_manifest_declares_query_controls():
    env_by_name = _manifest_env_by_name()

    for atlas_name, meta in QUERY_INPUTS.items():
        assert atlas_name in env_by_name
        assert env_by_name[atlas_name].get("default", "") == meta["default"]


def test_lightrag_compose_maps_role_inputs_to_native_env_names():
    env = _compose_lightrag_environment()

    for atlas_name, meta in ROLE_INPUTS.items():
        native_name = meta["native"]
        assert native_name in env
        expected = meta.get("compose", f"${{{atlas_name}:-}}")
        assert env[native_name] == expected


def test_lightrag_role_api_keys_default_to_litellm_master_key():
    """#721 (KEYWORD/QUERY) and #796 (EXTRACT): every role API key falls back
    to the in-network LiteLLM master key when the LIGHTRAG_* override is
    unset, so a consumer pointing a role at LiteLLM needs zero key wiring."""
    env = _compose_lightrag_environment()

    for role in ("EXTRACT", "KEYWORD", "QUERY"):
        assert env[f"{role}_LLM_BINDING_API_KEY"] == (
            f"${{LIGHTRAG_{role}_LLM_BINDING_API_KEY:-${{LITELLM_MASTER_KEY}}}}"
        )
    # The base binding uses the same master key the roles inherit.
    assert env["LLM_BINDING_API_KEY"] == "${LITELLM_MASTER_KEY}"


def test_extract_ollama_caps_are_declared_in_all_three_files():
    """#796 AC1/AC2: the caps reach a native-Ollama EXTRACT binding as
    EXTRACT_OLLAMA_LLM_NUM_PREDICT / _NUM_CTX from the manifest, Compose and
    .env.example alike, with an integer output cap inside 2k-4k."""
    env_by_name = _manifest_env_by_name()
    compose = _compose_lightrag_environment()
    example = ENV_EXAMPLE.read_text(encoding="utf-8")

    for atlas_name, meta in EXTRACT_OLLAMA_CAPS.items():
        assert env_by_name[atlas_name]["default"] == meta["default"]
        assert compose[meta["native"]] == meta["compose"]
        assert f"\n{atlas_name}={meta['default']}\n" in example
    assert 2048 <= int(EXTRACT_OLLAMA_CAPS["LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT"]["default"]) <= 4096
    for path in (LIGHTRAG_COMPOSE, LIGHTRAG_MANIFEST, ENV_EXAMPLE):
        assert "OLLAMA_LLM_NUM_PREDICT" in path.read_text(encoding="utf-8"), path


def test_lightrag_compose_maps_query_controls_to_native_env_names():
    env = _compose_lightrag_environment()

    for meta in QUERY_INPUTS.values():
        assert env[meta["native"]] == meta["compose"]


def test_lightrag_compose_keeps_base_models_init_resolved():
    env = _compose_lightrag_environment()

    assert "LLM_MODEL" not in env
    assert "EMBEDDING_MODEL" not in env
    assert "EMBEDDING_DIM" not in env


def test_lightrag_role_smoke_fails_when_model_evidence_is_missing():
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "missing expected EXTRACT model" in script
    assert "missing expected QUERY model" in script
    assert "pass criteria:" not in script
    assert "--since=10m" not in script
    assert "--since=\"$smoke_started_at\"" in script


def _docker_available() -> bool:
    return shutil.which("docker") is not None


_needs_docker = pytest.mark.skipif(
    not _docker_available() or not ENV_EXAMPLE.is_file(),
    reason="docker not on PATH or .env.example missing",
)


def _render_lightrag_environment(tmp_path: Path, overrides: dict[str, str]) -> dict[str, str]:
    """``docker compose config`` over .env.example with ``overrides`` applied."""
    env_file = tmp_path / ".env"
    out_lines = []
    seen = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            out_lines.append(line)
            continue
        key = line.split("=", 1)[0]
        if key in overrides:
            out_lines.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            out_lines.append(line)
    for key, value in overrides.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")
    env_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-p",
            "atlas",
            "-f",
            str(COMPOSE),
            "config",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    return rendered["services"]["lightrag"]["environment"]


_LIGHTRAG_ON = {
    "PROJECT_NAME": "atlas",
    "LIGHTRAG_SOURCE": "container",
    "LIGHTRAG_SCALE": "1",
    "LIGHTRAG_INIT_SCALE": "1",
}


@_needs_docker
def test_lightrag_role_models_render_into_container_environment(tmp_path: Path):
    env = _render_lightrag_environment(tmp_path, {
        **_LIGHTRAG_ON,
        "LIGHTRAG_EXTRACT_LLM_MODEL": "mistral-small3.2:24b",
        "LIGHTRAG_KEYWORD_LLM_MODEL": "mistral-small3.2:24b",
        "LIGHTRAG_QUERY_LLM_MODEL": "qwen3.8:latest",
        "LIGHTRAG_EXTRACT_MAX_ASYNC_LLM": "1",
        "LIGHTRAG_QUERY_LLM_TIMEOUT": "900",
        "LIGHTRAG_QUERY_ENABLE_RERANK": "false",
        "LIGHTRAG_QUERY_TOP_K": "10",
        "LIGHTRAG_QUERY_CHUNK_TOP_K": "5",
        "LIGHTRAG_QUERY_MAX_TOTAL_TOKENS": "12000",
    })

    assert env["EXTRACT_LLM_MODEL"] == "mistral-small3.2:24b"
    assert env["KEYWORD_LLM_MODEL"] == "mistral-small3.2:24b"
    assert env["QUERY_LLM_MODEL"] == "qwen3.8:latest"
    assert env["EXTRACT_MAX_ASYNC_LLM"] == "1"
    assert env["QUERY_LLM_TIMEOUT"] == "900"
    assert env["KEYWORD_LLM_TIMEOUT"] == ""
    assert env["RERANK_BY_DEFAULT"] == "false"
    assert env["TOP_K"] == "10"
    assert env["CHUNK_TOP_K"] == "5"
    assert env["MAX_TOTAL_TOKENS"] == "12000"


@_needs_docker
def test_litellm_routed_extract_needs_no_key_wiring(tmp_path: Path):
    """#796 AC5: setting only the EXTRACT binding and host (here LiteLLM
    itself) renders a non-empty EXTRACT key, the LiteLLM master key, as
    KEYWORD and QUERY already do (#721)."""
    env = _render_lightrag_environment(tmp_path, {
        **_LIGHTRAG_ON,
        "LITELLM_MASTER_KEY": "sk-atlas-master-796",
        "LIGHTRAG_EXTRACT_LLM_BINDING": "openai",
        "LIGHTRAG_EXTRACT_LLM_BINDING_HOST": "http://litellm:4000/v1",
    })

    assert (env["EXTRACT_LLM_BINDING"], env["EXTRACT_LLM_BINDING_API_KEY"]) == (
        "openai", "sk-atlas-master-796")


@_needs_docker
def test_native_ollama_extract_renders_numeric_caps_and_its_own_key(tmp_path: Path):
    """#796 AC1/AC2: with the documented native-Ollama settings and the cap
    variables left blank, Compose renders numeric caps, and the placeholder
    key keeps the LiteLLM master key away from Ollama."""
    env = _render_lightrag_environment(tmp_path, {
        **_LIGHTRAG_ON,
        "LITELLM_MASTER_KEY": "sk-atlas-master-796",
        "LIGHTRAG_EXTRACT_LLM_MODEL": "mistral-small3.2:24b",
        "LIGHTRAG_EXTRACT_LLM_BINDING": "ollama",
        "LIGHTRAG_EXTRACT_LLM_BINDING_HOST": "http://host.docker.internal:11434",
        "LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY": "ollama",
        "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT": "",
        "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_CTX": "",
    })

    assert (
        env["EXTRACT_LLM_BINDING_API_KEY"],
        env["EXTRACT_OLLAMA_LLM_NUM_PREDICT"], env["EXTRACT_OLLAMA_LLM_NUM_CTX"],
    ) == ("ollama", "4096", "16384")


def test_extract_generation_caps_are_documented():
    """#796 AC6 (and the upstream findings for AC3/AC4): the README, the
    consumer guide and the generated env reference name the caps."""
    readme = (REPO_ROOT / "services" / "lightrag" / "README.md").read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs" / "operations" / "reusing-atlas.md").read_text(encoding="utf-8")
    reference = (REPO_ROOT / "docs" / "reference" / "env-vars.md").read_text(encoding="utf-8")

    for text in (readme, guide, reference):
        assert "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT" in text
        assert "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_CTX" in text
    for fact in (
        "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT=4096",
        "LIGHTRAG_EXTRACT_LLM_MODEL=",
        "LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY=ollama",
        "POST /documents/clear_cache",
        "blob/v1.5.4/lightrag/utils.py#L1274-L1277",
        "blob/v1.5.4/lightrag/operate.py#L3746-L3779",
        "not configurable in 1.5.4",
    ):
        assert fact in readme
    assert "LIGHTRAG_EXTRACT_LLM_MODEL" in guide
    assert "placeholder such as `ollama`" in guide
