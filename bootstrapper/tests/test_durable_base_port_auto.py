"""Durable ``BASE_PORT: auto`` in the consumer manifest — multi-consumer isolation.

Manifest ``auto`` resolves once to the first wholly-free block (skipping the
default 63000 and blocks in use by other running stacks) and is KEPT across
restarts, so several consumers on one host get distinct, stable base-port blocks.
The one-off ``--base-port auto`` CLI flag stays resolve-fresh.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))


def _starter(env, port_manager, *, own_containers=False):
    import start

    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(parse_env_file=lambda: dict(env))
    s.port_manager = port_manager
    s.banner = NS(show_status_message=lambda *a, **k: None)
    s._project_has_running_containers = lambda overrides: own_containers
    return s


def test_resolver_keeps_non_default_and_resolves_fresh_otherwise():
    from core.config_parser import DEFAULT_BASE_PORT

    pm = NS(
        auto_base_port=lambda: 20000,
        validate_base_port=lambda p: 1024 <= p <= 65000,
        check_port_range_availability=lambda p: [],  # block free
    )

    # not auto -> unchanged
    r = _starter({}, pm)._resolve_auto_base_port_override({"BASE_PORT": "63100"})
    assert r["BASE_PORT"] == "63100"
    # auto + current == default -> resolve fresh
    r = _starter({"BASE_PORT": str(DEFAULT_BASE_PORT)}, pm)._resolve_auto_base_port_override({"BASE_PORT": "auto"})
    assert r["BASE_PORT"] == "20000"
    # auto + empty .env -> resolve fresh
    r = _starter({}, pm)._resolve_auto_base_port_override({"BASE_PORT": "auto"})
    assert r["BASE_PORT"] == "20000"
    # auto + a prior NON-default block -> KEEP it (durable; a warm restart never moves)
    r = _starter({"BASE_PORT": "20110"}, pm)._resolve_auto_base_port_override({"BASE_PORT": "auto"})
    assert r["BASE_PORT"] == "20110"
    # other overrides preserved
    r = _starter({}, pm)._resolve_auto_base_port_override({"BASE_PORT": "auto", "PROJECT_NAME": "p"})
    assert r["PROJECT_NAME"] == "p" and r["BASE_PORT"] == "20000"


def test_resolver_falls_back_to_default_when_no_free_block():
    from core.config_parser import DEFAULT_BASE_PORT

    pm = NS(auto_base_port=lambda: None, validate_base_port=lambda p: True)
    r = _starter({}, pm)._resolve_auto_base_port_override({"BASE_PORT": "auto"})
    assert r["BASE_PORT"] == str(DEFAULT_BASE_PORT)


def test_multiple_consumers_get_distinct_blocks_with_real_port_manager(monkeypatch):
    """With the real topology span, sequential auto-resolution skips blocks whose
    ports are occupied by another running stack — so consumers get distinct blocks."""
    from core.port_manager import PortManager

    pm = PortManager(str(REPO_ROOT))
    span = max(pm.port_offsets().values()) + 1

    # Consumer A: nothing running -> first block 20000.
    monkeypatch.setattr(pm, "check_port_availability", lambda port: True)
    a = pm.auto_base_port(start_from=20000, max_attempts=5)
    assert a == 20000

    # Consumer B started while A runs: A's block occupied -> B lands on the next block.
    a_ports = set(pm.calculate_port_assignments(20000).values())
    monkeypatch.setattr(pm, "check_port_availability", lambda port: port not in a_ports)
    b = pm.auto_base_port(start_from=20000, max_attempts=5)
    assert b == 20000 + span
    assert b != a


def test_manifest_base_port_auto_parses_to_literal(tmp_path):
    """A manifest `env.values: BASE_PORT: auto` reaches `env_overrides` as the
    literal 'auto' scalar — which the resolver then resolves at start."""
    from tests.test_consumer_manifest import _write_minimal_root, _write_conditional_consumer
    from core.consumer_manifest import load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_conditional_consumer(tmp_path, "    BASE_PORT: auto\n")
    cfg = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert cfg.env_overrides["BASE_PORT"] == "auto"

def test_foreign_occupancy_re_resolves_own_occupancy_keeps():
    """#727 composed-run finding: several consumers resolving `auto` at
    different times can each persist the SAME first-free block. The keep rule
    must distinguish ownership:
      free                  → keep (durable)
      occupied + ours up    → keep (warm restart)
      occupied + not ours   → re-resolve to the next free block (self-heal)
      docker unknowable     → keep (never surprise-move ports)"""
    pm_busy = NS(
        auto_base_port=lambda: 20100,
        validate_base_port=lambda p: 1024 <= p <= 65000,
        check_port_range_availability=lambda p: [20000, 20012],  # occupied
    )
    env = {"BASE_PORT": "20000", "PROJECT_NAME": "consumer-a"}

    # occupied by a FOREIGN stack → re-resolve
    r = _starter(env, pm_busy, own_containers=False)._resolve_auto_base_port_override(
        {"BASE_PORT": "auto"}
    )
    assert r["BASE_PORT"] == "20100"

    # occupied by OUR OWN containers (warm restart) → keep
    r = _starter(env, pm_busy, own_containers=True)._resolve_auto_base_port_override(
        {"BASE_PORT": "auto"}
    )
    assert r["BASE_PORT"] == "20000"

    # occupied + foreign + NO free block anywhere → keep with warning, not crash
    pm_full = NS(
        auto_base_port=lambda: None,
        validate_base_port=lambda p: True,
        check_port_range_availability=lambda p: [20000],
    )
    r = _starter(env, pm_full, own_containers=False)._resolve_auto_base_port_override(
        {"BASE_PORT": "auto"}
    )
    assert r["BASE_PORT"] == "20000"


def test_ownership_probe_is_conservative_on_docker_errors(monkeypatch):
    import start
    import subprocess as sp

    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(parse_env_file=lambda: {"PROJECT_NAME": "x"})

    monkeypatch.setattr(
        start.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(sp.SubprocessError("no docker")),
    )
    assert s._project_has_running_containers({}) is True  # conservative keep

    monkeypatch.setattr(
        start.subprocess, "run",
        lambda *a, **k: NS(returncode=0, stdout="abc123\n"),
    )
    assert s._project_has_running_containers({}) is True  # containers up

    monkeypatch.setattr(
        start.subprocess, "run",
        lambda *a, **k: NS(returncode=0, stdout=""),
    )
    assert s._project_has_running_containers({}) is False  # none of ours

