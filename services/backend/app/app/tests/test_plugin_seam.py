import logging
import os
import subprocess
import sys
import types
from pathlib import Path


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
        assert cmd[:6] == [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "-r",
        ]
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


def test_malformed_manifest_skips_only_that_plugin(tmp_path, monkeypatch, caplog):
    _plugin_pkg(tmp_path, "good_plugin", "/__good__",
                "plugin_manifest_version: 1\nname: good\nroute_prefix: /good\n")
    _plugin_pkg(tmp_path, "broken_plugin", "/__broken__", "name: [unclosed\n")

    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    caplog.set_level(logging.ERROR, logger="uvicorn.error")
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)

    paths = {r.path for r in app.router.routes}
    assert "/__good__" in paths          # healthy plugin unaffected
    assert "/__broken__" not in paths    # malformed skipped, NOT degraded to manifest-less
    statuses = {e["name"]: e["status"] for e in inventory}
    assert statuses.get("good") == "loaded"
    broken = next(e for e in inventory if e["status"] == "error")
    assert broken["name"] == "broken_plugin"
    assert "invalid plugin.yml" in caplog.text


def test_duplicate_plugin_name_second_is_skipped(tmp_path, monkeypatch):
    _plugin_pkg(tmp_path, "a_plugin", "/__dup_a__",
                "plugin_manifest_version: 1\nname: dup\nroute_prefix: /alpha\n")
    _plugin_pkg(tmp_path, "b_plugin", "/__dup_b__",
                "plugin_manifest_version: 1\nname: dup\nroute_prefix: /beta\n")

    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)

    paths = {r.path for r in app.router.routes}
    assert "/__dup_a__" in paths       # first wins
    assert "/__dup_b__" not in paths   # duplicate name skipped
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
    _plugin_pkg(tmp_path, "one_plugin", "/__one__",
                "plugin_manifest_version: 1\nname: one\nroute_prefix: /shared\n")
    _plugin_pkg(tmp_path, "two_plugin", "/__two__",
                "plugin_manifest_version: 1\nname: two\nroute_prefix: /shared/sub\n")
    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)

    paths = {r.path for r in app.router.routes}
    assert "/__one__" in paths
    assert "/__two__" not in paths
    assert any("overlaps prefix" in e.get("error", "") for e in inventory if e["status"] == "skipped")


def test_prefix_containment_overlap_second_skipped(tmp_path, monkeypatch):
    """`/zeta` and `/zetax` are NOT first-segment-equal but Kong's raw-prefix
    match makes `/zeta` intercept `/zetax` — the second must be skipped (M1)."""
    _plugin_pkg(tmp_path, "zeta_a_plugin", "/__ov_a__",
                "plugin_manifest_version: 1\nname: aa\nroute_prefix: /zeta\n")
    _plugin_pkg(tmp_path, "zeta_b_plugin", "/__ov_b__",
                "plugin_manifest_version: 1\nname: bb\nroute_prefix: /zetax\n")
    from fastapi import FastAPI
    app = FastAPI()
    monkeypatch.setenv("BACKEND_PLUGINS_DIR", str(tmp_path))
    import plugin_seam
    inventory = plugin_seam.load_plugins(app)
    paths = {r.path for r in app.router.routes}
    assert "/__ov_a__" in paths
    assert "/__ov_b__" not in paths
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
        tmp_path, "sec_plugin", "/__sec__",
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
