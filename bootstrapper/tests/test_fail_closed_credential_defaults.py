"""Fail-closed credential defaults and manifest/compose parity (audit C1-01, C1-02, C1-08, C1-09, NEW jenkins).

- Compose must not fall back to a literal admin password (grafana, jenkins).
  An empty value fails the render instead of becoming a known credential.
- Grafana's runtime_sc container slice mirrors what compose sets.
- The jupyterhub startup script refuses an empty NEO4J_PASSWORD instead of
  writing a guessed one.
- The PROD_ENV_* reservation stays explicit and unread by compose.

The docker-rendered checks use only placeholder values and the committed
.env.example. They never read a user .env.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
COMPOSE = REPO_ROOT / "docker-compose.yml"
GRAFANA_COMPOSE = REPO_ROOT / "services" / "grafana" / "compose.yml"
GRAFANA_MANIFEST = REPO_ROOT / "services" / "grafana" / "service.yml"
JENKINS_COMPOSE = REPO_ROOT / "services" / "jenkins" / "compose.yml"
JENKINS_MANIFEST = REPO_ROOT / "services" / "jenkins" / "service.yml"
STARTUP = REPO_ROOT / "services" / "jupyterhub" / "build" / "scripts" / "startup.sh"
GLOBALS_MANIFEST = REPO_ROOT / "services" / "globals" / "service.yml"

# runtime_sc keys the bootstrapper writes to .env. Compose does not forward them.
BOOTSTRAPPER_ONLY_GRAFANA_KEYS = {"GRAFANA_ENDPOINT"}

EXPECTED_PROD_ENV_RESERVATIONS = {
    "PROD_ENV_CPUS",
    "PROD_ENV_MEM_LIMIT",
    "PROD_ENV_BACKEND_CPUS",
    "PROD_ENV_BACKEND_MEM_LIMIT",
    "PROD_ENV_N8N_CPUS",
    "PROD_ENV_N8N_MEM_LIMIT",
    "PROD_ENV_COMFYUI_CPUS",
    "PROD_ENV_COMFYUI_MEM_LIMIT",
}

_requires_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not on PATH")
_requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")


def _compose_environment(path: Path, service: str) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc["services"][service]["environment"]


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value
    return values


@pytest.mark.parametrize(
    ("path", "service", "env_key", "source_var"),
    [
        (GRAFANA_COMPOSE, "grafana", "GF_SECURITY_ADMIN_PASSWORD", "GRAFANA_ADMIN_PASSWORD"),
        (JENKINS_COMPOSE, "jenkins", "JENKINS_ADMIN_PASSWORD", "JENKINS_ADMIN_PASSWORD"),
    ],
)
def test_admin_password_is_required_not_defaulted(path, service, env_key, source_var):
    """C1-01 / NEW jenkins: `${VAR:?...}` fails closed; `${VAR:-admin}` would not."""
    value = _compose_environment(path, service)[env_key]
    assert value == f"${{{source_var}:?{source_var} is required}}", value


@pytest.mark.parametrize("path", [GRAFANA_COMPOSE, JENKINS_COMPOSE])
def test_no_literal_admin_password_default_remains(path):
    text = path.read_text(encoding="utf-8")
    assert "PASSWORD:-admin" not in text
    assert "PASSWORD}:-" not in text


def test_grafana_runtime_sc_container_environment_mirrors_compose():
    """C1-02: the runtime slice declares the same keys and values compose sets."""
    manifest = yaml.safe_load(GRAFANA_MANIFEST.read_text(encoding="utf-8"))
    runtime = manifest["runtime_sc"]["grafana"]["container"]["environment"]
    compose = _compose_environment(GRAFANA_COMPOSE, "grafana")

    compared = set(runtime) - BOOTSTRAPPER_ONLY_GRAFANA_KEYS
    assert compared == set(compose), {
        "only_runtime": sorted(compared - set(compose)),
        "only_compose": sorted(set(compose) - compared),
    }
    mismatched = {
        key: (runtime[key], compose[key])
        for key in sorted(compared)
        if str(runtime[key]) != str(compose[key])
    }
    assert not mismatched


def test_jenkins_runtime_sc_admin_password_mirrors_compose():
    manifest = yaml.safe_load(JENKINS_MANIFEST.read_text(encoding="utf-8"))
    runtime = manifest["runtime_sc"]["jenkins"]["container"]["environment"]
    compose = _compose_environment(JENKINS_COMPOSE, "jenkins")
    assert runtime["JENKINS_ADMIN_PASSWORD"] == compose["JENKINS_ADMIN_PASSWORD"]


def _render_stack(overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """Render the full stack from .env.example with the given values replaced."""
    out: list[str] = []
    seen: set[str] = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if key and not stripped.startswith("#") and key in overrides:
            out.append(f"{key}={overrides[key]}\n")
            seen.add(key)
        else:
            out.append(line if line.endswith("\n") else line + "\n")
    out.extend(f"{key}={value}\n" for key, value in overrides.items() if key not in seen)

    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as handle:
        handle.writelines(out)
        env_file = Path(handle.name)
    try:
        # Minimal environment: host exports must not outrank the file under test.
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        return subprocess.run(
            [
                "docker", "compose",
                "--env-file", str(env_file),
                "-p", "atlas",
                "-f", str(COMPOSE),
                "config",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    finally:
        env_file.unlink(missing_ok=True)


@_requires_docker
def test_empty_grafana_admin_password_fails_the_rendered_stack():
    result = _render_stack({
        "GRAFANA_ADMIN_PASSWORD": "",
        "JENKINS_ADMIN_PASSWORD": "jenkins-test-placeholder",
    })
    assert result.returncode != 0
    assert "GRAFANA_ADMIN_PASSWORD is required" in result.stderr


@_requires_docker
def test_empty_jenkins_admin_password_fails_the_rendered_stack():
    result = _render_stack({
        "GRAFANA_ADMIN_PASSWORD": "grafana-test-placeholder",
        "JENKINS_ADMIN_PASSWORD": "",
    })
    assert result.returncode != 0
    assert "JENKINS_ADMIN_PASSWORD is required" in result.stderr


@_requires_docker
def test_set_admin_passwords_render_into_grafana_and_jenkins():
    """Rendered-config parity: the set value reaches the container, and the
    grafana root URL uses the Kong port from .env.example, as the runtime slice does."""
    result = _render_stack({
        "GRAFANA_ADMIN_PASSWORD": "grafana-test-placeholder",
        "JENKINS_ADMIN_PASSWORD": "jenkins-test-placeholder",
    })
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)["services"]
    grafana = rendered["grafana"]["environment"]
    assert grafana["GF_SECURITY_ADMIN_PASSWORD"] == "grafana-test-placeholder"
    kong_port = _env_example()["KONG_HTTP_PORT"]
    assert grafana["GF_SERVER_ROOT_URL"] == f"http://grafana.localhost:{kong_port}"
    assert rendered["jenkins"]["environment"]["JENKINS_ADMIN_PASSWORD"] == "jenkins-test-placeholder"


def test_jupyterhub_startup_has_no_literal_neo4j_fallback():
    text = STARTUP.read_text(encoding="utf-8")
    assert "NEO4J_PASSWORD:-password" not in text
    assert "\nNEO4J_PASSWORD=${NEO4J_PASSWORD}\n" in text


@_requires_bash
@pytest.mark.parametrize("neo4j_password", [None, ""])
def test_jupyterhub_startup_refuses_an_empty_neo4j_password(neo4j_password):
    """C1-08: an empty or missing value stops startup before any .env is written."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if neo4j_password is not None:
        env["NEO4J_PASSWORD"] = neo4j_password
    result = subprocess.run(
        ["bash", str(STARTUP)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )
    assert result.returncode != 0
    assert "NEO4J_PASSWORD is empty" in result.stderr
    assert "Environment file created" not in result.stdout


def test_prod_env_reservations_are_explicit_and_unread_by_compose():
    """C1-09: every PROD_ENV_* is documented as reserved, and no compose fragment reads it."""
    manifest = yaml.safe_load(GLOBALS_MANIFEST.read_text(encoding="utf-8"))
    prod_env = {
        entry["name"]: entry
        for entry in manifest["env"]
        if entry["name"].startswith("PROD_ENV_")
    }
    assert set(prod_env) == EXPECTED_PROD_ENV_RESERVATIONS
    for name, entry in prod_env.items():
        assert "reserved" in entry["description"].lower(), name

    readers = [
        fragment.relative_to(REPO_ROOT).as_posix()
        for fragment in sorted((REPO_ROOT / "services").glob("*/compose.yml"))
        if "PROD_ENV_" in fragment.read_text(encoding="utf-8")
    ]
    assert readers == []
