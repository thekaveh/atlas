"""Ray's command-submission API must have a generated backend credential."""

from __future__ import annotations

from pathlib import Path

import yaml

from utils.key_generator import KeyGenerator


REPO = Path(__file__).resolve().parents[2]


def test_ray_job_api_token_is_generated_and_preserved(tmp_path):
    env = tmp_path / ".env"
    env.write_text("RAY_JOB_API_TOKEN=\n", encoding="utf-8")
    generator = KeyGenerator(str(tmp_path))

    first = generator.generate_missing_keys()
    token = generator.get_current_env_value("RAY_JOB_API_TOKEN")
    second = generator.generate_missing_keys()

    assert first["RAY_JOB_API_TOKEN"] is True
    assert token.startswith("sk-ray-job-")
    assert generator.get_current_env_value("RAY_JOB_API_TOKEN") == token
    assert "RAY_JOB_API_TOKEN" not in second


def test_ray_job_api_token_is_declared_and_injected():
    manifest = yaml.safe_load(
        (REPO / "services/backend/service.yml").read_text(encoding="utf-8")
    )
    names = {entry["name"] for entry in manifest["env"]}
    compose = (REPO / "services/backend/compose.yml").read_text(encoding="utf-8")

    assert "RAY_JOB_API_TOKEN" in names
    assert "RAY_JOB_API_TOKEN: ${RAY_JOB_API_TOKEN" in compose


def test_native_ray_ports_are_loopback_only():
    compose = yaml.safe_load(
        (REPO / "services/ray/compose.yml").read_text(encoding="utf-8")
    )
    ports = compose["services"]["ray-head"]["ports"]

    assert ports
    assert all(str(port).startswith("127.0.0.1:") for port in ports)
