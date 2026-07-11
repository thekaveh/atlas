"""Host-time backend plugin manifest (``plugin.yml``) support — #402.

The backend validates ``plugin.yml`` at container startup (Pydantic, in
``services/backend/app/app/plugin_manifest.py``). This module is the
bootstrapper-side counterpart used BEFORE containers start, for two jobs:

1. **Consumer doctor** — validate each declared plugin env against the resolved
   ``.env`` so a missing/typo'd required var surfaces as a startup diagnostic
   rather than a runtime 500.
2. **Kong route-level auth composition** — derive the ``(route_prefix, mode)``
   policy from plugins whose ``auth`` is not ``inherit``, so ``key-auth`` /
   ``open`` can be expressed per prefix without weakening unrelated backend
   routes.

Validation uses ``jsonschema`` (a bootstrapper dependency) against the canonical
``bootstrapper/schemas/plugin.schema.json`` — the same file that documents the
contract — so the host-time and container-time views cannot drift on shape.

The reserved-prefix set and secret-masking behavior mirror the backend module;
the schema file is the shared source of truth for the field grammar.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

PLUGIN_MANIFEST_FILENAME = "plugin.yml"
SECRET_MASK = "***"

# Built-in backend route prefixes a plugin must not shadow. Kept in sync with
# services/backend/app/app/plugin_manifest.py::RESERVED_ROUTE_PREFIXES and the
# schema's route_prefix description.
RESERVED_ROUTE_PREFIXES: frozenset[str] = frozenset(
    {
        "api",
        "comfyui",
        "documents",
        "health",
        "jobs",
        "media",
        "memory",
        "plugins",
        "research",
        "storage",
        "workflows",
    }
)

_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "plugin.schema.json"
_validator_singleton: Draft202012Validator | None = None


def _get_validator() -> Draft202012Validator:
    global _validator_singleton
    if _validator_singleton is None:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        _validator_singleton = Draft202012Validator(schema)
    return _validator_singleton


class PluginManifestError(RuntimeError):
    """Raised when a present ``plugin.yml`` cannot be loaded or validated."""

    def __init__(self, plugin: str, message: str) -> None:
        self.plugin = plugin
        self.message = message
        super().__init__(f"{plugin}: {message}")


def prefixes_overlap(a: str, b: str) -> bool:
    """True when two route prefixes collide under Kong's raw-prefix matching.

    Kong matches a prefix route against any request path that *starts with* the
    route path (not segment-bounded), so ``/a`` intercepts ``/ab`` and ``/a/b``.
    Overlap = one is a raw string-prefix of the other. Kept identical to the
    backend's plugin_manifest.prefixes_overlap (#402 review M1)."""
    return a == b or a.startswith(b) or b.startswith(a)


@dataclass(frozen=True)
class PluginManifest:
    name: str
    route_prefix: str
    auth: str = "inherit"
    health_path: str | None = None
    docs_url: str | None = None
    env: tuple[dict, ...] = ()
    depends_on: tuple[str, ...] = ()
    source_dir: Path | None = None

    @property
    def prefix_head(self) -> str:
        return self.route_prefix.strip("/").split("/", 1)[0]


def _format_jsonschema_error(error) -> str:
    path = ".".join(str(p) for p in error.absolute_path) or "(root)"
    return f"{path}: {error.message}"


def load_plugin_manifest(plugin_dir: Path) -> PluginManifest | None:
    """Load & validate ``<plugin_dir>/plugin.yml`` against the canonical schema.

    Returns ``None`` when absent (manifest-less). Raises
    :class:`PluginManifestError` when present but malformed.
    """
    manifest_path = plugin_dir / PLUGIN_MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    hint = plugin_dir.name
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PluginManifestError(hint, f"could not parse YAML ({exc})") from exc
    if not isinstance(raw, dict):
        raise PluginManifestError(hint, "manifest must be a mapping at the top level")
    errors = sorted(_get_validator().iter_errors(raw), key=lambda e: list(e.absolute_path))
    if errors:
        details = "; ".join(_format_jsonschema_error(e) for e in errors)
        raise PluginManifestError(hint, f"schema violation(s): {details}")
    return PluginManifest(
        name=raw["name"],
        route_prefix=raw["route_prefix"],
        auth=raw.get("auth", "inherit"),
        health_path=raw.get("health_path"),
        docs_url=raw.get("docs_url"),
        env=tuple(raw.get("env", ())),
        depends_on=tuple(raw.get("depends_on", ())),
        source_dir=plugin_dir,
    )


@dataclass
class DiscoveryResult:
    manifests: list[PluginManifest] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # plugin-scoped error strings


def discover_plugin_manifests(plugin_dirs: list[Path]) -> DiscoveryResult:
    """Load every plugin.yml under the given roots, rejecting conflicts.

    A malformed manifest, a duplicate name, an overlapping prefix, or a reserved
    prefix becomes an error string (the offending plugin is excluded); clear
    manifests are returned in discovery order. Directories without a plugin.yml
    are silently ignored (manifest-less plugins are not this module's concern).
    """
    result = DiscoveryResult()
    seen_names: dict[str, str] = {}
    seen_prefixes: dict[str, str] = {}
    for plugins_dir in plugin_dirs:
        if not plugins_dir.is_dir():
            continue
        for entry in sorted(plugins_dir.iterdir()):
            if not (entry.is_dir() and (entry / "__init__.py").is_file()):
                continue
            try:
                manifest = load_plugin_manifest(entry)
            except PluginManifestError as exc:
                result.errors.append(f"invalid plugin.yml for {exc.plugin!r}: {exc.message}")
                continue
            if manifest is None:
                continue
            conflict = _conflict(manifest, seen_names, seen_prefixes)
            if conflict is not None:
                result.errors.append(conflict)
                continue
            result.manifests.append(manifest)
    return result


def _conflict(
    manifest: PluginManifest,
    seen_names: dict[str, str],
    seen_prefixes: dict[str, str],
) -> str | None:
    prefix = manifest.route_prefix
    for reserved in RESERVED_ROUTE_PREFIXES:
        if prefixes_overlap(prefix, f"/{reserved}"):
            return f"plugin {manifest.name!r}: route_prefix {prefix!r} shadows built-in backend route /{reserved}"
    if manifest.name in seen_names:
        return f"plugin {manifest.name!r}: duplicate plugin name"
    for other_prefix, other_name in seen_prefixes.items():
        if prefixes_overlap(prefix, other_prefix):
            return (
                f"plugin {manifest.name!r}: route_prefix {prefix!r} overlaps prefix "
                f"{other_prefix!r} claimed by {other_name!r}"
            )
    seen_names[manifest.name] = manifest.name
    seen_prefixes[prefix] = manifest.name
    return None


def derive_route_auth(manifests: list[PluginManifest]) -> list[tuple[str, str]]:
    """Per-prefix Kong auth overrides — (route_prefix, mode) for non-inherit auth.

    ``inherit`` plugins contribute nothing (they follow the backend default).
    Order follows discovery so the emitted route order is deterministic.
    """
    return [(m.route_prefix, m.auth) for m in manifests if m.auth in ("open", "key-auth")]


def validate_plugin_env(manifest: PluginManifest, env: dict[str, str]) -> list[str]:
    """Warnings for the manifest's declared env contract (never returns secrets)."""
    warnings: list[str] = []
    for spec in manifest.env:
        name = spec.get("name")
        if not name:
            continue
        raw = env.get(name)
        present = raw is not None and raw != ""
        secret = bool(spec.get("secret", False))
        if not present:
            if spec.get("required", False):
                warnings.append(f"plugin {manifest.name!r}: required env {name} is not set")
            continue
        mismatch = _type_mismatch(spec, raw)
        if mismatch:
            shown = SECRET_MASK if secret else repr(raw)
            warnings.append(f"plugin {manifest.name!r}: env {name}={shown} {mismatch}")
    return warnings


def _type_mismatch(spec: dict, value: str) -> str | None:
    kind = spec.get("type", "string")
    if kind == "int":
        try:
            int(value)
        except ValueError:
            return "is not a valid int"
    elif kind == "bool":
        if value.strip().lower() not in (_BOOL_TRUE | _BOOL_FALSE):
            return "is not a valid bool"
    elif kind == "enum":
        values = spec.get("values") or []
        if values and value not in values:
            return f"is not one of the allowed values [{', '.join(values)}]"
    return None
