"""Regression coverage for the opt-in SigLIP 2 multi2vec-clip path."""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEAVIATE_MANIFEST = REPO_ROOT / "services" / "weaviate" / "service.yml"
MULTI2VEC_README = REPO_ROOT / "services" / "multi2vec-clip" / "README.md"
WEAVIATE_README = REPO_ROOT / "services" / "weaviate" / "README.md"
SOURCE_CONFIG_DOC = REPO_ROOT / "docs" / "deployment" / "source-configuration.md"
SIGLIP_RESEARCH = REPO_ROOT / "docs" / "research" / "candidates" / "siglip2-vectorizer.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

DEFAULT_CLIP_IMAGE = (
    "semitechnologies/multi2vec-clip:"
    "sentence-transformers-clip-ViT-B-32-1.5.1"
)
SIGLIP2_OPT_IN_IMAGE = (
    "semitechnologies/multi2vec-clip:"
    "google-siglip2-so400m-patch16-512-1.5.1"
)


def _load_weaviate_manifest() -> dict:
    return yaml.safe_load(WEAVIATE_MANIFEST.read_text())


def test_siglip2_image_is_opt_in_and_default_clip_image_is_unchanged() -> None:
    manifest = _load_weaviate_manifest()
    images = {entry["var"]: entry for entry in manifest["images"]}
    env = {entry["name"]: entry for entry in manifest["env"]}

    assert images["MULTI2VEC_CLIP_IMAGE"]["default"] == DEFAULT_CLIP_IMAGE
    assert env["MULTI2VEC_CLIP_SOURCE"]["default"] == "container-cpu"
    assert env["MULTI2VEC_CLIP_SIGLIP2_IMAGE"]["default"] == SIGLIP2_OPT_IN_IMAGE
    assert "opt-in" in env["MULTI2VEC_CLIP_SIGLIP2_IMAGE"]["description"].lower()
    assert "1152" in env["MULTI2VEC_CLIP_SIGLIP2_IMAGE"]["description"]


def test_env_example_surfaces_siglip2_reference_without_switching_runtime_image() -> None:
    env_example = ENV_EXAMPLE.read_text()

    assert f"MULTI2VEC_CLIP_IMAGE={DEFAULT_CLIP_IMAGE}" in env_example
    assert f"MULTI2VEC_CLIP_SIGLIP2_IMAGE={SIGLIP2_OPT_IN_IMAGE}" in env_example
    assert f"MULTI2VEC_CLIP_IMAGE={SIGLIP2_OPT_IN_IMAGE}" not in env_example


def test_docs_capture_siglip2_migration_guardrails() -> None:
    docs = "\n".join(
        [
            MULTI2VEC_README.read_text(),
            WEAVIATE_README.read_text(),
            SOURCE_CONFIG_DOC.read_text(),
        ]
    ).lower()

    assert SIGLIP2_OPT_IN_IMAGE.lower() in docs
    assert "multi2vec_clip_siglip2_image" in docs
    assert "clip_inference_api=http://multi2vec-clip:8080" in docs
    assert "multi2vec_clip_source=container-gpu" in docs
    assert "1152" in docs
    assert "revector" in docs
    assert "do not change" in docs or "do not silently change" in docs
    assert "existing collections" in docs


def test_research_note_matches_current_weaviate_siglip2_image_family() -> None:
    text = SIGLIP_RESEARCH.read_text()

    assert "google-siglip2-so400m-patch16-512" in text
    assert "1152" in text
    assert "multi2vec-clip-google-siglip2-base-patch16-512" not in text
    assert "512 → 768" not in text
