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


def test_cleanup_removes_the_genai_era_header_and_writes_durably(tmp_path, monkeypatch):
    """The pre-rename header stayed behind forever; and the write must be
    fsynced before the rename, or a crash can leave an empty /etc/hosts."""
    import utils.hosts_manager as hosts_module
    from utils.hosts_manager import HostsManager

    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n\n# GenAI Stack subdomains (added by start.py)\n"
                     "127.0.0.1 n8n.localhost\n", encoding="utf-8")
    import stat

    synced = []
    real_fsync = hosts_module.os.fsync
    monkeypatch.setattr(hosts_module.os, "fsync", lambda fd: synced.append(
        "dir" if stat.S_ISDIR(hosts_module.os.fstat(fd).st_mode) else "file") or real_fsync(fd))
    assert HostsManager().remove_hosts_entries_silent(str(hosts)) is True
    result = hosts.read_text(encoding="utf-8")
    assert "GenAI Stack subdomains" not in result and "n8n.localhost" not in result
    # Both: the temp file before the rename, and its directory after it.
    assert "127.0.0.1 localhost" in result and sorted(synced) == ["dir", "file"]


# The wizard's "set up hosts" answer must not abort an unelevated launch.

def _hosts_starter(missing):
    from types import SimpleNamespace

    messages = []
    return SimpleNamespace(
        hosts_manager=SimpleNamespace(check_missing_hosts=lambda: list(missing)),
        banner=SimpleNamespace(
            show_status_message=lambda text, kind: messages.append((kind, text))
        ),
    ), messages


def test_present_entries_need_no_privilege(monkeypatch):
    import start

    monkeypatch.setattr(
        start, "_run_privileged_hosts_setup",
        lambda **_: (_ for _ in ()).throw(AssertionError("no sudo needed")),
    )
    starter, messages = _hosts_starter([])
    assert start.AtlasStarter.handle_hosts_configuration(starter, True, False)
    assert messages == []


def test_passwordless_sudo_is_used_non_interactively(monkeypatch):
    import start

    calls = []
    monkeypatch.setattr(
        start, "_run_privileged_hosts_setup",
        lambda **kw: calls.append(kw) or True,
    )
    starter, messages = _hosts_starter(["n8n.localhost"])
    assert start.AtlasStarter.handle_hosts_configuration(starter, True, False)
    assert calls == [{"non_interactive": True}]
    assert messages == []


def test_unapproved_sudo_warns_with_the_remedy_and_continues(monkeypatch, capsys):
    import start

    monkeypatch.setattr(start, "_run_privileged_hosts_setup", lambda **_: False)
    starter, _messages = _hosts_starter(["n8n.localhost"])
    assert start.AtlasStarter.handle_hosts_configuration(starter, True, False)
    # stdout, not the banner: the TUI banner is a no-op; its log pane shows stdout.
    out = capsys.readouterr().out
    assert out.startswith("WARNING:") and "./start.sh --setup-hosts" in out


@pytest.mark.parametrize("missing, warned", [([], False), (["n8n.localhost"], True)])
def test_default_hosts_answer_warns_only_when_entries_are_missing(missing, warned, capsys):
    import start

    starter, _messages = _hosts_starter(missing)
    assert start.AtlasStarter.handle_hosts_configuration(starter, False, False)
    assert capsys.readouterr().out.startswith("WARNING:") is warned


def test_skip_hosts_answer_does_not_check():
    import start

    starter, messages = _hosts_starter(["n8n.localhost"])
    starter.hosts_manager.check_missing_hosts = lambda: pytest.fail("skip means no check")
    assert start.AtlasStarter.handle_hosts_configuration(starter, False, True)
    assert messages == []



def test_kong_config_is_kong_readable_in_an_owner_only_directory(tmp_path):
    """The rendered config holds gateway secrets (service-role JWT)."""
    import stat

    out = tmp_path / "volumes" / "api" / "kong-dynamic.yml"
    assert KongConfigGenerator.__new__(KongConfigGenerator).write_config(
        {"_format_version": "3.0", "services": []}, out
    )
    assert stat.S_IMODE(out.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(out.stat().st_mode) == 0o644
    assert "_format_version" in out.read_text()


def test_source_conflict_is_reported_not_raised(monkeypatch, capsys):
    """--no-tui has no handler above this step; a ValueError from a service
    gate (Spark needs MinIO, …) used to escape as a raw traceback."""
    from types import SimpleNamespace

    import start

    messages = []

    def conflict():
        raise ValueError("Spark requires MinIO: --minio-source container")

    starter = SimpleNamespace(
        service_config=SimpleNamespace(generate_and_update_env=conflict),
        banner=SimpleNamespace(
            show_status_message=lambda text, kind: messages.append((kind, text))
        ),
    )
    assert start.AtlasStarter.generate_service_configuration(starter) is False
    assert capsys.readouterr().out == "ERROR: Spark requires MinIO: --minio-source container\n"


def test_hosts_cleanup_reports_only_what_it_removed_and_prunes_backups(tmp_path, monkeypatch):
    """It listed every alias as removed even when none were present, and left
    one unpruned /etc/hosts.backup.<second> per run (same-second runs overwrote)."""
    import utils.hosts_manager as hosts_module
    from utils.hosts_manager import HostsManager

    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1\\tlocalhost\\n", encoding="utf-8")
    monkeypatch.setattr(hosts_module, "is_elevated", lambda: True)
    manager = HostsManager()
    manager.hosts_file_path = str(hosts)
    logged = []
    monkeypatch.setattr(manager, "_log", lambda message, level="info": logged.append(message))
    for _ in range(8):
        assert manager.cleanup_hosts_entries() is True
    assert not any("were removed" in line for line in logged)
    assert any("No Atlas hosts entries were present" in line for line in logged)
    from utils.atomic_write import BACKUP_RETENTION

    # Eight runs in one second: unique names keep the newest five (the old
    # one-second timestamps overwrote each other down to one file).
    assert len(list(tmp_path.glob("*backup*"))) == BACKUP_RETENTION


def test_a_symlinked_state_dir_is_refused_with_a_clear_message(tmp_path):
    from services import remove_state_directory

    target = tmp_path / "real-state"
    target.mkdir()
    link = tmp_path / "state-link"
    link.symlink_to(target)
    with pytest.raises(RuntimeError, match="is a symlink to"):
        remove_state_directory(link, ("state", RuntimeError))
    assert target.is_dir() and link.is_symlink()


def test_direct_ports_do_not_answer_every_browser_origin():
    """pg-meta (SQL, unauthenticated, CORS *) and Weaviate (anonymous, CORS *)
    were reachable from any web page through their loopback ports."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    supabase = yaml.safe_load((root / "services/supabase/compose.yml").read_text(encoding="utf-8"))
    weaviate = yaml.safe_load((root / "services/weaviate/compose.yml").read_text(encoding="utf-8"))
    assert "ports" not in supabase["services"]["supabase-meta"]
    origin = weaviate["services"]["weaviate"]["environment"]["CORS_ALLOW_ORIGIN"]
    assert origin.startswith("http://weaviate.localhost:") and "*" not in origin
    # local-deep-researcher (langgraph-api) and LightRAG also defaulted to *.
    for service, key, host in (("local-deep-researcher", "CORS_ALLOW_ORIGINS", "research"),
                               ("lightrag", "CORS_ORIGINS", "lightrag")):
        compose = yaml.safe_load((root / f"services/{service}/compose.yml").read_text(encoding="utf-8"))
        origin = compose["services"][service]["environment"][key]
        assert origin.startswith(f"http://{host}.localhost:") and "*" not in origin, service


def test_hosts_setup_without_sudo_is_not_fatal(monkeypatch):
    """No sudo binary (minimal Linux, dev containers) raised FileNotFoundError
    and failed the launch; stop's twin already reported it."""
    import start as start_module

    monkeypatch.setattr(start_module, "is_elevated", lambda: False, raising=False)

    def no_sudo(*_args, **_kwargs):
        raise FileNotFoundError("sudo")

    monkeypatch.setattr(start_module.subprocess, "run", no_sudo)
    assert start_module._run_privileged_hosts_setup() is False


@pytest.mark.parametrize("env_text", [
    "ATLAS_MANAGED_HOST_STATE_ROOT={root}  # shared\n",
    "ATLAS_MANAGED_HOST_STATE_ROOT=/elsewhere\nATLAS_MANAGED_HOST_STATE_ROOT={root}\n",
    "﻿ATLAS_MANAGED_HOST_STATE_ROOT={root}\n",
])
def test_the_state_root_guard_reads_dotenv_like_the_managers(tmp_path, env_text):
    """The guard's own parser missed inline comments, last-wins duplicates and
    a BOM, so the root it meant to protect stayed deletable."""
    from utils import atomic_write

    root = tmp_path / "root"
    root.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(env_text.format(root=root), encoding="utf-8")
    assert atomic_write._env_file_state_root(env_file) == str(root)


def test_the_state_root_guard_follows_atlas_env_file(tmp_path, monkeypatch):
    from utils import atomic_write

    env_file = tmp_path / "atlas.env"
    env_file.write_text(f"ATLAS_MANAGED_HOST_STATE_ROOT={tmp_path / 'r'}\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env_file))
    assert atomic_write._env_file_state_root() == str(tmp_path / "r")


def test_supabase_studio_is_not_published_and_meta_reads_its_crypto_key():
    """Studio's pg-meta proxy accepted form-encoded SQL POSTs from any web page
    through its loopback port; and pg-meta read CRYPTO_KEY while Atlas set
    PG_META_CRYPTO_KEY, so it decrypted with SAMPLE_KEY and Studio's SQL and
    table editors failed on every generated stack."""
    from pathlib import Path

    import yaml

    compose = yaml.safe_load((Path(__file__).resolve().parents[2] / "services/supabase/compose.yml").read_text())
    assert "ports" not in compose["services"]["supabase-studio"]
    meta_env = compose["services"]["supabase-meta"]["environment"]
    studio_env = compose["services"]["supabase-studio"]["environment"]
    assert meta_env["CRYPTO_KEY"] == studio_env["PG_META_CRYPTO_KEY"] == "${SUPABASE_META_CRYPTO_KEY:-}"


def test_readme_topology_does_not_list_unpublished_ports_as_reachable():
    """pg-meta and Studio keep port slots but are not published; the README
    table still showed 63014/63019 as their default ports."""
    from pathlib import Path

    from tools.generate_readme_topology import generate_block

    block = generate_block(Path(__file__).resolve().parents[2] / "services")
    assert "| Supabase Studio | — (Kong only) | supabase-studio.localhost |" in block
    assert "| Supabase Meta | — | — |" in block
    assert "| TTS Provider | — " not in block  # virtual manifest: its slot is still shown


def test_no_surface_advertises_an_unpublished_port():
    """The wizard table/tooltip and the --no-tui summary still showed
    :63019 / :63014 for Studio and pg-meta after they were unpublished."""
    from services.topology import unpublished_port_vars
    from wizard.model.state_builder import resolve_port

    assert {"SUPABASE_META_PORT", "SUPABASE_STUDIO_PORT"} <= unpublished_port_vars()
    assert "TTS_PROVIDER_PORT" not in unpublished_port_vars()  # virtual display slot
    env = {"SUPABASE_STUDIO_PORT": "63019", "REDIS_PORT": "63025"}
    assert resolve_port("Supabase Studio", "container", "SUPABASE_STUDIO_PORT", env) is None
    assert resolve_port("Redis", "container", "REDIS_PORT", env) == ":63025"
    import start

    assert start._published_port_label("SUPABASE_STUDIO_PORT", env) == "-"  # --no-tui summary
    assert start._published_port_label("REDIS_PORT", env) == ":63025"


def test_the_service_directory_does_not_link_an_unpublished_port():
    """The Kong-served directory linked and probed http://localhost:63014 for
    pg-meta after it was unpublished, so the card always read unreachable."""
    from types import SimpleNamespace

    from utils.atlas_dashboard import _direct_url

    env = {"SUPABASE_META_PORT": "63014", "REDIS_PORT": "63025"}
    meta = SimpleNamespace(port_var="SUPABASE_META_PORT", localhost_port_var=None)
    redis = SimpleNamespace(port_var="REDIS_PORT", localhost_port_var=None)
    assert _direct_url(meta, "container", env) is None
    assert _direct_url(redis, "container", env) == "http://localhost:63025"
