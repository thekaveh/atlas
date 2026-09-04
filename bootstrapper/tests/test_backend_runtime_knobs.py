"""Deployment contract for Backend runtime knobs consumed by RAG jobs."""

from __future__ import annotations

from pathlib import Path

import yaml

from services.env_assembler import assemble_env_example
from services.manifests import load_manifests


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_MANIFEST = REPO_ROOT / "services" / "backend" / "service.yml"
BACKEND_COMPOSE = REPO_ROOT / "services" / "backend" / "compose.yml"
CELERY_COMPOSE = REPO_ROOT / "services" / "celery" / "compose.yml"
BACKEND_README = REPO_ROOT / "services" / "backend" / "README.md"
CELERY_README = REPO_ROOT / "services" / "celery" / "README.md"

EXPECTED = {
    "CHONKIE_SEMANTIC_EMBEDDING_MODEL": "minishlab/potion-base-32M",
    "LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS": 30,
    "RAG_INGESTION_TTL_SECONDS": 604800,
}


def test_backend_manifest_declares_supported_runtime_knobs() -> None:
    manifest = yaml.safe_load(BACKEND_MANIFEST.read_text(encoding="utf-8"))
    env = {entry["name"]: entry for entry in manifest["env"]}

    for name, default in EXPECTED.items():
        assert env[name]["default"] == default
        assert env[name].get("secret") is not True
        assert env[name]["description"]

    assert "non-empty" in env["CHONKIE_SEMANTIC_EMBEDDING_MODEL"]["description"].lower()
    assert "3600" in env["LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS"]["description"]
    assert "60" in env["RAG_INGESTION_TTL_SECONDS"]["description"]
    assert "31536000" in env["RAG_INGESTION_TTL_SECONDS"]["description"]


def test_env_example_exposes_supported_runtime_knobs() -> None:
    assembled = assemble_env_example(load_manifests(REPO_ROOT / "services"))

    for name, default in EXPECTED.items():
        assert f"{name}={default}" in assembled


def test_backend_and_celery_worker_receive_the_same_runtime_knobs() -> None:
    backend = yaml.safe_load(BACKEND_COMPOSE.read_text(encoding="utf-8"))["services"][
        "backend"
    ]["environment"]
    worker = yaml.safe_load(CELERY_COMPOSE.read_text(encoding="utf-8"))["services"][
        "celery-worker"
    ]["environment"]

    for name, default in EXPECTED.items():
        interpolation = f"${{{name}:-{default}}}"
        assert backend[name] == interpolation
        assert worker[name] == interpolation

    flower = yaml.safe_load(CELERY_COMPOSE.read_text(encoding="utf-8"))["services"][
        "flower"
    ]["environment"]
    assert EXPECTED.keys().isdisjoint(flower)


def test_backend_and_worker_docs_describe_shared_runtime_knobs() -> None:
    backend_docs = BACKEND_README.read_text(encoding="utf-8")
    worker_docs = CELERY_README.read_text(encoding="utf-8")

    for name in EXPECTED:
        assert name in backend_docs
        assert name in worker_docs
    assert "not surfaced in `.env.example`" not in backend_docs
