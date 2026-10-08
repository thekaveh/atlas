"""SourceValidator error accumulation across multiple invalid SOURCEs.

Regression guard: validate_source_value() used to reset the shared
validation_errors list on every call, so a .env with two invalid SOURCE
values (followed by valid ones) failed validation while reporting zero
errors — start.py then printed "✅ All SOURCE values are valid"
immediately before sys.exit(1).
"""
from __future__ import annotations

from pathlib import Path
from core.config_parser import ConfigParser
from services.source_validator import SourceValidator


REPO_ROOT = Path(__file__).resolve().parents[2]


def _validator(env_path: Path) -> SourceValidator:
    cp = ConfigParser(str(REPO_ROOT))
    cp.env_file_path = env_path
    return SourceValidator(config_parser=cp)


def test_multiple_invalid_sources_all_reported(env_with_overrides):
    v = _validator(env_with_overrides({
        "COMFYUI_SOURCE": "bogus-value",
        "WEAVIATE_SOURCE": "also-bogus",
    }))
    assert v.validate_all_sources() is False
    errors = v.get_validation_errors()
    joined = "\n".join(errors)
    assert "COMFYUI_SOURCE" in joined
    assert "WEAVIATE_SOURCE" in joined


def test_single_invalid_source_not_wiped_by_later_valid_ones(env_with_overrides):
    """An early invalid SOURCE must survive validation of later valid vars."""
    v = _validator(env_with_overrides({"COMFYUI_SOURCE": "bogus-value"}))
    assert v.validate_all_sources() is False
    assert any("COMFYUI_SOURCE" in e for e in v.get_validation_errors())


def test_default_env_example_is_valid(env_with_overrides):
    v = _validator(env_with_overrides({}))
    assert v.validate_all_sources() is True
    assert v.get_validation_errors() == []


def test_vllm_metal_only_is_a_valid_litellm_upstream(env_with_overrides):
    """A vLLM-Metal-only stack (no Ollama engine, no cloud) is valid — vLLM Metal
    is a real LiteLLM upstream registered by litellm-init, so the upstream guard
    must not reject it."""
    v = _validator(env_with_overrides({}))
    assert v._validate_litellm_has_upstream({
        "LLM_PROVIDER_SOURCE": "none",
        "VLLM_METAL_SOURCE": "managed-localhost",
    }) is True

    # No engine, no cloud, no vLLM Metal → LiteLLM has nothing to route.
    v.validation_errors = []
    assert v._validate_litellm_has_upstream({
        "LLM_PROVIDER_SOURCE": "none",
        "VLLM_METAL_SOURCE": "disabled",
    }) is False
    assert any("no upstream" in e for e in v.validation_errors)


def test_cloud_key_auto_disable_write_failure_is_validation_error(tmp_path, monkeypatch):
    from utils.source_override_manager import SourceOverrideManager

    env = tmp_path / ".env"
    env.write_text(
        "LLM_PROVIDER_SOURCE=none\n"
        "CLOUD_OPENAI_SOURCE=enabled\n"
        "OPENAI_API_KEY=\n",
        encoding="utf-8",
    )

    cp = ConfigParser(str(REPO_ROOT))
    cp.env_file_path = env
    validator = SourceValidator(config_parser=cp)

    def fail_update(self, overrides):
        return False

    monkeypatch.setattr(SourceOverrideManager, "update_env_file", fail_update)

    assert validator._enforce_cloud_keys_present() is False
    assert any("Could not persist cloud-provider" in e for e in validator.validation_errors)


def test_retired_source_cleanup_write_failure_stops_repair(tmp_path, monkeypatch):
    import services.source_validator as source_validator_module

    env = tmp_path / ".env"
    env.write_text("XTTS_ENDPOINT=http://retired\n", encoding="utf-8")
    validator = _validator(env)

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(source_validator_module, "atomic_write_text", fail_write)

    assert validator._migrate_legacy_tts_stt_sources() is False
    assert any("retired TTS/STT" in e for e in validator.validation_errors)


def test_a_selected_options_requires_vars_must_be_set():
    """`requires:` was parsed and documented but never enforced."""
    from types import SimpleNamespace

    from services.source_validator import _missing_option_requires

    sources = SimpleNamespace(var="FOO_SOURCE", options=[
        SimpleNamespace(id="cloud", requires=["FOO_API_KEY"]),
        SimpleNamespace(id="disabled", requires=[]),
    ])
    assert _missing_option_requires(sources, {"FOO_SOURCE": "cloud"}, {"FOO_API_KEY": " "}) == [
        "❌ FOO_SOURCE=cloud requires FOO_API_KEY to be set in .env."]
    assert _missing_option_requires(sources, {"FOO_SOURCE": "cloud"}, {"FOO_API_KEY": "k"}) == []
    assert _missing_option_requires(sources, {"FOO_SOURCE": "disabled"}, {}) == []
    assert _missing_option_requires(None, {}, {}) == []


def test_validate_all_sources_enforces_a_selected_options_requires(env_with_overrides, monkeypatch):
    """The helper above was tested but its wiring was not: making the check
    a no-op left the whole suite green."""
    from types import SimpleNamespace

    import services.manifests as manifests_module

    validator = _validator(env_with_overrides({"COMFYUI_SOURCE": "container-cpu"}))
    real = manifests_module.load_manifests
    probe = SimpleNamespace(sources=SimpleNamespace(var="COMFYUI_SOURCE", options=[
        SimpleNamespace(id="container-cpu", requires=["ATLAS_TEST_REQUIRED_KEY"]),
    ]))
    assert validator.load_yaml_config()  # the synthesized config, from the real manifests
    monkeypatch.setattr(validator, "load_yaml_config", lambda: True)
    monkeypatch.setattr(manifests_module, "load_manifests", lambda *a, **k: [*real(*a, **k), probe])
    assert validator.validate_all_sources() is False
    assert any("ATLAS_TEST_REQUIRED_KEY" in error for error in validator.validation_errors), validator.validation_errors
