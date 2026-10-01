"""
Shared pytest fixtures for bootstrapper tests.

A `manifest_factory` helper writes valid/invalid service.yml files into a tmp
directory mirroring the services/<name>/ shape. Tests use it to construct
arbitrary fixture trees on the fly without committing YAML files to the repo.
"""

from __future__ import annotations

import os
from pathlib import Path
import shlex
from typing import Any

import pytest
import yaml


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
