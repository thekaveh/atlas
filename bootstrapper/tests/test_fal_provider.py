from __future__ import annotations

from pathlib import Path

import yaml

from core.config_parser import ConfigParser
from services.source_validator import SourceValidator
from tracks import is_in_track, load_tracks
from utils.source_override_manager import SourceOverrideManager


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
MANIFEST = SERVICES / "fal" / "service.yml"


def _manifest() -> dict:
    assert MANIFEST.exists(), "FAL provider must have a first-class service manifest"
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _validator(env_path: Path) -> SourceValidator:
    cp = ConfigParser(str(ROOT))
    cp.env_file_path = env_path
    return SourceValidator(config_parser=cp)


def test_fal_provider_manifest_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "fal"
    assert manifest["virtual"] is True
    assert manifest["containers"] == []
    assert manifest["category"] == "media"
    assert manifest["docs"] == "services/fal/README.md"
    assert manifest["sources"]["var"] == "FAL_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert manifest["sources"]["options"] == [
        {"id": "enabled", "label": "Enabled (fal.ai cloud media APIs)"},
        {"id": "disabled", "label": "Disabled"},
    ]

    env = {entry["name"]: entry for entry in manifest["env"]}
    assert env["FAL_SOURCE"]["default"] == "disabled"
    assert env["FAL_API_KEY"]["secret"] is True
    assert env["FAL_MODEL"]["default"] == "fal-ai/flux/dev"
    assert env["FAL_TIMEOUT_SECONDS"]["default"] == 120
    assert env["FAL_OUTPUT_FORMAT"]["default"] == "jpeg"
    assert env["FAL_ENABLE_SAFETY_CHECKER"]["default"] is True
    assert manifest["data_flow"]["calls"] == []


def test_fal_provider_env_example_track_and_cli_contract() -> None:
    registry = load_tracks()
    manager = SourceOverrideManager(ConfigParser(str(ROOT)))
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    start_py = (ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")

    assert is_in_track(
        registry.by_key["gen-ai-creative"],
        "fal",
        always_on=registry.always_on,
    ) is True
    assert is_in_track(
        registry.by_key["gen-ai-rag"],
        "fal",
        always_on=registry.always_on,
    ) is False
    assert manager.source_mapping["fal_source"] == "FAL_SOURCE"
    assert manager.collect_overrides(fal_source="enabled") == {"FAL_SOURCE": "enabled"}
    assert "@click.option('--fal-source'" in start_py
    assert "'fal_source': fal_source" in start_py

    for expected in (
        "FAL_SOURCE=disabled",
        "FAL_API_KEY=",
        "FAL_MODEL=fal-ai/flux/dev",
        "FAL_TIMEOUT_SECONDS=120",
        "FAL_OUTPUT_FORMAT=jpeg",
        "FAL_ENABLE_SAFETY_CHECKER=true",
    ):
        assert expected in env_example


def test_fal_source_requires_key_only_when_enabled(env_with_overrides) -> None:
    missing_key = _validator(env_with_overrides({
        "FAL_SOURCE": "enabled",
        "FAL_API_KEY": "",
    }))

    assert missing_key.validate_all_sources() is False
    assert any("FAL_API_KEY" in error for error in missing_key.get_validation_errors())

    disabled = _validator(env_with_overrides({
        "FAL_SOURCE": "disabled",
        "FAL_API_KEY": "",
    }))
    assert disabled.validate_all_sources() is True
    assert disabled.get_validation_errors() == []
