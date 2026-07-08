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
import os
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger("uvicorn.error")

# pip runs at backend import time (load_plugins is called from main.py at
# startup), so a hung download (flaky index, slow mirror, network partition)
# would block the whole backend indefinitely with no request-level timeout to
# rescue it. Bound it. Override via env for slow networks.
try:
    _PIP_INSTALL_TIMEOUT = int(os.getenv("BACKEND_PLUGINS_PIP_TIMEOUT_SECONDS", "300"))
except ValueError:
    # A non-numeric operator value (e.g. "300s") would otherwise crash the
    # backend at import with a raw ValueError; fall back to the default.
    # Mirrors fal_media_client.py's FAL_TIMEOUT_SECONDS parse guard.
    _PIP_INSTALL_TIMEOUT = 300


class PluginRequirementsInstallError(RuntimeError):
    """Raised when pip cannot install a plugin requirements file."""

    def __init__(self, requirements_file: Path, error: subprocess.CalledProcessError) -> None:
        self.requirements_file = requirements_file
        self.returncode = error.returncode
        self.stdout = (error.stdout or "").strip()
        self.stderr = (error.stderr or "").strip()

        details = [
            f"plugin seam: failed to install {requirements_file} (exit {error.returncode})"
        ]
        if self.stderr:
            details.append(f"stderr: {self.stderr}")
        if self.stdout:
            details.append(f"stdout: {self.stdout}")
        super().__init__("; ".join(details))


def _install_requirements(directory: Path, installed: set[Path] | None = None) -> None:
    reqs = directory / "requirements.txt"
    if not reqs.is_file():
        return
    resolved = reqs.resolve()
    if installed is not None:
        if resolved in installed:
            return
    _log.info("plugin seam: installing %s", reqs)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", str(reqs)],
            check=True,
            capture_output=True,
            text=True,
            timeout=_PIP_INSTALL_TIMEOUT,
        )
    except subprocess.CalledProcessError as exc:
        raise PluginRequirementsInstallError(reqs, exc) from exc
    except subprocess.TimeoutExpired as exc:
        # 124 is the conventional timeout exit code (matches `timeout(1)`).
        # Surface it through the same error type the caller already handles
        # so a timed-out install degrades like a failed one (shared reqs
        # timeout → skip all plugins; per-plugin reqs timeout → skip plugin).
        raise PluginRequirementsInstallError(
            reqs,
            subprocess.CalledProcessError(
                returncode=124,
                cmd=exc.cmd,
                output=exc.stdout if isinstance(exc.stdout, str) else "",
                stderr=(exc.stderr if isinstance(exc.stderr, str) else "")
                + f"\nplugin seam: pip install timed out after {_PIP_INSTALL_TIMEOUT}s",
            ),
        ) from exc
    if installed is not None:
        installed.add(resolved)


def load_plugins(app) -> None:
    plugins_dir = Path(os.getenv("BACKEND_PLUGINS_DIR", "/app/plugins"))
    if not plugins_dir.is_dir():
        return
    installed_requirements: set[Path] = set()
    try:
        _install_requirements(plugins_dir, installed_requirements)
    except PluginRequirementsInstallError:
        _log.exception("plugin seam: shared plugin requirements failed; skipping plugin loading")
        return
    if str(plugins_dir) not in sys.path:
        sys.path.insert(0, str(plugins_dir))
    for entry in sorted(plugins_dir.iterdir()):
        if not (entry.is_dir() and (entry / "__init__.py").is_file()):
            continue
        try:
            _install_requirements(entry, installed_requirements)
        except PluginRequirementsInstallError:
            _log.exception("plugin seam: requirements failed for plugin %r; skipping plugin", entry.name)
            continue
        try:
            module = importlib.import_module(entry.name)
            router = getattr(module, "router", None)
            if router is not None:
                app.include_router(router)
                _log.info("plugin seam: loaded plugin %r", entry.name)
        except Exception:  # one bad plugin must not crash the backend
            _log.exception("plugin seam: failed to load plugin %r", entry.name)
