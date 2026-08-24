"""
Service manifest loader for the per-service configuration layout.

Each `services/<name>/service.yml` is the single source of truth about one
service family (env vars, source variants, dependencies, image refs). This
module loads, validates, and exposes those manifests as typed dataclasses.

This module is intentionally side-effect-free: it does not touch the
environment, the bootstrapper state, or docker-compose. The
manifest_validator module performs the cross-manifest checks; this module
only handles per-file load + schema validation + per-manifest sanity checks.

Wired into start.py via services.topology and services.env_assembler — see
those modules for the consumer-side flow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaError


# ────────────────────────────────────────────────────────────────────────────
# Public dataclasses
# ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EnvVarDecl:
    """One entry from a manifest's `env:` list."""

    name: str
    default: Any = ""
    description: str = ""
    auto_managed: bool = False
    secret: bool = False
    # Scaffold field (no live consumer as of 2026-05-30). Original use case
    # was COMFYUI_CATALOG_CACHE_DIR (PR #17 T9), which was deleted in the
    # same PR's architectural pivot before the flag could be retired with
    # it. Kept as documented infrastructure for future bootstrapper-side
    # env vars that must not appear in .env.example — the assembler filter
    # in env_assembler.py and the synthetic-manifest tests at
    # test_env_example_bootstrapper_only.py keep the contract honest.
    bootstrapper_only: bool = False


@dataclass(frozen=True)
class ImageRef:
    """One entry from `images:`."""

    var: str
    default: str
    container: str
    notes: str = ""


@dataclass(frozen=True)
class SourceOption:
    """One option inside `sources.options`.

    Per-source runtime data (scale, environment, deploy, extra_hosts) lives
    in `manifest.runtime_sc.<sc_key>.<id>` — `runtime_sc` is the operational
    source consumed by the bootstrapper. The fields below are wizard-facing
    only (label for display, requires for input validation, profiles for
    deployment-profile gating).

    `profiles`: None means the option is available in all profiles (unannotated).
    A list restricts the option to the named profiles only (e.g. ["default"]
    marks a dev-only localhost option that is hidden/rejected under --profile prod).
    """

    id: str
    label: str
    requires: list[str] = field(default_factory=list)
    profiles: list[str] | None = None


@dataclass(frozen=True)
class AutoPreference:
    """One ordered entry in `sources.auto_prefer` (#753).

    Drives `<SVC>_SOURCE: auto` resolution: the first entry whose
    `requires_capability` holds on the host (per services/host_capabilities.py)
    wins. `requires_capability=None` means always eligible — the terminal
    fallback (place it last)."""

    id: str
    requires_capability: str | None = None


@dataclass(frozen=True)
class SourcesBlock:
    """The `sources:` block of a manifest, present only for source-configurable services."""

    var: str
    default: str
    options: list[SourceOption]
    auto_prefer: list[AutoPreference] = field(default_factory=list)


@dataclass(frozen=True)
class DependsOn:
    """Logical dependencies (NOT the compose-level depends_on, which lives in compose.yml)."""

    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExportRef:
    """An env var this manifest exports to other services."""

    name: str
    consumers: list[str]


@dataclass(frozen=True)
class Capability:
    """One operator-facing entry from a manifest's `capabilities:` list."""

    name: str
    status: str
    verification: str
    note: str


@dataclass(frozen=True)
class Row:
    """One box row a manifest renders. Replaces the legacy _SERVICES tuple
    plus several scattered constants. See spec §rows."""

    display_name: str
    source_var: str
    port_var: str = ""
    scale_var: str = ""
    alias: str = ""
    description: str = ""
    localhost_endpoint_var: str = ""
    # Env var holding the user-overridable host port for the localhost
    # source variant. Read by wizard.model.state_builder.resolve_port to show the
    # port column on localhost rows; written by the wizard via the
    # inline SecondaryNumberInput widget. Empty string means the
    # service has no overridable localhost port (mostly: services with
    # no localhost source variant, OR legacy services not yet migrated
    # to the LOCALHOST_PORT pattern).
    localhost_port_var: str = ""


@dataclass(frozen=True)
class Manifest:
    """Parsed services/<name>/service.yml."""

    name: str
    label: str
    category: str
    env: list[EnvVarDecl]
    containers: list[str] = field(default_factory=list)
    virtual: bool = False
    docs: str = ""
    images: list[ImageRef] = field(default_factory=list)
    sources: SourcesBlock | None = None
    depends_on: DependsOn = field(default_factory=DependsOn)
    exports: list[ExportRef] = field(default_factory=list)
    capabilities: list[Capability] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    # Extra *.localhost Kong hostnames beyond rows[].alias (e.g. minio's
    # s3.minio.localhost -> minio:9000). Not wizard rows; may be multi-label.
    # Projected into Topology.aliases for --setup-hosts wiring.
    extra_kong_aliases: list[str] = field(default_factory=list)
    # Slices of the legacy bootstrapper/service-configs.yml structure, owned
    # by this manifest. sc_synthesizer.synthesize_legacy() concatenates these
    # across manifests to produce the dict the bootstrapper used to load from
    # the YAML file. Loaded as opaque mappings; the synthesizer interprets
    # the shape (which mirrors service-configs.yml's `source_configurable:`,
    # `adaptive_services:`, and `service_dependencies:` blocks respectively).
    runtime_sc: dict = field(default_factory=dict)
    runtime_adaptive: dict = field(default_factory=dict)
    runtime_deps: dict = field(default_factory=dict)
    # Globals-only. Equivalent to the legacy `dependencies:` block.
    runtime_dependency_tiers: dict = field(default_factory=dict)
    doc_extras: dict = field(default_factory=dict)
    data_flow: dict = field(default_factory=dict)
    source_path: Path | None = None


# ────────────────────────────────────────────────────────────────────────────
# Errors
# ────────────────────────────────────────────────────────────────────────────


class ManifestLoadError(Exception):
    """Raised when one or more service.yml files fail to load or validate.

    The message lists every failure encountered in a single pass so the user
    can fix them all without playing whack-a-mole.
    """


# ────────────────────────────────────────────────────────────────────────────
# Schema loading (cached)
# ────────────────────────────────────────────────────────────────────────────


_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "service.schema.json"
_validator_singleton: Draft202012Validator | None = None


def _get_validator() -> Draft202012Validator:
    global _validator_singleton
    if _validator_singleton is None:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        _validator_singleton = Draft202012Validator(schema)
    return _validator_singleton


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def load_manifests(services_root: Path) -> list[Manifest]:
    """Discover, parse, and validate every manifest under services_root.

    Each immediate subfolder of services_root (skipping ones whose name starts
    with `_` or `.`) must contain a `service.yml`. Returns manifests sorted
    alphabetically by folder name for deterministic ordering.

    Raises ManifestLoadError with an aggregated message if any manifest fails.
    """

    services_root = Path(services_root)
    if not services_root.is_dir():
        return []

    errors: list[str] = []
    manifests: list[Manifest] = []
    candidates = sorted(p for p in services_root.iterdir() if _is_service_dir(p))

    for service_dir in candidates:
        try:
            manifests.append(_load_one(service_dir))
        except _PerManifestError as e:
            errors.append(str(e))

    if errors:
        raise ManifestLoadError(
            "Failed to load one or more service manifests:\n  - "
            + "\n  - ".join(errors)
        )
    return manifests


def option_in_profile(
    manifests: list[Manifest],
    service_key: str,
    option_id: str,
    profile: str,
) -> bool:
    """Return True if option_id is offered under the given deployment profile.

    An unannotated option (profiles=None) is available in ALL profiles.
    An annotated option (profiles=[...]) is available only in the listed profiles.

    Args:
        manifests:   The full manifest list returned by load_manifests().
        service_key: The service name (manifest.name), e.g. "comfyui".
        option_id:   The source option id, e.g. "localhost" or "container-cpu".
        profile:     The deployment profile to check, e.g. "prod" or "default".

    Returns True when the option is unannotated or lists the profile.
    Returns False when the option is annotated and does not list the profile.
    Returns True (permissive) when the service or option is not found, so
    callers don't need to guard against unknown ids.
    """
    for m in manifests:
        if m.name != service_key:
            continue
        if m.sources is None:
            return True
        for opt in m.sources.options:
            if opt.id == option_id:
                return opt.profiles is None or profile in opt.profiles
        return True  # option_id not found in this manifest — permissive
    return True  # service not found — permissive


# ────────────────────────────────────────────────────────────────────────────
# Internals
# ────────────────────────────────────────────────────────────────────────────


class _PerManifestError(Exception):
    """Internal exception type accumulated per-file then surfaced once."""


def _is_service_dir(path: Path) -> bool:
    if not path.is_dir() or path.name.startswith(("_", ".")):
        return False
    return (path / "service.yml").exists()


def _load_one(service_dir: Path) -> Manifest:
    manifest_path = service_dir / "service.yml"
    if not manifest_path.is_file():
        raise _PerManifestError(
            f"services/{service_dir.name}/service.yml is missing"
        )

    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise _PerManifestError(
            f"services/{service_dir.name}/service.yml: invalid YAML — {e}"
        ) from e

    if not isinstance(raw, dict):
        raise _PerManifestError(
            f"services/{service_dir.name}/service.yml: top-level must be a mapping, got {type(raw).__name__}"
        )

    # JSON-Schema validation collects every error in one pass.
    schema_errors = sorted(
        _get_validator().iter_errors(raw),
        key=lambda e: list(e.absolute_path),
    )
    if schema_errors:
        details = "; ".join(_format_jsonschema_error(e) for e in schema_errors)
        raise _PerManifestError(
            f"services/{service_dir.name}/service.yml: schema violation(s): {details}"
        )

    # Cross-field sanity (things JSON Schema can't express ergonomically).
    if raw["name"] != service_dir.name:
        raise _PerManifestError(
            f"services/{service_dir.name}/service.yml: manifest.name='{raw['name']}' "
            f"does not match folder name '{service_dir.name}'"
        )

    container_set = set(raw.get("containers", []))
    for img in raw.get("images") or []:
        if img["container"] not in container_set:
            raise _PerManifestError(
                f"services/{service_dir.name}/service.yml: images[].container='{img['container']}' "
                f"is not listed in containers={sorted(container_set)}"
            )

    sources = raw.get("sources")
    if sources is not None:
        option_ids = {opt["id"] for opt in sources["options"]}
        if sources["default"] not in option_ids:
            raise _PerManifestError(
                f"services/{service_dir.name}/service.yml: sources.default='{sources['default']}' "
                f"is not one of options[].id={sorted(option_ids)}"
            )

    return _to_dataclass(raw, manifest_path)


def _to_dataclass(raw: dict[str, Any], source_path: Path) -> Manifest:
    env = [
        EnvVarDecl(
            name=e["name"],
            default=e.get("default", ""),
            description=e.get("description", ""),
            auto_managed=bool(e.get("auto_managed", False)),
            secret=bool(e.get("secret", False)),
            bootstrapper_only=bool(e.get("bootstrapper_only", False)),
        )
        for e in raw.get("env", [])
    ]

    images = [
        ImageRef(
            var=i["var"],
            default=i["default"],
            container=i["container"],
            notes=i.get("notes", ""),
        )
        for i in raw.get("images") or []
    ]

    sources_raw = raw.get("sources")
    sources_block: SourcesBlock | None = None
    if sources_raw is not None:
        sources_block = SourcesBlock(
            var=sources_raw["var"],
            default=sources_raw["default"],
            options=[
                SourceOption(
                    id=opt["id"],
                    label=opt["label"],
                    requires=list(opt.get("requires", [])),
                    profiles=list(opt["profiles"]) if "profiles" in opt else None,
                )
                for opt in sources_raw["options"]
            ],
            auto_prefer=[
                AutoPreference(
                    id=pref["id"],
                    requires_capability=pref.get("requires_capability"),
                )
                for pref in sources_raw.get("auto_prefer") or []
            ],
        )

    deps_raw = raw.get("depends_on") or {}
    depends_on = DependsOn(
        required=list(deps_raw.get("required", [])),
        optional=list(deps_raw.get("optional", [])),
    )

    exports = [
        ExportRef(name=x["name"], consumers=list(x["consumers"]))
        for x in raw.get("exports") or []
    ]

    capabilities = [
        Capability(
            name=entry["name"],
            status=entry["status"],
            verification=entry["verification"],
            note=entry["note"],
        )
        for entry in raw.get("capabilities") or []
    ]

    rows = [
        Row(
            display_name=r["display_name"],
            source_var=r["source_var"],
            port_var=r.get("port_var", ""),
            scale_var=r.get("scale_var", ""),
            alias=r.get("alias", ""),
            description=r.get("description", ""),
            localhost_endpoint_var=r.get("localhost_endpoint_var", ""),
            localhost_port_var=r.get("localhost_port_var", ""),
        )
        for r in raw.get("rows") or []
    ]

    return Manifest(
        name=raw["name"],
        label=raw["label"],
        category=raw["category"],
        docs=raw.get("docs", ""),
        containers=list(raw.get("containers", [])),
        virtual=bool(raw.get("virtual", False)),
        env=env,
        images=images,
        sources=sources_block,
        depends_on=depends_on,
        exports=exports,
        capabilities=capabilities,
        rows=rows,
        extra_kong_aliases=list(raw.get("extra_kong_aliases") or []),
        runtime_sc=dict(raw.get("runtime_sc") or {}),
        runtime_adaptive=dict(raw.get("runtime_adaptive") or {}),
        runtime_deps=dict(raw.get("runtime_deps") or {}),
        runtime_dependency_tiers=dict(raw.get("runtime_dependency_tiers") or {}),
        doc_extras=dict(raw.get("doc_extras") or {}),
        data_flow=dict(raw.get("data_flow") or {}),
        source_path=source_path,
    )


def _format_jsonschema_error(err: JsonSchemaError) -> str:
    """Compact one-line summary of a jsonschema error."""
    path = "/".join(str(p) for p in err.absolute_path) or "<root>"
    return f"{path}: {err.message}"
