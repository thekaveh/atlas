"""Provider credentials reach only trusted server-side consumers."""

from __future__ import annotations

import os
import subprocess
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
            "STT_INTERNAL_URL": "http://stt",
            "STT_INTERNAL_API_KEY": api_key,
            "SEARXNG_INTERNAL_URL": "http://search",
            "LIGHTRAG_INTERNAL_URL": "http://lightrag",
            "LIGHTRAG_API_KEY": "lightrag-test",
            "LITELLM_MODELS_LIST": "[]",
        }
    )
    rendered = subprocess.run(
        ["envsubst"],
        input=template.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        env=environment,
        check=True,
    ).stdout

    config = yaml.safe_load(rendered)
    assert config["stt"]["api_key"] == api_key
