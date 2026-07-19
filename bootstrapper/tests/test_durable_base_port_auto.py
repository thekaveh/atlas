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


def _starter(env, port_manager):
    import start

    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(parse_env_file=lambda: dict(env))
    s.port_manager = port_manager
    s.banner = NS(show_status_message=lambda *a, **k: None)
    return s


def test_resolver_keeps_non_default_and_resolves_fresh_otherwise():
    from core.config_parser import DEFAULT_BASE_PORT

    pm = NS(auto_base_port=lambda: 20000, validate_base_port=lambda p: 1024 <= p <= 65000)

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
