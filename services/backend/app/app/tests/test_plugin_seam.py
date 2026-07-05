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

    def fake_run(cmd, check):
        assert cmd[:6] == [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "-r",
        ]
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
