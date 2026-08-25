"""Typed, validated backend plugin manifest (``plugin.yml``) — #402.

A downstream plugin package under ``$BACKEND_PLUGINS_DIR`` MAY ship an optional
``plugin.yml`` next to its ``__init__.py``. Absent → the plugin loads exactly as
before (fully backward compatible). Present → it declares a versioned, typed
config contract the plugin seam validates before mounting:

- ``name`` / ``route_prefix`` (unique, non-overlapping, non-reserved),
- ``health_path`` / ``docs_url`` metadata,
- ``auth: inherit|open|key-auth`` (enforced by both the application seam and
  the bootstrapper's per-plugin Kong policy),
- typed / ``default`` / ``required`` / ``secret`` ``env`` declarations,
- ``depends_on`` dependency-endpoint hints.

A **present-but-malformed** manifest is a hard error: the seam skips that plugin
with a structured message and leaves the others healthy (it does NOT silently
degrade to manifest-less loading). Secret env values are masked in every
surface (inventory, logs, exceptions).

This module has no FastAPI/Atlas-internal imports so it stays trivially unit
testable; validation uses Pydantic v2 (already a backend dependency).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

PLUGIN_MANIFEST_FILENAME = "plugin.yml"
SUPPORTED_MANIFEST_VERSION = 1
SECRET_MASK = "***"

# Shared path grammar — MUST stay identical to bootstrapper/schemas/plugin.schema.json's
# route_prefix/health_path pattern so the container-time (Pydantic) and host-time
# (jsonschema) validators accept/reject the exact same set. A drift here is an
# auth-bypass vector (#402 review B2): at least one non-empty segment, so a bare
# "/" (which would match the whole backend) is rejected.
_PATH_RE = re.compile(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*")

# Built-in backend route prefixes a plugin must not shadow. Kept as the first
# path segment (``/api/ray`` reserves ``api``). Mirrors main.py's routers plus
# the ``/plugins`` inventory endpoint this feature adds.
RESERVED_ROUTE_PREFIXES: frozenset[str] = frozenset(
    {
        "api",
        "comfyui",
        "documents",
        "health",
        "jobs",
        "lightrag",
        "media",
        "memory",
        "metrics",
        "docs",
        "openapi.json",
        "plugins",
        "ready",
        "redoc",
        "research",
        "storage",
        "workflows",
    }
)

_ENV_TYPES = ("string", "int", "bool", "enum")
_AUTH_MODES = ("inherit", "open", "key-auth")
_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}
_KONG_TIMEOUT_MAX_MS = 2_147_483_646


class PluginManifestError(RuntimeError):
    """Raised when a present ``plugin.yml`` cannot be loaded or validated.

    Structured so the seam can log a clear, plugin-scoped message and skip the
    offending plugin without touching the others.
    """

    def __init__(self, plugin: str, message: str) -> None:
        self.plugin = plugin
        self.message = message
        super().__init__(f"plugin seam: invalid {PLUGIN_MANIFEST_FILENAME} for plugin {plugin!r}: {message}")


def prefixes_overlap(a: str, b: str) -> bool:
    """True when two route prefixes collide under Kong's raw-prefix matching.

    Kong (traditional/traditional_compatible) matches a prefix route against any
    request path that *starts with* the route path — it is not segment-bounded.
    So ``/a`` intercepts ``/ab`` and ``/a/b`` alike. Two prefixes overlap when one
    is a raw string-prefix of the other (equal counts as overlap). Conservative by
    design: over-rejecting a lookalike prefix is safe, under-rejecting is an
    auth-composition bypass (#402 review M1)."""
    return a == b or a.startswith(b) or b.startswith(a)


class PluginEnvVar(BaseModel):
    # strict=True disables Pydantic's lenient coercion so this model accepts/
    # rejects the same value types jsonschema does (e.g. required: "yes" is
    # rejected, not coerced to True) — closing the validator-drift auth bypass
    # (#402 review B2).
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    type: str = "string"
    values: list[str] = Field(default_factory=list)
    default: Optional[str] = None
    required: bool = False
    secret: bool = False
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name_upper_snake(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", v):
            raise ValueError(f"env var name {v!r} must be UPPER_SNAKE_CASE")
        return v

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in _ENV_TYPES:
            raise ValueError(f"env type {v!r} must be one of {', '.join(_ENV_TYPES)}")
        return v


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    plugin_manifest_version: int
    name: str
    route_prefix: str
    health_path: Optional[str] = None
    docs_url: Optional[str] = None
    auth: str = "inherit"
    connect_timeout: Optional[int] = Field(default=None, ge=1, le=_KONG_TIMEOUT_MAX_MS)
    write_timeout: Optional[int] = Field(default=None, ge=1, le=_KONG_TIMEOUT_MAX_MS)
    read_timeout: Optional[int] = Field(default=None, ge=1, le=_KONG_TIMEOUT_MAX_MS)
    env: list[PluginEnvVar] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("plugin_manifest_version")
    @classmethod
    def _supported_version(cls, v: int) -> int:
        if v != SUPPORTED_MANIFEST_VERSION:
            raise ValueError(
                f"unsupported plugin_manifest_version {v!r}; this backend understands {SUPPORTED_MANIFEST_VERSION}"
            )
        return v

    @field_validator("name")
    @classmethod
    def _name_kebab(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[a-z][a-z0-9-]*[a-z0-9]", v):
            raise ValueError(f"name {v!r} must be kebab-case")
        return v

    @field_validator("route_prefix", "health_path")
    @classmethod
    def _valid_path(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # fullmatch against the shared grammar — same set the schema enforces.
        if not _PATH_RE.fullmatch(v):
            raise ValueError(
                f"path {v!r} must be one or more '/'-separated segments of "
                f"[A-Za-z0-9._~-] (bare '/' is not allowed)"
            )
        return v

    @field_validator("auth")
    @classmethod
    def _known_auth(cls, v: str) -> str:
        if v not in _AUTH_MODES:
            raise ValueError(f"auth {v!r} must be one of {', '.join(_AUTH_MODES)}")
        return v

    @property
    def prefix_head(self) -> str:
        """First path segment of route_prefix (``/tableau/x`` → ``tableau``)."""
        return self.route_prefix.strip("/").split("/", 1)[0]

    def env_summary(self, environ: dict[str, str]) -> list[dict[str, str]]:
        """Inventory-safe env view: secret values masked, presence resolved."""
        out: list[dict[str, str]] = []
        for spec in self.env:
            raw = environ.get(spec.name)
            if raw is None or raw == "":
                shown = "(unset)"
            elif spec.secret:
                shown = SECRET_MASK
            else:
                shown = raw
            out.append(
                {
                    "name": spec.name,
                    "type": spec.type,
                    "required": "true" if spec.required else "false",
                    "secret": "true" if spec.secret else "false",
                    "value": shown,
                }
            )
        return out


def load_manifest(plugin_dir: Path) -> Optional[PluginManifest]:
    """Load & validate ``<plugin_dir>/plugin.yml``.

    Returns ``None`` when the file is absent (manifest-less, today's behavior).
    Raises :class:`PluginManifestError` when the file is present but malformed
    (bad YAML, wrong shape, schema/version violation) — the caller skips that
    plugin and keeps the others healthy.
    """
    manifest_path = plugin_dir / PLUGIN_MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    plugin_hint = plugin_dir.name
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PluginManifestError(plugin_hint, f"could not parse YAML ({exc})") from exc
    if raw is None:
        raise PluginManifestError(plugin_hint, "manifest is empty")
    if not isinstance(raw, dict):
        raise PluginManifestError(plugin_hint, "manifest must be a mapping at the top level")
    try:
        manifest = PluginManifest.model_validate(raw)
    except ValidationError as exc:
        raise PluginManifestError(plugin_hint, _format_pydantic_error(exc)) from exc
    return manifest


def validate_env(manifest: PluginManifest, environ: dict[str, str]) -> list[str]:
    """Return human-readable warnings for the manifest's declared env contract.

    Never raises and never returns secret values — a missing secret is reported
    by name only. Warnings name the plugin and var so operators can act.
    """
    warnings: list[str] = []
    for spec in manifest.env:
        raw = environ.get(spec.name)
        present = raw is not None and raw != ""
        if not present:
            if spec.required:
                warnings.append(
                    f"plugin {manifest.name!r}: required env {spec.name} is not set"
                )
            continue
        assert raw is not None  # for type-checkers; present implies not None
        mismatch = _type_mismatch(spec, raw)
        if mismatch:
            # Never echo a secret value in the warning.
            shown = SECRET_MASK if spec.secret else repr(raw)
            warnings.append(
                f"plugin {manifest.name!r}: env {spec.name}={shown} {mismatch}"
            )
    return warnings


def _type_mismatch(spec: PluginEnvVar, value: str) -> Optional[str]:
    if spec.type == "int":
        try:
            int(value)
        except ValueError:
            return "is not a valid int"
    elif spec.type == "bool":
        if value.strip().lower() not in (_BOOL_TRUE | _BOOL_FALSE):
            return "is not a valid bool"
    elif spec.type == "enum":
        if spec.values and value not in spec.values:
            return f"is not one of the allowed values [{', '.join(spec.values)}]"
    return None


def _format_pydantic_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)
