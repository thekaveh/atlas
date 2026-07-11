"""Generic downstream extension seam (no RAG-specific logic).

A downstream consumer mounts one or more directories of plugin packages at
``$BACKEND_PLUGINS_DIR`` (default ``/app/plugins``; multiple roots are joined
with ``os.pathsep``). Each immediate
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

from plugin_manifest import (
    PluginManifest,
    PluginManifestError,
    RESERVED_ROUTE_PREFIXES,
    load_manifest,
    prefixes_overlap,
    validate_env,
)

_log = logging.getLogger("uvicorn.error")

# Populated by load_plugins(); exposed to the app (GET /plugins) as the plugin
# inventory. Each entry is a JSON-safe dict with secrets already masked.
PLUGIN_INVENTORY: list[dict] = []

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


def _plugin_roots() -> list[Path]:
    raw = os.getenv("BACKEND_PLUGINS_DIR", "/app/plugins")
    return [Path(part) for part in raw.split(os.pathsep) if part.strip()]


def _inventory_entry(
    name: str,
    status: str,
    *,
    manifest: PluginManifest | None = None,
    error: str | None = None,
) -> dict:
    """Build a JSON-safe inventory row (secrets already masked)."""
    entry: dict = {
        "name": name,
        "status": status,  # loaded | skipped | error
        "manifest": manifest is not None,
        "route_prefix": manifest.route_prefix if manifest else None,
        "health_path": manifest.health_path if manifest else None,
        "docs_url": manifest.docs_url if manifest else None,
        "auth": manifest.auth if manifest else None,
        "depends_on": list(manifest.depends_on) if manifest else [],
        "env": manifest.env_summary(dict(os.environ)) if manifest else [],
    }
    if error is not None:
        entry["error"] = error
    return entry


def _register_manifest(
    manifest: PluginManifest,
    seen_names: dict[str, str],
    seen_prefixes: dict[str, str],
) -> str | None:
    """Reject duplicate names / overlapping / reserved prefixes BEFORE mounting.

    Overlap is raw-prefix containment (Kong-accurate), not just first-segment
    equality, so ``/a`` vs ``/ab`` and ``/heal`` vs the built-in ``/health`` are
    both caught (#402 review M1). Returns a human-readable conflict reason, or
    None when the manifest is clear to load. Populates the seen-maps on success.
    """
    prefix = manifest.route_prefix
    for reserved in RESERVED_ROUTE_PREFIXES:
        if prefixes_overlap(prefix, f"/{reserved}"):
            return f"route_prefix {prefix!r} shadows built-in backend route /{reserved}"
    if manifest.name in seen_names:
        return f"duplicate plugin name {manifest.name!r} (already provided by {seen_names[manifest.name]!r})"
    for other_prefix, other_name in seen_prefixes.items():
        if prefixes_overlap(prefix, other_prefix):
            return f"route_prefix {prefix!r} overlaps prefix {other_prefix!r} claimed by {other_name!r}"
    seen_names[manifest.name] = manifest.name
    seen_prefixes[prefix] = manifest.name
    return None


def _load_plugins_from_dir(
    app,
    plugins_dir: Path,
    installed_requirements: set[Path],
    seen_names: dict[str, str],
    seen_prefixes: dict[str, str],
) -> None:
    if not plugins_dir.is_dir():
        return
    try:
        _install_requirements(plugins_dir, installed_requirements)
    except PluginRequirementsInstallError:
        _log.exception("plugin seam: shared plugin requirements failed for %s; skipping root", plugins_dir)
        return
    if str(plugins_dir) not in sys.path:
        sys.path.insert(0, str(plugins_dir))
    for entry in sorted(plugins_dir.iterdir()):
        if not (entry.is_dir() and (entry / "__init__.py").is_file()):
            continue

        # Load & validate the optional manifest BEFORE installing requirements
        # or importing, so a malformed manifest fails fast without executing any
        # plugin code (import side effects, pip). A present-but-malformed
        # manifest skips THIS plugin only — it does not degrade to manifest-less
        # loading, and other plugins stay healthy.
        try:
            manifest = load_manifest(entry)
        except PluginManifestError as exc:
            _log.error("%s; skipping plugin", exc)
            PLUGIN_INVENTORY.append(_inventory_entry(entry.name, "error", error=exc.message))
            continue

        if manifest is not None:
            conflict = _register_manifest(manifest, seen_names, seen_prefixes)
            if conflict is not None:
                _log.error("plugin seam: %s; skipping plugin %r", conflict, entry.name)
                PLUGIN_INVENTORY.append(
                    _inventory_entry(manifest.name, "skipped", manifest=manifest, error=conflict)
                )
                continue
            for warning in validate_env(manifest, dict(os.environ)):
                _log.warning("plugin seam: %s", warning)

        try:
            _install_requirements(entry, installed_requirements)
        except PluginRequirementsInstallError:
            _log.exception("plugin seam: requirements failed for plugin %r; skipping plugin", entry.name)
            name = manifest.name if manifest else entry.name
            PLUGIN_INVENTORY.append(
                _inventory_entry(name, "error", manifest=manifest, error="requirements install failed")
            )
            continue
        try:
            module = importlib.import_module(entry.name)
            router = getattr(module, "router", None)
            if router is not None:
                app.include_router(router)
                _log.info("plugin seam: loaded plugin %r", entry.name)
            name = manifest.name if manifest else entry.name
            PLUGIN_INVENTORY.append(_inventory_entry(name, "loaded", manifest=manifest))
        except Exception:  # one bad plugin must not crash the backend
            _log.exception("plugin seam: failed to load plugin %r", entry.name)
            name = manifest.name if manifest else entry.name
            PLUGIN_INVENTORY.append(
                _inventory_entry(name, "error", manifest=manifest, error="import failed")
            )


def load_plugins(app) -> list[dict]:
    """Discover, validate, and mount plugins; return the plugin inventory.

    The returned list (also available as module-level ``PLUGIN_INVENTORY``) is
    JSON-safe with secret env values masked, suitable for the ``GET /plugins``
    endpoint and generated docs.
    """
    PLUGIN_INVENTORY.clear()
    installed_requirements: set[Path] = set()
    seen_names: dict[str, str] = {}
    seen_prefixes: dict[str, str] = {}
    for plugins_dir in _plugin_roots():
        _load_plugins_from_dir(app, plugins_dir, installed_requirements, seen_names, seen_prefixes)
    return list(PLUGIN_INVENTORY)
