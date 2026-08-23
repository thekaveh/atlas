"""
Regression tests for the three single-source-of-truth surfaces that
wire host-aliased Kong routes through the stack:

  - ``KongConfigGenerator.generate_litellm_service()`` (the always-on LiteLLM route)
  - ``KongConfigGenerator.get_adaptive_services()`` (the orchestrator that calls it)
  - ``Topology.aliases`` — the canonical alias list. Drives:
      * ``HostsManager.get_atlas_hosts()`` (the ``--setup-hosts`` consumer;
        the old ``HostsManager.get_atlas_hosts`` constant is retired)
      * ``state_builder.alias_for`` (the wizard service-box renderer)

Together these surfaces define every Kong-aliased URL the stack
exposes. A drift between any two (e.g. ``litellm.localhost`` added to
the generator but not the hosts list) shows up as a "the URL is in the
wizard but my browser can't resolve it" UX bug — silent unless caught
at the source. These tests pin the surfaces against each other.

Coverage focus is on the LiteLLM Kong alias; the assertions also
implicitly cover Hermes / Backend / n8n / etc. to the extent that
the surfaces must agree about each entry.
"""

from __future__ import annotations

import pytest

# Imports are top-level so a syntax error in any of the four modules
# fails the test collection step with a clear traceback.
from utils.hosts_manager import HostsManager
from utils.kong_config_generator import KongConfigGenerator
from wizard.model.state_builder import alias_for, _get_topology


# ────────────────────────────────────────────────────────────────────────────
# Fixture: a minimal ConfigParser stub that satisfies KongConfigGenerator's
# get_env_value() reads. We only need ``BACKEND_SOURCE`` and
# ``OPEN_WEB_UI_SOURCE`` to be non-disabled for get_adaptive_services()
# to emit those peers; the LiteLLM route is unconditional so it always
# appears regardless of env state.
# ────────────────────────────────────────────────────────────────────────────


class _StubConfigParser:
    def __init__(self, env: dict[str, str]):
        self._env = env
        # KongConfigGenerator.__init__ reads .env_file_path for error
        # messages; an unset attribute is fine for the tests.
        self.env_file_path = "/tmp/stub.env"

    def get_env_value(self, key: str, default: str = "") -> str:
        return self._env.get(key, default)


@pytest.fixture
def gen_with_all_enabled() -> KongConfigGenerator:
    env = {
        "BACKEND_SOURCE": "container",
        "OPEN_WEB_UI_SOURCE": "container",
        "KONG_HTTP_PORT": "63000",
        "LITELLM_PORT": "63030",
    }
    return KongConfigGenerator(_StubConfigParser(env))


# ────────────────────────────────────────────────────────────────────────────
# LiteLLM-specific: the always-on Kong route
# ────────────────────────────────────────────────────────────────────────────


def test_generate_litellm_service_is_always_on(gen_with_all_enabled):
    """The LiteLLM Kong route has no SOURCE gate — every call returns a dict."""
    svc = gen_with_all_enabled.generate_litellm_service()
    assert isinstance(svc, dict)
    assert svc.get("name") == "litellm-gateway"
    assert svc.get("url") == "http://litellm:4000/"


def test_generate_litellm_service_route_shape(gen_with_all_enabled):
    """The single route hits ``litellm.localhost`` with no path-stripping."""
    svc = gen_with_all_enabled.generate_litellm_service()
    routes = svc.get("routes") or []
    assert len(routes) == 1, "LiteLLM route should be a single host-routed entry"
    route = routes[0]
    assert route["name"] == "litellm-gateway-all"
    assert route["strip_path"] is False
    assert route["hosts"] == ["litellm.localhost"]


def test_generate_litellm_service_emits_cors_plugin(gen_with_all_enabled):
    """LiteLLM's dashboard is browser-facing; CORS must be enabled."""
    svc = gen_with_all_enabled.generate_litellm_service()
    plugins = svc.get("plugins") or []
    plugin_names = [p.get("name") for p in plugins]
    assert "cors" in plugin_names


def test_get_adaptive_services_includes_litellm(gen_with_all_enabled):
    """The orchestrator must include the LiteLLM route in its output."""
    services = gen_with_all_enabled.get_adaptive_services()
    names = [s["name"] for s in services]
    assert "litellm-gateway" in names, (
        "generate_litellm_service() must be wired into get_adaptive_services() — "
        "drift here means the route is generated but not actually emitted into "
        "the Kong config."
    )


# ────────────────────────────────────────────────────────────────────────────
# Cross-surface invariants: every Kong-aliased host must appear in BOTH
# HostsManager.get_atlas_hosts AND Topology.aliases. Drift here means
# the wizard advertises a URL that can't resolve, or --setup-hosts writes
# an entry that nothing else uses.
# ────────────────────────────────────────────────────────────────────────────


def test_hosts_manager_atlas_hosts_unique():
    """No duplicate entries in the topology-derived hosts list."""
    hosts = HostsManager._atlas_hosts_from_topology()
    assert len(hosts) == len(set(hosts)), f"duplicate host in topology hosts: {hosts}"


def test_topology_aliases_unique():
    """No two topology rows point at the same alias."""
    aliases = _get_topology().aliases
    assert len(aliases) == len(set(aliases)), (
        f"duplicate alias in Topology.aliases: {aliases}"
    )


def test_topology_aliases_contract():
    """The single source of truth for Kong-aliased hostnames is
    ``Topology.aliases``. ``_atlas_hosts_from_topology()`` is now a thin
    pass-through to it, so comparing the two directly would be a tautology.

    Instead, pin the *contract* on ``Topology.aliases`` so a manifest-level
    drift (e.g. someone adding a bare ``foo`` alias without ``.localhost``,
    or duplicating an alias across two manifests' ``rows[]``) surfaces here:

      1. Every entry is a non-empty string.
      2. Every entry ends with ``.localhost``.
      3. The list is deduplicated.
      4. Its length equals the count of non-empty ``rows[].alias`` values
         declared across all manifests — i.e. ``Topology.aliases`` is the
         lossless projection of the manifest aliases.
    """
    from services.manifests import load_manifests
    from services.topology import get_topology
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    aliases = list(_get_topology().aliases)

    # 1 + 2: well-formed entries.
    assert all(isinstance(a, str) and a for a in aliases), (
        f"Topology.aliases must contain non-empty strings: {aliases}"
    )
    bad_suffix = [a for a in aliases if not a.endswith(".localhost")]
    assert not bad_suffix, (
        f"Topology.aliases entries must end with .localhost: {bad_suffix}"
    )

    # 3: deduplicated.
    assert len(aliases) == len(set(aliases)), (
        f"Topology.aliases must be deduplicated: {aliases}"
    )

    # 4: lossless projection of manifest aliases.
    manifests = load_manifests(repo_root / "services")
    manifest_aliases = [r.alias for m in manifests for r in m.rows if r.alias]
    manifest_aliases += [a for m in manifests for a in m.extra_kong_aliases]
    assert len(aliases) == len(manifest_aliases), (
        f"Topology.aliases ({len(aliases)}) and manifest rows[].alias + "
        f"extra_kong_aliases ({len(manifest_aliases)}) counts must match — "
        f"every declared alias should land in the topology's alias list."
    )


def test_litellm_localhost_is_in_both_surfaces():
    """Spot-check for THIS round of work — ``litellm.localhost`` must be
    in both surfaces. Covered transitively by the agreement test above,
    but kept as a focused regression guard."""
    assert "litellm.localhost" in HostsManager._atlas_hosts_from_topology()
    assert alias_for("LiteLLM") == "litellm.localhost"


# ── pass 17: /etc/hosts cleanup must remove only what Atlas wrote ────


def test_cleanup_removes_only_lines_atlas_itself_wrote(tmp_path):
    """It deleted the operator's IPv4 `localhost` mapping.

    Removal matched any `127.0.0.1` line CONTAINING an Atlas alias, and
    several aliases are generic enough to share a line with an operator's own
    entries — `api.localhost`, `chat.localhost`, `search.localhost`,
    `graph.localhost`, `mcp.localhost`. The canonical first entry on macOS and
    Linux is `127.0.0.1\tlocalhost`, so appending one alias to it meant
    `stop.sh --clean-hosts` removed the whole line: a system-wide
    name-resolution break, with the summary reporting only Atlas's own aliases
    as removed.
    """
    from utils.hosts_manager import HostsManager

    manager = HostsManager()
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "##\n# Host Database\n##\n"
        "127.0.0.1\tlocalhost api.localhost my-dev-box\n"
        "127.0.0.1 chat.localhost   # my own reverse proxy\n"
        "255.255.255.255\tbroadcasthost\n"
        "::1             localhost\n"
        "127.0.0.1 unrelated.test\n"
        "# Atlas subdomains (added by start.py)\n"
        "127.0.0.1 n8n.localhost\n"
        "127.0.0.1 api.localhost\n",
        encoding="utf-8",
    )

    assert manager.remove_hosts_entries_silent(str(hosts)) is True
    result = hosts.read_text(encoding="utf-8")

    # operator-owned lines survive untouched, comments and all
    assert "127.0.0.1\tlocalhost api.localhost my-dev-box\n" in result
    assert "my own reverse proxy" in result
    assert "broadcasthost" in result
    assert "::1             localhost\n" in result
    assert "127.0.0.1 unrelated.test\n" in result
    # ...and Atlas's own block is gone
    assert "# Atlas subdomains" not in result
    assert "127.0.0.1 n8n.localhost" not in result


def test_cleanup_is_idempotent_on_operator_lines(tmp_path):
    from utils.hosts_manager import HostsManager

    manager = HostsManager()
    hosts = tmp_path / "hosts"
    original = "127.0.0.1\tlocalhost api.localhost\n::1 localhost\n"
    hosts.write_text(original, encoding="utf-8")
    for _ in range(3):
        manager.remove_hosts_entries_silent(str(hosts))
    assert hosts.read_text(encoding="utf-8") == original
