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
    monkeypatch.setattr(dm_module, "run_with_deadline", fake_run)
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


def test_projection_probe_has_a_total_deadline(monkeypatch):
    from core.docker_manager import DockerManager
    import core.docker_manager as dm_module

    manager = DockerManager(str(REPO_ROOT))
    monkeypatch.setattr(
        manager, "detect_docker_compose_command", lambda: "docker compose"
    )
    seen: dict[str, object] = {}

    def fake_run(_cmd, **kwargs):
        seen.update(kwargs)
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(dm_module, "run_with_deadline", fake_run)

    assert manager.enabled_service_targets() is None
    assert seen["timeout_seconds"] == dm_module._COMPOSE_PROBE_TIMEOUT_SECONDS


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
    assert up_cmd[-1] in {"--force-recreate", "--build"}
    assert not set(_PROJECTION["services"]).intersection(up_cmd)


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


def test_warm_up_sites_thread_source_build_args():
    """#506 structural guard: both WARM `up` call sites — DockerManager.
    start_services (linear) and the TUI launch in wizard_screen.py — thread
    `source_build_args()` so a normal start after an in-place source upgrade
    rebuilds stale local images, and record `mark_source_built()` on success.
    A regression at either site reintroduces the stale-image bug (backend runs
    old code after a submodule pin bump)."""
    dm_src = (REPO_ROOT / "bootstrapper" / "core" / "docker_manager.py").read_text()
    start_src = (REPO_ROOT / "bootstrapper" / "start.py").read_text()
    wizard_src = (
        REPO_ROOT / "bootstrapper" / "ui" / "textual" / "screens" / "wizard_screen.py"
    ).read_text()
    assert "def source_build_args" in dm_src
    assert "def mark_source_built" in dm_src
    # start_services (canonical warm path) consults the drift gate + records.
    assert "self.source_build_args(targets)" in dm_src
    assert "self.mark_source_built(targets)" in dm_src
    assert "capture_build_state(targets)" in start_src
    # TUI warm launch consults the drift gate + records.
    assert "prepare_build_args(cold, targets)" in wizard_src
    assert "mark_source_built(targets)" in wizard_src
    assert "def prepare_build_args" in dm_src


def test_linear_warm_start_threads_the_same_build_gate():
    """#989: the linear warm start now builds stale images itself, before
    `up`, instead of `up --build` in start_services. It must consult the same
    #506 drift gate and record the build state only after `up` succeeded."""
    import inspect

    import start as start_module

    warm = inspect.getsource(start_module.AtlasStarter._warm_compose_up)
    assert "prepare_build_args(False, targets)" in warm
    assert "mark_source_built(targets)" in warm
    assert warm.index("_compose_up(targets, wait)") < warm.index(
        "mark_source_built(targets)"
    )


# ── #989: which enabled images a cold start can go on without ─────────────
_BUILD_GRAPH = {
    # celery-worker depends on backend: backend's image is required.
    "backend": {"build": {"context": "b"}},
    "celery-worker": {
        "build": {"context": "c"},
        "image": "atlas-backend-celery:local",
        "depends_on": {"backend": {"condition": "service_healthy"}},
    },
    # Nothing enabled depends on jupyterhub (the #989 report).
    "jupyterhub": {"build": {"context": "j"}, "depends_on": {"litellm": {}}},
    # A disabled consumer of jupyterhub does not make it required.
    "jupyterhub-consumer": {"image": "x", "depends_on": {"jupyterhub": {}}},
    # Two services building one image are one unit, required through either.
    "spark-master": {"build": {"context": "s"}, "image": "atlas-spark:local"},
    "spark-worker": {
        "build": {"context": "s"},
        "image": "atlas-spark:local",
        "depends_on": {"spark-master": {}},
    },
    "litellm": {"image": "litellm"},
}


def test_build_split_keeps_images_enabled_services_depend_on_required():
    from start import _split_local_builds

    targets = [name for name in _BUILD_GRAPH if name != "jupyterhub-consumer"]
    required, optional = _split_local_builds(_BUILD_GRAPH, targets, set())

    assert required == ["backend", "spark-master", "spark-worker"]
    assert optional == [["celery-worker"], ["jupyterhub"]]


def test_build_split_keeps_the_always_running_core_required():
    """With celery-worker off nothing lists backend in depends_on, but the
    backend image belongs to the always-running core, so it stays required."""
    from start import _split_local_builds

    targets = ["backend", "jupyterhub", "litellm"]
    required, optional = _split_local_builds(_BUILD_GRAPH, targets, {"backend"})

    assert required == ["backend"]
    assert optional == [["jupyterhub"]]


def test_the_core_is_the_locked_tier_for_every_local_image():
    """The topology's ``locked`` rows (no source choice) are the core #989
    keeps required. Of every service that builds a local image, exactly the
    Backend and LiteLLM ones are locked, which is the documented
    always-running tier (Supabase, Kong, Redis, LiteLLM, Backend); a new
    locked local image must be reviewed here."""
    import yaml

    import start as start_module

    core = start_module.AtlasStarter()._always_running_services()
    local_builds = set()
    for fragment in sorted((REPO_ROOT / "services").glob("*/compose.yml")):
        services = (yaml.safe_load(fragment.read_text()) or {}).get("services") or {}
        local_builds.update(
            name for name, spec in services.items() if (spec or {}).get("build")
        )

    assert {"backend", "kong-api-gateway", "redis", "litellm", "supabase-db"} <= core
    assert "jupyterhub" not in core and "celery-worker" not in core
    assert core & local_builds == {"backend", "litellm-init"}


def test_one_shot_verification_skips_an_image_the_cold_build_left_out(monkeypatch):
    """A one-shot whose image was left out was never started, so waiting for
    it would time out and fail a launch that #989 lets continue."""
    from types import SimpleNamespace

    import start as start_module

    starter = start_module.AtlasStarter()
    env = {"OPEN_WEB_UI_INIT_SCALE": "1", "N8N_INIT_SCALE": "1"}
    monkeypatch.setattr(starter.config_parser, "parse_env_file", lambda: env)
    monkeypatch.setattr(
        starter.config_parser, "load_consumer_config",
        lambda: SimpleNamespace(n8n_workflows=()),
    )
    waited: list[list[str]] = []
    monkeypatch.setattr(
        starter.docker_manager, "failed_one_shot_services",
        lambda services, **_kwargs: waited.append(list(services)) or [],
    )
    starter.skipped_builds = ["open-webui-init"]

    assert starter.verify_one_shot_init_containers() is True
    assert waited == [["n8n-init"]]
