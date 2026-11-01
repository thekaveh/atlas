"""
Shared pytest fixtures for bootstrapper tests.

A `manifest_factory` helper writes valid/invalid service.yml files into a tmp
directory mirroring the services/<name>/ shape. Tests use it to construct
arbitrary fixture trees on the fly without committing YAML files to the repo.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import signal
from typing import Any

import pytest
import yaml


def _ignore_in_pytest_only(signum, frame):
    """Stand-in for an inherited SIG_IGN on HUP/INT.

    ``nohup`` and ``cmd &`` start pytest with these ignored, and exec keeps an
    ignored signal ignored, so the signal probes' traps never fire. exec resets
    a Python handler to SIG_DFL, so probes can trap again while pytest still
    ignores the signal."""


def pytest_configure(config):
    for sig in (signal.SIGHUP, signal.SIGINT):
        if signal.getsignal(sig) is signal.SIG_IGN:
            signal.signal(sig, _ignore_in_pytest_only)


@pytest.fixture(autouse=True)
def _no_real_docker_in_redis_aof_doctor(monkeypatch):
    """Keep the redis-aof doctor probe off the host Docker daemon (#1343).

    Whole-doctor tests run every registered check with PROJECT_NAME=atlas, so
    a real probe would inspect whatever ``atlas-redis-data`` volume this host
    has. Tests that exercise the probe replace ``start._docker_text`` again."""
    monkeypatch.setattr(
        "start._docker_text", lambda args, timeout: (-1, "docker disabled in tests")
    )


@pytest.fixture(autouse=True)
def _no_real_project_stop(monkeypatch):
    """Never let a test stop the developer's real stack.

    ``handle_port_configuration`` stops the project when its containers run
    and the port block moves; a test driving it with a real DockerManager
    ran ``docker compose ... down`` against the repo's .env (2026-10-08 run,
    cycle 7). Tests that exercise that branch set the probe themselves."""
    monkeypatch.setattr(
        "core.docker_manager.DockerManager.are_project_containers_running", lambda self: False
    )


@pytest.fixture(autouse=True)
def _no_inherited_env_file_selection(monkeypatch):
    """Never let a test resolve or rewrite the operator's selected env file.

    ConfigParser prefers an exported ATLAS_ENV_FILE or GENAI_ENV_FILE over
    ``root/.env``, so writer tests seeded in tmp_path rewrote the shell's real
    file: Supabase JWT keys and DB passwords, with no backup (2026-10-31 run).
    The overlay and consumer-manifest selectors are cleared for hermetic reads.
    A test that needs one of these sets it itself."""
    for name in (
        "ATLAS_ENV_FILE",
        "GENAI_ENV_FILE",
        "ATLAS_ENV_USER_FILE",
        "ATLAS_CONSUMER_MANIFEST",
        "_ATLAS_CONSUMER_MANIFEST_CLI",
    ):
        monkeypatch.delenv(name, raising=False)


_ENV_EXAMPLE_KEYS = frozenset(re.findall(
    r"^([A-Z_][A-Z0-9_]*)=",
    (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8"),
    re.M,
))


@pytest.fixture(autouse=True)
def _no_inherited_operator_settings(monkeypatch):
    """Clear exported ``.env.example`` variables from the test environment.

    An exported value wins over the env file in
    ``core/port_manager.py::_host_bind_ip``, ``utils/system.py::compose_env``
    and docker compose interpolation. An operator's shell (HOST_BIND_IP,
    PROJECT_NAME, REDIS_PASSWORD) therefore changed test results. A test that
    needs one of these sets it itself."""
    for name in _ENV_EXAMPLE_KEYS & os.environ.keys():
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _private_session_log_store(monkeypatch, tmp_path_factory):
    """Keep WizardScreen's launch-log tee out of the developer's real store.

    Building a real WizardScreen opens a session-log tee in the default
    per-user store, and its retention pass deletes the oldest real
    ``./start.sh`` launch logs. Tests that patch ``tempfile.gettempdir`` or
    pass an explicit directory still get the store they asked for."""
    import tempfile

    from ui import session_log_retention as retention

    real_resolve, real_gettempdir = retention.resolve_store, tempfile.gettempdir
    root = tmp_path_factory.getbasetemp() / "session-log-parent"
    root.mkdir(exist_ok=True)

    def resolve(parent):
        if not parent and tempfile.gettempdir is real_gettempdir:
            parent = root
        return real_resolve(parent)

    monkeypatch.setattr(retention, "resolve_store", resolve)


@pytest.fixture
def forbid_host_side_effects(monkeypatch):
    """Fail the test if it builds the real starter or config parser, spawns
    a process, or opens a socket.

    Tests that drive host diagnostics through explicit doubles request this
    to prove nothing private on the developer's machine is read or reached.
    """
    import socket
    import subprocess

    import start
    from core.config_parser import ConfigParser

    def forbidden(*args, **kwargs):
        raise AssertionError("Use explicit doubles, not real host config, processes or network")

    for owner, name in (
        (start.AtlasStarter, "__init__"),
        (ConfigParser, "__init__"),
        (ConfigParser, "parse_env_file"),
        (subprocess, "Popen"),
        (socket.socket, "connect"),
        (socket.socket, "bind"),
        (socket, "getaddrinfo"),
    ):
        monkeypatch.setattr(owner, name, forbidden)


@pytest.fixture
def services_root(tmp_path: Path) -> Path:
    """An empty services/ root inside tmp_path."""
    root = tmp_path / "services"
    root.mkdir()
    return root


@pytest.fixture
def write_manifest(services_root: Path):
    """Factory: write a services/<name>/service.yml from a Python dict."""

    def _write(name: str, data: dict[str, Any], *, folder_name: str | None = None) -> Path:
        folder = services_root / (folder_name or name)
        folder.mkdir(parents=True, exist_ok=True)
        manifest_path = folder / "service.yml"
        manifest_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return manifest_path

    return _write


@pytest.fixture
def minimal_manifest_dict():
    """A minimal-but-valid manifest dict (used as a base in tests)."""

    def _make(name: str = "redis") -> dict[str, Any]:
        return {
            "name": name,
            "label": f"{name.capitalize()} service",
            "category": "data",
            "containers": [name],
            "capabilities": [
                {
                    "name": "Synthetic service contract",
                    "status": "supported",
                    "verification": "tested",
                    "note": "Tests exercise this synthetic manifest contract.",
                }
            ],
            "env": [
                {"name": f"{name.upper()}_PORT", "default": 6379, "description": "Host port."},
            ],
        }

    return _make


@pytest.fixture
def full_manifest_dict():
    """A manifest exercising every optional field."""

    def _make(name: str = "ollama") -> dict[str, Any]:
        return {
            "name": name,
            "label": "Ollama (local LLM engine)",
            "category": "llm",
            "docs": f"services/{name}/README.md",
            "containers": ["ollama", "ollama-pull"],
            "capabilities": [
                {
                    "name": "Synthetic service contract",
                    "status": "supported",
                    "verification": "tested",
                    "note": "Tests exercise this synthetic manifest contract.",
                }
            ],
            "images": [
                {
                    "var": "LLM_PROVIDER_IMAGE",
                    "default": "ollama/ollama:0.30.11",
                    "container": "ollama",
                    "notes": "Used for container-cpu and container-gpu sources.",
                },
                {
                    "var": "OLLAMA_PULL_IMAGE",
                    "default": "alpine/curl:latest",
                    "container": "ollama-pull",
                },
            ],
            "sources": {
                "var": "LLM_PROVIDER_SOURCE",
                "default": "ollama-container-cpu",
                "options": [
                    {
                        "id": "ollama-container-cpu",
                        "label": "Container (CPU)",
                    },
                    {
                        "id": "ollama-localhost",
                        "label": "Host (existing Ollama)",
                        "requires": ["OLLAMA_LOCALHOST_PORT"],
                    },
                ],
            },
            # Runtime data (was sources.options[].effects in the old shape;
            # `runtime_sc` is the operational source the bootstrapper reads).
            "runtime_sc": {
                "llm_provider": {
                    "ollama-container-cpu": {
                        "scale": 1,
                        "environment": {
                            "OLLAMA_SCALE": 1,
                            "OLLAMA_ENDPOINT": "http://ollama:11434",
                        },
                        "deploy": {},
                        "extra_hosts": [],
                    },
                    "ollama-localhost": {
                        "scale": 0,
                        "environment": {
                            "OLLAMA_SCALE": 0,
                            "OLLAMA_ENDPOINT": "http://host.docker.internal:${OLLAMA_LOCALHOST_PORT:-11434}",
                        },
                        "deploy": {},
                        "extra_hosts": ["host.docker.internal:host-gateway"],
                    },
                },
            },
            "env": [
                {"name": "LLM_PROVIDER_SOURCE", "default": "ollama-container-cpu"},
                {"name": "OLLAMA_LOCALHOST_PORT", "default": "11434", "description": "Host Ollama port."},
                {"name": "OLLAMA_SCALE", "auto_managed": True, "description": "Computed."},
                {"name": "OLLAMA_ENDPOINT", "auto_managed": True},
            ],
            "depends_on": {
                "required": [],
                "optional": [],
            },
            "exports": [
                {"name": "OLLAMA_ENDPOINT", "consumers": ["litellm", "weaviate"]},
            ],
        }

    return _make


@pytest.fixture
def env_with_overrides(tmp_path):
    """Factory: copy .env.example to a tmp .env with KEY=value overrides
    spliced in-place (appended when the key is absent). Returns the path.

    Extracted from the identical 15-line loop previously duplicated in
    four test files (compose-profiles, n8n-scale, lightrag/tei
    permutations, lightrag adaptation).
    """
    repo_root = Path(__file__).resolve().parents[2]
    env_example = repo_root / ".env.example"

    def _build(overrides: dict, filename: str = ".env") -> Path:
        env_path = tmp_path / filename
        text = env_example.read_text(encoding="utf-8")
        out, replaced = [], set()
        for line in text.splitlines():
            key = line.split("=", 1)[0] if "=" in line else None
            if key in overrides and key not in replaced:
                out.append(f"{key}={overrides[key]}")
                replaced.add(key)
            else:
                out.append(line)
        for var, val in overrides.items():
            if var not in replaced:
                out.append(f"{var}={val}")
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return env_path

    return _build


@pytest.fixture
def dead_pid() -> int:
    """See _provably_dead_pid."""
    return _provably_dead_pid()


def _provably_dead_pid() -> int:
    """A PID that is definitely not running (#1237).

    Tests used the literal 4242 and assumed nothing owned it. On a loaded CI
    runner a real process does, so the managed-host removal guard correctly
    refuses to erase state and the test fails for an unrelated reason. Spawn
    a trivial child, reap it, then confirm the PID is free.
    """
    import os
    import subprocess
    import sys

    for _ in range(10):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        try:
            os.kill(proc.pid, 0)
        except ProcessLookupError:
            return proc.pid
    raise AssertionError("could not obtain a dead PID after 10 attempts")


# What one fake role-drill ``docker run`` prints and how it ends, as
# (fd, text, then). The stall text is verbatim from the postgres:15.18-alpine
# psql client (#1306).
_FAKE_PSQL_OUTCOMES = {
    "ok": (1, "1", "exit 0"),
    "denied": (2, "ERROR:  permission denied for table task3", "exit 1"),
    "connect": (
        2,
        'psql: error: connection to server at "supabase-db" (172.18.0.2), '
        "port 5432 failed: timeout expired",
        "exit 2",
    ),
    "lock": (2, "ERROR:  canceling statement due to lock timeout", "exit 1"),
    "statement": (2, "ERROR:  canceling statement due to statement timeout", "exit 1"),
    "stall": (2, "partial output", "exec sleep 30"),
}
_FAKE_ROLE_CLIENT_DOCKER = """#!/bin/sh
[ "$1" = run ] || {{ [ "$1" = container ] && exit 1; exit 0; }}
printf '%s\\n' "$*" >>"{runs}"
case "$(wc -l <"{runs}" | tr -d ' ')" in
{arms}  *) echo 'unscripted docker run' >&2; exit 99 ;;
esac
"""


@pytest.fixture
def fake_role_client_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Factory: put a fake ``docker`` for role-drill psql clients on PATH.

    The Nth ``docker run`` replays the Nth scripted outcome (a key of
    ``_FAKE_PSQL_OUTCOMES``) and logs its arguments, one line per client, to
    the returned file. Every other docker call is a cleanup probe that finds
    no leftover container.
    """

    def _install(*outcomes: str) -> Path:
        fake_bin = tmp_path / "fake-docker-bin"
        fake_bin.mkdir()
        runs = tmp_path / "docker-runs"
        arms = "".join(
            f"  {index}) echo {shlex.quote(text)} >&{fd}; {then} ;;\n"
            for index, (fd, text, then) in enumerate(
                (_FAKE_PSQL_OUTCOMES[outcome] for outcome in outcomes), 1
            )
        )
        fake_docker = fake_bin / "docker"
        fake_docker.write_text(
            _FAKE_ROLE_CLIENT_DOCKER.format(runs=runs, arms=arms), encoding="utf-8"
        )
        fake_docker.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
        return runs

    return _install
