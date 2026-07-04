from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "services" / "backend"
MANIFEST = BACKEND / "service.yml"
COMPOSE = BACKEND / "compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_backend_upload_and_cors_knobs_are_manifested_and_composed() -> None:
    env_vars = {entry["name"]: entry for entry in _manifest()["env"]}
    backend_env = _compose()["services"]["backend"]["environment"]

    assert env_vars["MAX_UPLOAD_BYTES"]["default"] == 104857600
    assert env_vars["BACKEND_CORS_ORIGINS"]["default"] == "*"
    assert env_vars["BACKEND_CORS_ALLOW_ORIGIN_REGEX"]["default"] == ""

    assert backend_env["MAX_UPLOAD_BYTES"] == "${MAX_UPLOAD_BYTES:-104857600}"
    assert backend_env["BACKEND_CORS_ORIGINS"] == "${BACKEND_CORS_ORIGINS:-*}"
    assert backend_env["BACKEND_CORS_ALLOW_ORIGIN_REGEX"] == "${BACKEND_CORS_ALLOW_ORIGIN_REGEX:-}"

    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "MAX_UPLOAD_BYTES=104857600" in env_example
    assert "BACKEND_CORS_ORIGINS=*" in env_example
    assert "BACKEND_CORS_ALLOW_ORIGIN_REGEX=" in env_example
