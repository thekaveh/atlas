"""Provider credentials reach only trusted server-side consumers."""

from __future__ import annotations

import os
from string import Template
from pathlib import Path

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig


ROOT = Path(__file__).resolve().parents[2]


def _yaml(relative: str) -> dict:
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def _service_config(env_path: Path) -> ServiceConfig:
    parser = ConfigParser(str(ROOT))
    parser.env_file_path = env_path
    config = ServiceConfig(config_parser=parser)
    config.localhost_host = "localhost"
    return config


def test_server_consumers_receive_both_provider_tokens_but_lightrag_does_not():
    expected = {
        "DOCLING_API_TOKEN": "${DOCLING_API_TOKEN}",
        "PARAKEET_API_TOKEN": "${PARAKEET_API_TOKEN}",
    }
    for relative, services in (
        ("services/backend/compose.yml", ("backend",)),
        ("services/n8n/compose.yml", ("n8n", "n8n-worker")),
        ("services/jupyterhub/compose.yml", ("jupyterhub",)),
    ):
        compose = _yaml(relative)
        for service in services:
            environment = compose["services"][service]["environment"]
            for name, value in expected.items():
                assert environment[name] == value, f"{relative}:{service}:{name}"

    lightrag = _yaml("services/lightrag/compose.yml")["services"]["lightrag"]
    assert "DOCLING_API_TOKEN" not in lightrag["environment"]
    assert "PARAKEET_API_TOKEN" not in lightrag["environment"]


def test_manifests_declare_server_side_token_adaptation():
    for relative, service in (
        ("services/backend/service.yml", "backend"),
        ("services/n8n/service.yml", "n8n"),
        ("services/jupyterhub/service.yml", "jupyterhub"),
    ):
        manifest = _yaml(relative)
        adaptation = manifest["runtime_adaptive"][service]["environment_adaptation"]
        assert adaptation["DOCLING_API_TOKEN"] == "${DOCLING_API_TOKEN}"
        assert adaptation["PARAKEET_API_TOKEN"] == "${PARAKEET_API_TOKEN}"


@pytest.mark.parametrize(
    "source,expected",
    [
        ("parakeet-container-gpu", "parakeet-test-token"),
        ("parakeet-localhost", "parakeet-test-token"),
        ("speaches-container-cpu", "sk-unused"),
        ("speaches-container-gpu", "sk-unused"),
        ("whisper-cpp-localhost", "sk-unused"),
        ("disabled", ""),
    ],
)
def test_stt_consumer_key_is_source_aware(env_with_overrides, source, expected):
    config = _service_config(
        env_with_overrides(
            {
                "STT_PROVIDER_SOURCE": source,
                "PARAKEET_API_TOKEN": "parakeet-test-token",
            }
        )
    )

    generated = config.generate_service_environment()

    assert generated["OPEN_WEB_UI_STT_API_KEY"] == expected
    assert generated["STT_INTERNAL_API_KEY"] == expected


@pytest.mark.parametrize("api_key", ["parakeet-test-token", "sk-unused"])
def test_hermes_template_renders_server_side_stt_key(api_key):
    template = ROOT / "services" / "hermes" / "init" / "templates" / "config.yaml.tmpl"
    environment = os.environ.copy()
    environment.update(
        {
            "HERMES_DEFAULT_MODEL": "test-model",
            "HERMES_CONTEXT_LENGTH": "65536",
            "LITELLM_MASTER_KEY": "litellm-test",
            "TTS_INTERNAL_URL": "http://tts",
            "TTS_INTERNAL_MODEL": "tts-1-hd",
            "TTS_INTERNAL_VOICE": "alloy",
            "STT_INTERNAL_URL": "http://stt",
            "STT_INTERNAL_API_KEY": api_key,
            "SEARXNG_INTERNAL_URL": "http://search",
            "LIGHTRAG_INTERNAL_URL": "http://lightrag",
            "LIGHTRAG_API_KEY": "lightrag-test",
            "LITELLM_MODELS_LIST": "[]",
        }
    )
    rendered = Template(template.read_text(encoding="utf-8")).substitute(environment)

    config = yaml.safe_load(rendered)
    # Hermes v2026.6.19 reads only the nested openai blocks; flat keys were
    # ignored and audio went to api.openai.com.
    assert config["stt"]["openai"] == {
        "base_url": "http://stt/v1", "api_key": api_key, "model": "whisper-1",
    }
    assert config["tts"]["openai"]["base_url"] == "http://tts/v1"
    assert "base_url" not in config["stt"] and "base_url" not in config["tts"]
    init = (ROOT / "services/hermes/init/scripts/init-hermes.sh").read_text(encoding="utf-8")
    assert 'STT_INTERNAL_API_KEY="${STT_INTERNAL_API_KEY:-not-required}"' in init
    compose = yaml.safe_load((ROOT / "services/hermes/compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["hermes"]["environment"]["VOICE_TOOLS_OPENAI_KEY"] == "not-required"
    # Hermes reads web.search_backend + the SEARXNG_URL env var (no `search:`),
    # has no config-declared HTTP tools, and indexes only <dir>/SKILL.md.
    assert config["web"] == {"search_backend": "searxng"} and "search" not in config
    assert "tools" not in config
    assert compose["services"]["hermes"]["environment"]["SEARXNG_URL"] == "${SEARXNG_INTERNAL_URL:-}"
    assert '"${SKILLS_DIR}/creative/atlas-comfyui-host/SKILL.md"' in init


def test_hermes_default_model_fallback_skips_non_chat_routes():
    # The name filter ('embed') let bge-m3 become Hermes's default model and
    # 500 every request; the fallback must drop LiteLLM non-chat modes too.
    import re
    import subprocess

    script = (ROOT / "services/hermes/init/scripts/init-hermes.sh").read_text(encoding="utf-8")
    assert 'litellm_get "/model/info"' in script  # the key goes on curl's stdin (-K-)
    assert 'select((.model_info.mode // "chat") != "chat")' in script
    program = re.search(r"awk '(BEGIN \{ n = split\(ENVIRON\[\"NON_CHAT_IDS\"\].*?)'", script).group(1)
    kept = subprocess.run(
        ["awk", program], input="ollama/bge-m3\nollama/llama3.2:3b\n",
        env={**os.environ, "NON_CHAT_IDS": "ollama/bge-m3\nfal-image"},
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert kept == ["ollama/llama3.2:3b"]


def test_hermes_tts_uses_the_engine_voice_open_webui_uses():
    # Speaches' Kokoro has no OpenAI "alloy" voice; hermes-init receives the
    # same per-engine model/voice the bootstrapper computes for Open WebUI.
    template = (ROOT / "services/hermes/init/templates/config.yaml.tmpl").read_text(encoding="utf-8")
    assert "model: ${TTS_INTERNAL_MODEL}" in template
    assert "voice: ${TTS_INTERNAL_VOICE}" in template
    compose = yaml.safe_load((ROOT / "services/hermes/compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["hermes-init"]["environment"]
    assert env["TTS_INTERNAL_VOICE"] == "${OPEN_WEB_UI_TTS_VOICE:-alloy}"
    assert env["TTS_INTERNAL_MODEL"] == "${OPEN_WEB_UI_TTS_MODEL:-tts-1-hd}"
