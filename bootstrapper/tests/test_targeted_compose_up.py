"""#504: `compose up` targets only enabled services from the rendered projection.

Compose evaluates/builds local `build:` images for the whole assembled graph
before honoring zero replicas — so a broken build for a disabled service
(asset-baker's 403'ing Blender download) aborted unrelated track bring-ups.
The enabled target set is derived from `docker compose config --format json`
(the resolved configuration: env scales, tracks, overrides, consumer overlays
all applied) — never a hand-maintained allowlist — and passed to `up`/`build`.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _manager(monkeypatch, config_payload, config_rc=0):
    from core.docker_manager import DockerManager
    import core.docker_manager as dm_module

    manager = DockerManager(str(REPO_ROOT))
    monkeypatch.setattr(
        manager, "detect_docker_compose_command", lambda: "docker compose"
    )
    calls: list[list[str]] = []

    class Result:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        if "config" in cmd:
            return Result(config_rc, json.dumps(config_payload))
        return Result(0, "")

    monkeypatch.setattr(dm_module.subprocess, "run", fake_run)
    return manager, calls


_PROJECTION = {
    "services": {
        # AC: enabled local-build service → included.
        "backend": {"build": {"context": "x"}, "deploy": {"replicas": 1}},
        # AC: disabled local-build service with no image → excluded.
        "asset-baker": {"build": {"context": "y"}, "deploy": {"replicas": 0}},
        # No deploy block at all → enabled by default.
        "kong-api-gateway": {"image": "kong:3.9"},
        # AC: explicit out-of-track override enables a service → the rendered
        # projection already reflects it (replicas 1) → included.
        "comfyui": {"build": {"context": "z"}, "deploy": {"replicas": 1}},
        "n8n": {"image": "n8n", "deploy": {"replicas": 0}},
    }
}


def test_enabled_targets_derived_from_rendered_projection(monkeypatch):
    manager, _ = _manager(monkeypatch, _PROJECTION)
    targets = manager.enabled_service_targets()
    assert targets == ["backend", "comfyui", "kong-api-gateway"]


def test_start_services_passes_only_enabled_targets(monkeypatch):
    """The `up` argv carries the enabled set — Compose then plans builds only
    for those services (+ their depends_on companions, added by Compose)."""
    manager, calls = _manager(monkeypatch, _PROJECTION)
    assert manager.start_services(detached=True, wait=True) == 0
    up_cmd = next(c for c in calls if "up" in c)
    assert "backend" in up_cmd and "comfyui" in up_cmd and "kong-api-gateway" in up_cmd
    assert "asset-baker" not in up_cmd
    assert "n8n" not in up_cmd
    # flags preserved
    for flag in ("-d", "--force-recreate", "--wait"):
        assert flag in up_cmd


def test_start_services_fails_open_when_projection_unavailable(monkeypatch):
    """AC/safety: a projection failure must fall back to the historical
    full-graph `up` (never LESS available than before the optimization)."""
    manager, calls = _manager(monkeypatch, {}, config_rc=1)
    assert manager.start_services(detached=True) == 0
    up_cmd = next(c for c in calls if "up" in c)
    # no service names appended — full graph
    assert up_cmd[-1] == "--force-recreate"


def test_build_services_targets_enabled_set(monkeypatch):
    """Cold start builds only enabled services' images."""
    manager, calls = _manager(monkeypatch, _PROJECTION)
    targets = manager.enabled_service_targets()
    assert manager.build_services(no_cache=True, services=targets) == 0
    build_cmd = next(c for c in calls if "build" in c)
    assert "backend" in build_cmd and "asset-baker" not in build_cmd
    assert "--no-cache" in build_cmd


def test_disabled_exclusions_are_logged_for_debugging(monkeypatch):
    """AC: startup output makes the selected target set inspectable."""
    manager, _ = _manager(monkeypatch, _PROJECTION)
    lines: list[str] = []
    manager.set_command_echo_callback(lines.append)
    manager.enabled_service_targets()
    joined = "\n".join(lines)
    assert "asset-baker" in joined and "disabled" in joined


def test_all_call_sites_thread_the_target_set():
    """Structural guard: the three `up` call sites (DockerManager.start_services,
    the cold-start path in start.py, the TUI launch in wizard_screen.py) all
    consult enabled_service_targets — a regression at any site reintroduces
    whole-graph build planning."""
    dm_src = (REPO_ROOT / "bootstrapper" / "core" / "docker_manager.py").read_text()
    start_src = (REPO_ROOT / "bootstrapper" / "start.py").read_text()
    wizard_src = (
        REPO_ROOT / "bootstrapper" / "ui" / "textual" / "screens" / "wizard_screen.py"
    ).read_text()
    assert "def enabled_service_targets" in dm_src
    assert dm_src.count("enabled_service_targets()") >= 1  # start_services
    assert "enabled_service_targets()" in start_src        # cold path
    assert "enabled_service_targets" in wizard_src         # TUI launch
