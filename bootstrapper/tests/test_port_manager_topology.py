"""Regression tests for PortManager — must derive its mapping from the
live topology, never a hard-coded snapshot.

Background: PortManager used to carry a frozen PORT_MAPPING dict whose
offsets shadowed the manifest-driven Topology.port_defaults. When the
topology rework moved Hermes / Agents into a 60-block, that map went
stale and ``update_env_ports(default_base)`` started clobbering the
just-migrated .env on every run. These tests pin the contract that
PortManager NEVER rewrites a port to a value that disagrees with
``Topology.port_defaults``.
"""

from __future__ import annotations

from pathlib import Path
import os
import socket

import pytest


def _real_root() -> Path:
    """Repo root (parent of bootstrapper/)."""
    return Path(__file__).resolve().parent.parent.parent


def test_port_offsets_match_topology(monkeypatch):
    """PortManager.port_offsets() == Topology.port_defaults shifted to
    DEFAULT_BASE_PORT. Pinned so a future hardcoded fallback can't
    silently re-introduce drift."""
    from core.port_manager import PortManager
    from core.config_parser import DEFAULT_BASE_PORT
    from services.topology import build_topology

    pm = PortManager(str(_real_root()))
    offsets = pm.port_offsets()
    topology = build_topology(_real_root() / "services", base_port=DEFAULT_BASE_PORT)
    assert offsets == {
        var: port - DEFAULT_BASE_PORT for var, port in topology.port_defaults.items()
    }


def test_handle_port_configuration_with_default_base_does_not_clobber_topology(
    tmp_path, monkeypatch,
):
    """C1 regression: calling ``update_env_ports(DEFAULT_BASE_PORT)`` on
    a .env whose port values already match ``topology.port_defaults``
    must leave the file byte-identical. Otherwise the v0→v1 migration
    is undone immediately by the very next pipeline step.
    """
    from core.port_manager import PortManager
    from core.config_parser import DEFAULT_BASE_PORT
    from services.topology import build_topology

    real_root = _real_root()
    topology = build_topology(real_root / "services", base_port=DEFAULT_BASE_PORT)

    # Build a fixture .env at exactly the topology defaults — plus the
    # BASE_PORT line plus an unrelated key — so update_env_ports has
    # something to compare against.
    env_lines = [f"BASE_PORT={DEFAULT_BASE_PORT}\n", "UNRELATED_KEY=hello\n"]
    for var, port in topology.port_defaults.items():
        env_lines.append(f"{var}={port}\n")
    fixture_env = tmp_path / ".env"
    fixture_env.write_text("".join(env_lines))
    original = fixture_env.read_text()

    monkeypatch.setenv("ATLAS_ENV_FILE", str(fixture_env))
    pm = PortManager(str(real_root))
    assert pm.update_env_ports(DEFAULT_BASE_PORT, create_backup=False) is True
    assert fixture_env.read_text() == original


def test_update_env_ports_preserves_inline_comments(tmp_path, monkeypatch):
    """``LITELLM_PORT=63030  # label`` must keep its trailing label
    even when the port itself stays unchanged (the regex must not eat
    the comment tail)."""
    from core.port_manager import PortManager
    from core.config_parser import DEFAULT_BASE_PORT
    from services.topology import build_topology

    real_root = _real_root()
    topology = build_topology(real_root / "services", base_port=DEFAULT_BASE_PORT)
    litellm = topology.port_defaults["LITELLM_PORT"]

    fixture_env = tmp_path / ".env"
    fixture_env.write_text(
        f"BASE_PORT={DEFAULT_BASE_PORT}\n"
        f"LITELLM_PORT={litellm}  # custom label\n"
    )
    monkeypatch.setenv("ATLAS_ENV_FILE", str(fixture_env))
    pm = PortManager(str(real_root))
    assert pm.update_env_ports(DEFAULT_BASE_PORT, create_backup=False) is True
    assert "LITELLM_PORT" in fixture_env.read_text()
    assert "# custom label" in fixture_env.read_text()


def test_validate_base_port_uses_topology_max_offset():
    """``validate_base_port`` clamps against the largest topology slot,
    not a stale hardcoded offset (was 48 = JUPYTERHUB_PORT in v0)."""
    from core.port_manager import PortManager
    from services.topology import build_topology
    from core.config_parser import DEFAULT_BASE_PORT

    real_root = _real_root()
    topology = build_topology(real_root / "services", base_port=DEFAULT_BASE_PORT)
    max_offset = max(p - DEFAULT_BASE_PORT for p in topology.port_defaults.values())
    pm = PortManager(str(real_root))
    assert pm.validate_base_port(65535 - max_offset) is True
    assert pm.validate_base_port(65535 - max_offset + 1) is False


def test_bound_but_not_listening_ipv4_port_is_unavailable():
    from core.port_manager import PortManager

    pm = PortManager(str(_real_root()))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("0.0.0.0", 0))
        port = occupied.getsockname()[1]
        assert pm.check_port_availability(port) is False


def test_ipv6_only_listener_makes_port_unavailable():
    from core.port_manager import PortManager

    if not socket.has_ipv6:
        pytest.skip("IPv6 is unavailable")
    pm = PortManager(str(_real_root()))
    try:
        occupied = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        occupied.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        occupied.bind(("::", 0))
    except OSError:
        pytest.skip("IPv6 wildcard bind is unavailable")
    with occupied:
        port = occupied.getsockname()[1]
        occupied.listen()
        assert pm.check_port_availability(port) is False


def test_port_probe_fails_closed_on_indeterminate_socket_errors(monkeypatch):
    from core.port_manager import PortManager
    import core.port_manager as port_manager

    monkeypatch.setattr(
        port_manager.socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )
    assert PortManager(str(_real_root())).check_port_availability(54321) is False


def test_update_env_ports_rewrites_trailing_whitespace_lines(tmp_path, monkeypatch):
    """``VAR=63002␣`` (trailing space, no comment) must still be
    rewritten — the pre-fix regex required a `#` after the spaces and
    silently no-oped on such lines."""
    from core.port_manager import PortManager
    from core.config_parser import DEFAULT_BASE_PORT
    from services.topology import build_topology

    real_root = _real_root()
    new_base = DEFAULT_BASE_PORT + 1000
    topology = build_topology(real_root / "services", base_port=new_base)
    var, want = next(iter(topology.port_defaults.items()))

    fixture_env = tmp_path / ".env"
    fixture_env.write_text(
        f"BASE_PORT={DEFAULT_BASE_PORT}\n{var}=12345 \n"
    )
    monkeypatch.setenv("ATLAS_ENV_FILE", str(fixture_env))
    pm = PortManager(str(real_root))
    assert pm.update_env_ports(new_base, create_backup=False) is True
    assert f"{var}={want}" in fixture_env.read_text()


def test_port_unset_covers_topology():
    """Every slot-allocated *_PORT in Topology.port_defaults must appear in
    AtlasStarter.unset_port_environment_variables()'s list. A stale
    shell-exported value of any allocated port shadows the freshly-computed
    slot on a `--base-port` cold start, which is exactly what that function
    exists to prevent. The exporter ports (cAdvisor/node/postgres/redis) were
    missing for several releases; this guards against the next omission."""
    import re
    from services.topology import build_topology
    from core.config_parser import DEFAULT_BASE_PORT

    src = (_real_root() / "bootstrapper" / "start.py").read_text(encoding="utf-8")
    m = re.search(r"port_variables\s*=\s*\[(.*?)\]", src, re.S)
    assert m, "could not locate the port_variables list in start.py"
    listed = set(re.findall(r"'([A-Z0-9_]+)'", m.group(1)))

    topology = build_topology(_real_root() / "services", base_port=DEFAULT_BASE_PORT)
    allocated = set(topology.port_defaults)

    missing = sorted(allocated - listed)
    assert not missing, (
        f"slot-allocated ports missing from unset_port_environment_variables: "
        f"{missing}"
    )


def test_auto_base_port_skips_busy_blocks_stepping_by_span(monkeypatch):
    """--base-port auto returns the first wholly-free block, stepping by the
    topology span so candidate blocks never overlap (#717)."""
    from core.port_manager import PortManager

    pm = PortManager(str(_real_root()))
    span = max(pm.port_offsets().values()) + 1
    busy = set(pm.calculate_port_assignments(20000).values())
    monkeypatch.setattr(pm, "check_port_availability", lambda port: port not in busy)

    chosen = pm.auto_base_port(start_from=20000, max_attempts=5)
    assert chosen == 20000 + span  # 20000 block busy -> next span-stepped block


def test_auto_base_port_never_returns_default(monkeypatch):
    """auto skips DEFAULT_BASE_PORT even when every port is free, so an
    auto-selected consumer can't squat the port a bare atlas checkout binds."""
    from core.config_parser import DEFAULT_BASE_PORT
    from core.port_manager import PortManager

    pm = PortManager(str(_real_root()))
    monkeypatch.setattr(pm, "check_port_availability", lambda port: True)

    chosen = pm.auto_base_port(start_from=DEFAULT_BASE_PORT, max_attempts=3)
    assert chosen is not None
    assert chosen != DEFAULT_BASE_PORT


def test_auto_base_port_returns_none_when_no_free_block(monkeypatch):
    from core.port_manager import PortManager

    pm = PortManager(str(_real_root()))
    monkeypatch.setattr(pm, "check_port_availability", lambda port: False)
    assert pm.auto_base_port(start_from=20000, max_attempts=3) is None
