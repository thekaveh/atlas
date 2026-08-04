"""Provider API credentials are generated once and remain private."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from utils.key_generator import KeyGenerator


ROOT = Path(__file__).resolve().parents[2]
TOKEN_PATTERN = re.compile(r"sk-atlas-(docling|parakeet)-[A-Za-z0-9_-]{40,}")


def _seed_env(root: Path, body: str = "") -> Path:
    env_file = root / ".env"
    env_file.write_text(body, encoding="utf-8")
    return env_file


def test_provider_token_generator_returns_distinct_prefixed_url_safe_secrets(tmp_path):
    _seed_env(tmp_path)
    generator = KeyGenerator(str(tmp_path))

    docling = generator.generate_provider_api_token("DOCLING")
    parakeet = generator.generate_provider_api_token("PARAKEET")

    assert TOKEN_PATTERN.fullmatch(docling)
    assert TOKEN_PATTERN.fullmatch(parakeet)
    assert docling.startswith("sk-atlas-docling-")
    assert parakeet.startswith("sk-atlas-parakeet-")
    assert docling != parakeet


def test_provider_tokens_are_generated_when_absent_and_preserved_on_warm_run(tmp_path):
    _seed_env(tmp_path, "DOCLING_API_TOKEN=\nPARAKEET_API_TOKEN=\n")
    generator = KeyGenerator(str(tmp_path))

    first = generator.generate_missing_keys(force_regenerate=False)
    initial_docling = generator.get_current_env_value("DOCLING_API_TOKEN")
    initial_parakeet = generator.get_current_env_value("PARAKEET_API_TOKEN")
    second = generator.generate_missing_keys(force_regenerate=False)

    assert first["DOCLING_API_TOKEN"] is True
    assert first["PARAKEET_API_TOKEN"] is True
    assert second["DOCLING_API_TOKEN"] is True
    assert second["PARAKEET_API_TOKEN"] is True
    assert generator.get_current_env_value("DOCLING_API_TOKEN") == initial_docling
    assert generator.get_current_env_value("PARAKEET_API_TOKEN") == initial_parakeet


def test_cold_regeneration_does_not_rotate_provider_tokens(tmp_path):
    _seed_env(
        tmp_path,
        "DOCLING_API_TOKEN=operator-docling-token\n"
        "PARAKEET_API_TOKEN=operator-parakeet-token\n",
    )
    generator = KeyGenerator(str(tmp_path))

    results = generator.generate_missing_keys(force_regenerate=True)

    assert results["DOCLING_API_TOKEN"] is True
    assert results["PARAKEET_API_TOKEN"] is True
    assert generator.get_current_env_value("DOCLING_API_TOKEN") == "operator-docling-token"
    assert generator.get_current_env_value("PARAKEET_API_TOKEN") == "operator-parakeet-token"


def test_provider_tokens_honor_custom_atlas_env_file(tmp_path, monkeypatch):
    default_env = _seed_env(tmp_path, "UNCHANGED=yes\n")
    custom_env = tmp_path / "provider.env"
    custom_env.write_text("DOCLING_API_TOKEN=\nPARAKEET_API_TOKEN=\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(custom_env))
    generator = KeyGenerator(str(tmp_path))

    assert generator.generate_and_update_docling_api_token() is True
    assert generator.generate_and_update_parakeet_api_token() is True

    custom_body = custom_env.read_text(encoding="utf-8")
    assert re.search(r"^DOCLING_API_TOKEN=sk-atlas-docling-", custom_body, re.MULTILINE)
    assert re.search(r"^PARAKEET_API_TOKEN=sk-atlas-parakeet-", custom_body, re.MULTILINE)
    assert default_env.read_text(encoding="utf-8") == "UNCHANGED=yes\n"


def test_provider_tokens_are_secret_manifest_values_with_blank_public_placeholders():
    expected = {
        "services/docling/service.yml": "DOCLING_API_TOKEN",
        "services/parakeet/service.yml": "PARAKEET_API_TOKEN",
    }
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    for relative_path, variable in expected.items():
        manifest = yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))
        declaration = next(item for item in manifest["env"] if item["name"] == variable)
        assert declaration["secret"] is True
        assert declaration.get("default", "") == ""
        assert variable not in manifest.get("exports", [])
        assert re.search(rf"^{variable}=$", env_example, re.MULTILINE)
