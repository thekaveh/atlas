"""COMPOSE_PROFILES stacking across STT/TTS/doc-processor generators.

Regression guard for the doc-processor clobber bug: the docling-gpu branch
used to rebuild COMPOSE_PROFILES from service_sources (which never holds
that key) instead of stacking onto the shared running tally, wiping any
speaches/parakeet/chatterbox profile added before it. Also pins the
stale-clearing contract: the pipeline always emits COMPOSE_PROFILES (empty
when no profile-gated source is active) so update_env_file() can't preserve
a profile from a since-disabled source.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from core.config_parser import ConfigParser
from services.service_config import ServiceConfig


REPO_ROOT = Path(__file__).resolve().parents[2]


def _sc(env_path: Path) -> ServiceConfig:
    cp = ConfigParser(str(REPO_ROOT))
    cp.env_file_path = env_path
    sc = ServiceConfig(config_parser=cp)
    sc.localhost_host = "localhost"
    return sc


def _profiles(env: dict) -> set:
    return {p for p in env["COMPOSE_PROFILES"].split(",") if p}


def test_docling_gpu_stacks_on_stt_profile(env_with_overrides):
    sc = _sc(env_with_overrides({
        "STT_PROVIDER_SOURCE": "speaches-container-cpu",
        "TTS_PROVIDER_SOURCE": "disabled",
        "DOC_PROCESSOR_SOURCE": "docling-container-gpu",
    }))
    env = sc.generate_service_environment()
    assert _profiles(env) == {"speaches-cpu", "docling-gpu", "doc-gpu"}


def test_docling_gpu_stacks_on_stt_and_tts_profiles(env_with_overrides):
    sc = _sc(env_with_overrides({
        "STT_PROVIDER_SOURCE": "parakeet-container-gpu",
        "TTS_PROVIDER_SOURCE": "chatterbox-container-gpu",
        "DOC_PROCESSOR_SOURCE": "docling-container-gpu",
    }))
    env = sc.generate_service_environment()
    assert _profiles(env) == {
        "parakeet-gpu", "chatterbox-gpu", "docling-gpu", "doc-gpu",
    }


def test_docling_gpu_alone(env_with_overrides):
    sc = _sc(env_with_overrides({
        "STT_PROVIDER_SOURCE": "disabled",
        "TTS_PROVIDER_SOURCE": "disabled",
        "DOC_PROCESSOR_SOURCE": "docling-container-gpu",
    }))
    env = sc.generate_service_environment()
    assert _profiles(env) == {"docling-gpu", "doc-gpu"}


def test_all_disabled_emits_empty_profiles_key(env_with_overrides):
    """The key must be present (and empty) so stale .env values get cleared."""
    sc = _sc(env_with_overrides({
        "STT_PROVIDER_SOURCE": "disabled",
        "TTS_PROVIDER_SOURCE": "disabled",
        "DOC_PROCESSOR_SOURCE": "disabled",
    }))
    env = sc.generate_service_environment()
    assert env["COMPOSE_PROFILES"] == ""


def test_speaches_shared_by_stt_and_tts_not_duplicated(env_with_overrides):
    sc = _sc(env_with_overrides({
        "STT_PROVIDER_SOURCE": "speaches-container-cpu",
        "TTS_PROVIDER_SOURCE": "speaches-container-cpu",
        "DOC_PROCESSOR_SOURCE": "disabled",
    }))
    env = sc.generate_service_environment()
    assert env["COMPOSE_PROFILES"].split(",").count("speaches-cpu") == 1


def test_speaches_gpu_profile_selects_gpu_image(env_with_overrides):
    """The compose fragment interpolates ${SPEACHES_IMAGE} under BOTH
    profiles — when speaches-gpu wins, the generator must rewrite it to
    the CUDA build (regression: the gpu profile ran the CPU image)."""
    sc = _sc(env_with_overrides({
        "STT_PROVIDER_SOURCE": "speaches-container-gpu",
        "TTS_PROVIDER_SOURCE": "disabled",
        "DOC_PROCESSOR_SOURCE": "disabled",
    }))
    env = sc.generate_service_environment()
    assert "speaches-gpu" in env["COMPOSE_PROFILES"]
    assert "cuda" in env["SPEACHES_IMAGE"]


def test_speaches_cpu_profile_keeps_cpu_image(env_with_overrides):
    """CPU profile (or a gpu→cpu switch) resolves the CPU image — the
    pin refresher resets SPEACHES_IMAGE from the manifest each run, so a
    stale cuda value can't survive."""
    sc = _sc(env_with_overrides({
        "STT_PROVIDER_SOURCE": "speaches-container-cpu",
        "TTS_PROVIDER_SOURCE": "disabled",
        "DOC_PROCESSOR_SOURCE": "disabled",
        "SPEACHES_IMAGE": "ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cuda",  # stale
    }))
    env = sc.generate_service_environment()
    assert "speaches-cpu" in env["COMPOSE_PROFILES"]
    assert env["SPEACHES_IMAGE"].endswith("-cpu")


def test_speaches_gpu_image_shell_export_wins(env_with_overrides, monkeypatch):
    """The refresher's documented override path is a shell export —
    the gpu rewrite must consult os.environ first (regression: it read
    env_vars/.env only, so an exported pin silently lost — or, with no
    .env line either, the gpu profile fell back to the CPU image)."""
    monkeypatch.setenv("SPEACHES_GPU_IMAGE", "ghcr.io/example/speaches:custom-cuda")
    sc = _sc(env_with_overrides({
        "STT_PROVIDER_SOURCE": "speaches-container-gpu",
        "TTS_PROVIDER_SOURCE": "disabled",
        "DOC_PROCESSOR_SOURCE": "disabled",
    }))
    env = sc.generate_service_environment()
    assert env["SPEACHES_IMAGE"] == "ghcr.io/example/speaches:custom-cuda"


@pytest.mark.parametrize("comfyui_source, expected", [
    ("disabled", "false"), ("container-cpu", "true"),
])
def test_open_webui_image_generation_follows_comfyui_source(env_with_overrides, comfyui_source, expected):
    # No image toggle aimed at a ComfyUI host that does not exist.
    sc = _sc(env_with_overrides({"COMFYUI_SOURCE": comfyui_source}))
    assert sc.generate_service_environment()["OPEN_WEB_UI_ENABLE_IMAGE_GENERATION"] == expected


@pytest.mark.parametrize(("weaviate", "clip_scale"), [("container", "1"), ("localhost", "0"), ("disabled", "0")])
def test_clip_runs_only_beside_a_container_weaviate(tmp_path, weaviate, clip_scale):
    """Weaviate is CLIP's only consumer and CLIP has no host port: without a
    container Weaviate it loaded its model for nothing on every start."""
    from pathlib import Path

    from core.config_parser import ConfigParser
    from services.service_config import ServiceConfig

    repo = Path(__file__).resolve().parents[2]
    over = {"WEAVIATE_SOURCE": weaviate, "MULTI2VEC_CLIP_SOURCE": "container-cpu"}
    lines = [line for line in (repo / ".env.example").read_text().splitlines() if line.split("=", 1)[0] not in over]
    env_path = tmp_path / ".env"
    env_path.write_text("\n".join(lines + [f"{k}={v}" for k, v in over.items()]) + "\n")
    parser = ConfigParser(str(repo))
    parser.env_file_path = env_path
    config = ServiceConfig(parser)
    config.load_config()
    assert config.generate_service_environment()["CLIP_SCALE"] == clip_scale
