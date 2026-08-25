import logging
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest


def test_load_plugins_includes_router(tmp_path, monkeypatch):
    # Arrange: a fake plugin package exposing `router`
    pkg = tmp_path / "demoplugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/__demoplugin__')\n"
        "def ping():\n"
        "    return {'ok': True}\n"
    )
    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))

    import plugin_seam  # the module we will create
    plugin_seam.load_plugins(app)

    paths = {r.path for r in app.router.routes}
    assert "/__demoplugin__" in paths


def test_load_plugins_noop_when_dir_missing(monkeypatch):
    from fastapi import FastAPI
    app = FastAPI()
    before = len(app.router.routes)
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", "/nonexistent/path/xyz")
    import plugin_seam
    plugin_seam.load_plugins(app)
    assert len(app.router.routes) == before


def test_load_plugins_accepts_pathsep_separated_roots(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root, plugin_name, route in (
        (first, "first_plugin", "/__first__"),
        (second, "second_plugin", "/__second__"),
    ):
        pkg = root / plugin_name
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            f"@router.get('{route}')\n"
            "def ping():\n"
            "    return {'ok': True}\n"
        )

    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", os.pathsep.join([str(first), str(second)]))

    import plugin_seam
    plugin_seam.load_plugins(app)

    paths = {r.path for r in app.router.routes}
    assert "/__first__" in paths
    assert "/__second__" in paths


def test_load_plugins_installs_shared_and_per_plugin_requirements_in_order(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("shared-dep\n")
    events = []
    event_module = types.ModuleType("plugin_test_events")
    event_module.events = events
    monkeypatch.setitem(sys.modules, "plugin_test_events", event_module)

    for name in ("zeta_plugin", "alpha_plugin", "beta_plugin"):
        pkg = tmp_path / name
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "import plugin_test_events\n"
            f"plugin_test_events.events.append(('import', '{name}'))\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
        )

    (tmp_path / "alpha_plugin" / "requirements.txt").write_text("alpha-dep\n")
    (tmp_path / "zeta_plugin" / "requirements.txt").write_text("zeta-dep\n")

    non_package = tmp_path / "not_a_plugin"
    non_package.mkdir()
    (non_package / "requirements.txt").write_text("ignored-dep\n")

    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))

    import plugin_seam

    def fake_run(cmd, **kwargs):
        # #559: the seam now installs with `--target <site dir> --upgrade`
        # into the pre-created writable plugin site; the invocation prefix and
        # the `-r <requirements>` tail are otherwise unchanged.
        assert cmd[:5] == [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
        ]
        assert "--target" in cmd and "--upgrade" in cmd
        assert cmd[-2] == "-r"
        assert kwargs == {"check": True, "capture_output": True, "text": True, "timeout": 300}
        requirement_path = Path(cmd[-1]).resolve().relative_to(tmp_path.resolve())
        events.append(("install", str(requirement_path)))

    monkeypatch.setattr(plugin_seam.subprocess, "run", fake_run)

    plugin_seam.load_plugins(app)

    assert events == [
        ("install", "requirements.txt"),
        ("install", "alpha_plugin/requirements.txt"),
        ("import", "alpha_plugin"),
        ("import", "beta_plugin"),
        ("install", "zeta_plugin/requirements.txt"),
        ("import", "zeta_plugin"),
    ]


def test_load_plugins_skips_plugin_when_requirements_install_fails(tmp_path, monkeypatch, caplog):
    events = []
    event_module = types.ModuleType("plugin_test_events")
    event_module.events = events
    monkeypatch.setitem(sys.modules, "plugin_test_events", event_module)

    pkg = tmp_path / "bad_plugin"
    pkg.mkdir()
    (pkg / "requirements.txt").write_text("missing-dep\n")
    (pkg / "__init__.py").write_text(
        "import plugin_test_events\n"
        "plugin_test_events.events.append(('import', 'bad_plugin'))\n"
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/bad-plugin-loaded')\n"
        "def ping():\n"
        "    return {'ok': True}\n"
    )

    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))

    import plugin_seam

    def fake_run(cmd, **kwargs):
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(
                returncode=9,
                cmd=cmd,
                output="looking for packages",
                stderr="No matching distribution found for missing-dep",
            )
        return subprocess.CompletedProcess(
            cmd,
            9,
            stdout="looking for packages",
            stderr="No matching distribution found for missing-dep",
        )

    monkeypatch.setattr(plugin_seam.subprocess, "run", fake_run)
    caplog.set_level(logging.ERROR, logger="uvicorn.error")

    plugin_seam.load_plugins(app)

    paths = {r.path for r in app.router.routes}
    assert "/bad-plugin-loaded" not in paths
    assert events == []
    assert "bad_plugin/requirements.txt" in caplog.text
    assert "No matching distribution found for missing-dep" in caplog.text
    assert "failed to load plugin" not in caplog.text


# ── plugin.yml manifest integration (#402) ──────────────────────────────────

def _plugin_pkg(root: Path, name: str, route: str, manifest: str | None = None) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        f"@router.get('{route}')\n"
        "def ping():\n"
        "    return {'ok': True}\n"
    )
    if manifest is not None:
        (pkg / "plugin.yml").write_text(manifest, encoding="utf-8")
    return pkg


def test_manifest_less_plugin_still_loads_and_appears_in_inventory(tmp_path, monkeypatch):
    _plugin_pkg(tmp_path, "ml_plugin", "/__manifestless__")
    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)

    assert "/__manifestless__" in {r.path for r in app.router.routes}
    entry = next(e for e in inventory if e["name"] == "ml_plugin")
    assert entry["status"] == "loaded"
    assert entry["manifest"] is False
    assert entry["route_prefix"] is None


def test_manifest_less_plugin_cannot_shadow_root(tmp_path, monkeypatch):
    _plugin_pkg(tmp_path, "root_plugin", "/")
    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)

    skipped = next(entry for entry in inventory if entry["name"] == "root_plugin")
    assert skipped["status"] == "skipped"
    assert "built-in root route" in skipped["error"]


def test_plugin_routes_enforce_declared_application_auth(tmp_path, monkeypatch):
    """Direct backend access must not bypass the plugin's Kong auth policy."""
    _plugin_pkg(tmp_path, "legacy_auth_plugin", "/legacy-auth")
    _plugin_pkg(
        tmp_path,
        "inherit_auth_plugin",
        "/inherit-auth",
        "plugin_manifest_version: 1\nname: inherit-auth\n"
        "route_prefix: /inherit-auth\nauth: inherit\n",
    )
    _plugin_pkg(
        tmp_path,
        "key_auth_plugin",
        "/key-auth",
        "plugin_manifest_version: 1\nname: key-auth\n"
        "route_prefix: /key-auth\nauth: key-auth\n",
    )
    _plugin_pkg(
        tmp_path,
        "open_auth_plugin",
        "/open-auth",
        "plugin_manifest_version: 1\nname: open-auth\n"
        "route_prefix: /open-auth\nauth: open\n",
    )

    from fastapi import FastAPI
    from fastapi.routing import APIRoute
    import plugin_seam

    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    plugin_seam.load_plugins(app)

    dependencies = {
        route.path: {dependency.call.__name__ for dependency in route.dependant.dependencies}
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    assert dependencies["/legacy-auth"] == {"require_backend_principal"}
    assert dependencies["/inherit-auth"] == {"require_backend_principal"}
    assert dependencies["/key-auth"] == {"require_plugin_gateway_key"}
    assert dependencies["/open-auth"] == set()


def test_key_auth_plugin_protects_http_and_websocket_routes(tmp_path, monkeypatch):
    plugin = tmp_path / "key_auth_socket_plugin"
    plugin.mkdir()
    (plugin / "__init__.py").write_text(
        "from fastapi import APIRouter, WebSocket\n"
        "http_calls = 0\n"
        "router = APIRouter()\n"
        "@router.get('/key-socket/http')\n"
        "def http_route():\n"
        "    global http_calls\n"
        "    http_calls += 1\n"
        "    return {'accepted': True}\n"
        "@router.websocket('/key-socket/ws')\n"
        "async def websocket_route(websocket: WebSocket):\n"
        "    await websocket.accept()\n"
        "    await websocket.send_text('accepted')\n"
    )
    (plugin / "plugin.yml").write_text(
        "plugin_manifest_version: 1\nname: key-auth-socket\n"
        "route_prefix: /key-socket\nauth: key-auth\n",
        encoding="utf-8",
    )

    from fastapi import FastAPI
    from fastapi.routing import APIWebSocketRoute, APIRoute
    from starlette.testclient import TestClient, WebSocketDenialResponse
    import plugin_seam

    app = FastAPI()
    monkeypatch.setenv("BACKEND_KONG_API_KEY", "gateway-secret")
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    plugin_seam.load_plugins(app)
    plugin_module = sys.modules["key_auth_socket_plugin"]

    dependencies = {
        route.path: {dependency.call.__name__ for dependency in route.dependant.dependencies}
        for route in app.routes
        if isinstance(route, (APIRoute, APIWebSocketRoute))
    }
    assert dependencies["/key-socket/http"] == {"require_plugin_gateway_key"}
    assert dependencies["/key-socket/ws"] == {"require_plugin_gateway_key"}

    with TestClient(app) as client:
        assert client.get("/key-socket/http").status_code == 401
        assert client.get("/key-socket/http", headers={"apikey": "wrong"}).status_code == 401
        assert plugin_module.http_calls == 0
        assert client.get("/key-socket/http", headers={"apikey": "gateway-secret"}).json() == {
            "accepted": True
        }
        assert plugin_module.http_calls == 1
        with client.websocket_connect("/key-socket/ws?apikey=gateway-secret") as websocket:
            assert websocket.receive_text() == "accepted"
        for path in ("/key-socket/ws", "/key-socket/ws?apikey=wrong"):
            with pytest.raises(WebSocketDenialResponse) as exc:
                with client.websocket_connect(path):
                    pass
            assert exc.value.status_code == 401


def test_plugin_router_cannot_escape_declared_prefix(tmp_path, monkeypatch):
    _plugin_pkg(
        tmp_path,
        "escaping_plugin",
        "/health",
        "plugin_manifest_version: 1\nname: escaping\n"
        "route_prefix: /extension\nauth: open\n",
    )
    from fastapi import FastAPI
    import plugin_seam

    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    inventory = plugin_seam.load_plugins(app)

    assert "/health" not in {route.path for route in app.routes}
    skipped = next(entry for entry in inventory if entry["status"] == "skipped")
    assert "escape declared route_prefix" in skipped["error"]


def test_malformed_manifest_skips_only_that_plugin(tmp_path, monkeypatch, caplog):
    _plugin_pkg(tmp_path, "good_plugin", "/good/ping",
                "plugin_manifest_version: 1\nname: good\nroute_prefix: /good\n")
    _plugin_pkg(tmp_path, "broken_plugin", "/__broken__", "name: [unclosed\n")

    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    caplog.set_level(logging.ERROR, logger="uvicorn.error")
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)

    paths = {r.path for r in app.router.routes}
    assert "/good/ping" in paths          # healthy plugin unaffected
    assert "/__broken__" not in paths    # malformed skipped, NOT degraded to manifest-less
    statuses = {e["name"]: e["status"] for e in inventory}
    assert statuses.get("good") == "loaded"
    broken = next(e for e in inventory if e["status"] == "error")
    assert broken["name"] == "broken_plugin"
    assert "invalid plugin.yml" in caplog.text


def test_duplicate_plugin_name_second_is_skipped(tmp_path, monkeypatch):
    _plugin_pkg(tmp_path, "a_plugin", "/alpha/ping",
                "plugin_manifest_version: 1\nname: dup\nroute_prefix: /alpha\n")
    _plugin_pkg(tmp_path, "b_plugin", "/beta/ping",
                "plugin_manifest_version: 1\nname: dup\nroute_prefix: /beta\n")

    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)

    paths = {r.path for r in app.router.routes}
    assert "/alpha/ping" in paths       # first wins
    assert "/beta/ping" not in paths   # duplicate name skipped
    skipped = [e for e in inventory if e["status"] == "skipped"]
    assert any("duplicate plugin name" in e.get("error", "") for e in skipped)


def test_reserved_prefix_is_rejected(tmp_path, monkeypatch):
    _plugin_pkg(tmp_path, "sneaky", "/__sneaky__",
                "plugin_manifest_version: 1\nname: sneaky\nroute_prefix: /health\n")
    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)

    assert "/__sneaky__" not in {r.path for r in app.router.routes}
    skipped = next(e for e in inventory if e["status"] == "skipped")
    assert "shadows built-in" in skipped["error"]


def test_overlapping_prefix_second_is_skipped(tmp_path, monkeypatch):
    _plugin_pkg(tmp_path, "one_plugin", "/shared/ping",
                "plugin_manifest_version: 1\nname: one\nroute_prefix: /shared\n")
    _plugin_pkg(tmp_path, "two_plugin", "/shared/sub/ping",
                "plugin_manifest_version: 1\nname: two\nroute_prefix: /shared/sub\n")
    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)

    paths = {r.path for r in app.router.routes}
    assert "/shared/ping" in paths
    assert "/shared/sub/ping" not in paths
    assert any("overlaps prefix" in e.get("error", "") for e in inventory if e["status"] == "skipped")


def test_prefix_containment_overlap_second_skipped(tmp_path, monkeypatch):
    """`/zeta` and `/zetax` are NOT first-segment-equal but Kong's raw-prefix
    match makes `/zeta` intercept `/zetax` — the second must be skipped (M1)."""
    _plugin_pkg(tmp_path, "zeta_a_plugin", "/zeta/ping",
                "plugin_manifest_version: 1\nname: aa\nroute_prefix: /zeta\n")
    _plugin_pkg(tmp_path, "zeta_b_plugin", "/zetax/ping",
                "plugin_manifest_version: 1\nname: bb\nroute_prefix: /zetax\n")
    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)
    paths = {r.path for r in app.router.routes}
    assert "/zeta/ping" in paths
    assert "/zetax/ping" not in paths
    assert any("overlaps prefix" in e.get("error", "") for e in inventory if e["status"] == "skipped")


def test_reserved_overlap_shorter_prefix_rejected(tmp_path, monkeypatch):
    """`/heal` is not literally reserved, but Kong route `/heal` intercepts the
    built-in `/health` — it must be rejected (review M1)."""
    _plugin_pkg(tmp_path, "heal_plugin", "/__heal__",
                "plugin_manifest_version: 1\nname: heal\nroute_prefix: /heal\n")
    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)
    assert "/__heal__" not in {r.path for r in app.router.routes}
    assert any("shadows built-in" in e.get("error", "") for e in inventory if e["status"] == "skipped")


def test_inventory_masks_secret_env(tmp_path, monkeypatch):
    _plugin_pkg(
        tmp_path, "sec_plugin", "/sec/ping",
        "plugin_manifest_version: 1\nname: sec\nroute_prefix: /sec\n"
        "env:\n  - name: SEC_TOKEN\n    secret: true\n",
    )
    monkeypatch.setenv("SEC_TOKEN", "top-secret-value")
    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)

    entry = next(e for e in inventory if e["name"] == "sec")
    token_row = next(r for r in entry["env"] if r["name"] == "SEC_TOKEN")
    assert token_row["value"] == "***"
    assert "top-secret-value" not in repr(inventory)


# ── #559: pip --target into a pre-created writable site dir ─────────────────
def _write_plugin(tmp_path, name="siteplug", requirement="boto3"):
    plugin = tmp_path / name
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
    )
    (plugin / "requirements.txt").write_text(f"{requirement}\n")
    return plugin


def test_install_targets_writable_site_dir_on_sys_path(tmp_path, monkeypatch):
    """#559 core: requirements install with `--target <site dir> --upgrade`,
    and the pre-created site dir is on sys.path BEFORE any plugin import (so
    CPython's path-finder cache is built against an existing directory)."""
    import plugin_seam
    from fastapi import FastAPI

    site_dir = tmp_path / "plugin-site"
    monkeypatch.setenv("BACKEND_PLUGINS_SITE_DIR", str(site_dir))
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root)
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(plugins_root))

    pip_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        pip_calls.append(list(cmd))
        # The site dir must already exist and be on sys.path at install time.
        assert site_dir.is_dir()
        assert str(site_dir) in sys.path
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(plugin_seam.subprocess, "run", fake_run)
    plugin_seam.load_plugins(FastAPI())

    assert pip_calls, "per-plugin requirements should trigger a pip install"
    for cmd in pip_calls:
        target_idx = cmd.index("--target")
        assert cmd[target_idx + 1] == str(site_dir)
        assert "--upgrade" in cmd
        assert "-r" in cmd


def test_site_dir_creation_failure_degrades_to_untargeted_install(tmp_path, monkeypatch):
    """Fail-open: when the site dir cannot be created, installs run without
    --target (correct for images whose site-packages is writable) and startup
    is never blocked."""
    import plugin_seam
    from fastapi import FastAPI

    # A path under a FILE cannot be mkdir'd → OSError path.
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setenv("BACKEND_PLUGINS_SITE_DIR", str(blocker / "site"))
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root)
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(plugins_root))

    pip_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        pip_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(plugin_seam.subprocess, "run", fake_run)
    plugin_seam.load_plugins(FastAPI())

    assert pip_calls
    for cmd in pip_calls:
        assert "--target" not in cmd


def test_install_invalidates_import_caches(tmp_path, monkeypatch):
    """#559 footgun 2: after a successful install, importlib caches are
    refreshed so packages that appeared mid-process become importable."""
    import plugin_seam
    from fastapi import FastAPI

    monkeypatch.setenv("BACKEND_PLUGINS_SITE_DIR", str(tmp_path / "site"))
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root)
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(plugins_root))
    monkeypatch.setattr(
        plugin_seam.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    calls = {"n": 0}
    real_invalidate = plugin_seam.importlib.invalidate_caches

    def counting_invalidate():
        calls["n"] += 1
        real_invalidate()

    monkeypatch.setattr(plugin_seam.importlib, "invalidate_caches", counting_invalidate)
    plugin_seam.load_plugins(FastAPI())
    # once at site setup + once per successful install (>=1 plugin reqs)
    assert calls["n"] >= 2


def test_no_home_environment_regression_shape(tmp_path, monkeypatch):
    """The report's EACCES shape (no $HOME, root site-packages) cannot recur:
    the pip command never relies on user-site — it always carries an explicit
    --target when the site dir is available."""
    import plugin_seam
    from fastapi import FastAPI

    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("BACKEND_PLUGINS_SITE_DIR", str(tmp_path / "site"))
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root)
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(plugins_root))

    pip_calls: list[list[str]] = []
    monkeypatch.setattr(
        plugin_seam.subprocess,
        "run",
        lambda cmd, **kw: (pip_calls.append(list(cmd)), subprocess.CompletedProcess(cmd, 0, "", ""))[1],
    )
    plugin_seam.load_plugins(FastAPI())
    assert pip_calls and all("--target" in cmd for cmd in pip_calls)
    assert all("--user" not in cmd for cmd in pip_calls)
