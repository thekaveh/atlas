"""Generic downstream extension seam (no RAG-specific logic).

A downstream consumer mounts a directory of plugin packages at
``$BACKEND_PLUGINS_DIR`` (default ``/app/plugins``). Each immediate
subdirectory that is an importable package exposing a module-level
``router`` (a FastAPI ``APIRouter``) is included into the app. A shared
``requirements.txt`` in the plugin root is installed first, and each plugin
package's own ``requirements.txt`` is installed before that package is
imported. The whole thing is a no-op when the directory is absent, so base
Atlas is unaffected.
"""
from __future__ import annotations

import importlib
import logging
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger("uvicorn.error")


def _install_requirements(directory: Path, installed: set[Path] | None = None) -> None:
    reqs = directory / "requirements.txt"
    if not reqs.is_file():
        return
    resolved = reqs.resolve()
    if installed is not None:
        if resolved in installed:
            return
        installed.add(resolved)
    _log.info("plugin seam: installing %s", reqs)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", str(reqs)],
        check=False,
    )


def load_plugins(app) -> None:
    import os

    plugins_dir = Path(os.getenv("BACKEND_PLUGINS_DIR", "/app/plugins"))
    if not plugins_dir.is_dir():
        return
    installed_requirements: set[Path] = set()
    _install_requirements(plugins_dir, installed_requirements)
    if str(plugins_dir) not in sys.path:
        sys.path.insert(0, str(plugins_dir))
    for entry in sorted(plugins_dir.iterdir()):
        if not (entry.is_dir() and (entry / "__init__.py").is_file()):
            continue
        try:
            _install_requirements(entry, installed_requirements)
            module = importlib.import_module(entry.name)
            router = getattr(module, "router", None)
            if router is not None:
                app.include_router(router)
                _log.info("plugin seam: loaded plugin %r", entry.name)
        except Exception:  # one bad plugin must not crash the backend
            _log.exception("plugin seam: failed to load plugin %r", entry.name)
