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
    "LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY": {"native": "EXTRACT_LLM_BINDING_API_KEY", "secret": True},
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


def test_lightrag_keyword_query_api_keys_default_to_litellm_master_key():
    """#721: KEYWORD/QUERY role API keys fall back to the in-network LiteLLM
    master key when the LIGHTRAG_* override is unset, so a consumer pointing
    those roles at LiteLLM needs zero key wiring. EXTRACT stays opt-in (empty)."""
    env = _compose_lightrag_environment()

    assert env["KEYWORD_LLM_BINDING_API_KEY"] == (
        "${LIGHTRAG_KEYWORD_LLM_BINDING_API_KEY:-${LITELLM_MASTER_KEY}}"
    )
    assert env["QUERY_LLM_BINDING_API_KEY"] == (
        "${LIGHTRAG_QUERY_LLM_BINDING_API_KEY:-${LITELLM_MASTER_KEY}}"
    )
    # EXTRACT remains empty-default (out of scope for #721).
    assert env["EXTRACT_LLM_BINDING_API_KEY"] == "${LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY:-}"
    # The base binding uses the same master key the KEYWORD/QUERY roles now inherit.
    assert env["LLM_BINDING_API_KEY"] == "${LITELLM_MASTER_KEY}"


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


@pytest.mark.skipif(
    not _docker_available() or not ENV_EXAMPLE.is_file(),
    reason="docker not on PATH or .env.example missing",
)
def test_lightrag_role_models_render_into_container_environment(tmp_path: Path):
    env_file = tmp_path / ".env"
    overrides = {
        "PROJECT_NAME": "atlas",
        "LIGHTRAG_SOURCE": "container",
        "LIGHTRAG_SCALE": "1",
        "LIGHTRAG_INIT_SCALE": "1",
        "LIGHTRAG_EXTRACT_LLM_MODEL": "mistral-small3.2:24b",
        "LIGHTRAG_KEYWORD_LLM_MODEL": "mistral-small3.2:24b",
        "LIGHTRAG_QUERY_LLM_MODEL": "qwen3.6:latest",
        "LIGHTRAG_EXTRACT_MAX_ASYNC_LLM": "1",
        "LIGHTRAG_QUERY_LLM_TIMEOUT": "900",
        "LIGHTRAG_QUERY_ENABLE_RERANK": "false",
        "LIGHTRAG_QUERY_TOP_K": "10",
        "LIGHTRAG_QUERY_CHUNK_TOP_K": "5",
        "LIGHTRAG_QUERY_MAX_TOTAL_TOKENS": "12000",
    }

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
    env = rendered["services"]["lightrag"]["environment"]

    assert env["EXTRACT_LLM_MODEL"] == "mistral-small3.2:24b"
    assert env["KEYWORD_LLM_MODEL"] == "mistral-small3.2:24b"
    assert env["QUERY_LLM_MODEL"] == "qwen3.6:latest"
    assert env["EXTRACT_MAX_ASYNC_LLM"] == "1"
    assert env["QUERY_LLM_TIMEOUT"] == "900"
    assert env["KEYWORD_LLM_TIMEOUT"] == ""
    assert env["RERANK_BY_DEFAULT"] == "false"
    assert env["TOP_K"] == "10"
    assert env["CHUNK_TOP_K"] == "5"
    assert env["MAX_TOTAL_TOKENS"] == "12000"
