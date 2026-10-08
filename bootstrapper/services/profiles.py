"""Deployment-profile bundle registry (#755).

Loads ``bootstrapper/profiles.yml`` — the declarative environment bundles a
``--profile`` (or a consumer manifest's ``profile:`` default) selects — and
merges consumer ``profile_overrides:`` on top (override-only: consumers may
override fields of the platform-defined ``default``/``prod`` bundles, never
define new profile names).

The applier (``AtlasStarter.apply_profile_overrides``) consumes the merged
bundles; this module owns parsing, validation, aliasing, and merge rules only,
so it stays import-light and unit-testable without a starter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from services.manifests import load_yaml_strict

# Canonical profile names — locked to the manifest options' `profiles:` tag
# vocabulary (service.schema.json enum) so `option_in_profile` /
# `validate_sources_for_profile` compose without translation.
CANONICAL_PROFILES = ("default", "prod")

# `dev` reads better in consumer manifests and matches the dev/prod framing;
# it is an alias, not a third bundle.
PROFILE_ALIASES = {"dev": "default"}

_ALLOWED_BUNDLE_FIELDS = frozenset({"host_bind_ip", "sources", "env"})
_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SERVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class ProfileConfigError(Exception):
    """Raised for a malformed profiles.yml or consumer profile_overrides."""


def canonical_profile(name: str | None) -> str:
    """Resolve aliases (``dev`` → ``default``); default the empty case."""
    resolved = PROFILE_ALIASES.get((name or "").strip().lower(), (name or "").strip().lower())
    return resolved or "default"


def is_known_profile(name: str | None) -> bool:
    return canonical_profile(name) in CANONICAL_PROFILES


@dataclass(frozen=True)
class ProfileBundle:
    """One profile's declarative environment set."""

    host_bind_ip: str | None = None  # None = field not declared (no bind management)
    sources: dict[str, str] = field(default_factory=dict)  # service-name -> option id | "auto"
    env: dict[str, str] = field(default_factory=dict)  # ENV_VAR -> value


def _env_value_text(value: object) -> str:
    """YAML booleans as true/false: str(True) wrote "True", which shell checks
    such as BACKUP_DATABASES reject (consumer env.values agrees)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _parse_bundle(name: str, raw: object, *, origin: str) -> ProfileBundle:
    if raw is None:
        return ProfileBundle()
    if not isinstance(raw, dict):
        raise ProfileConfigError(f"{origin}: profile '{name}' must be a mapping")
    unknown = set(map(str, raw.keys())) - _ALLOWED_BUNDLE_FIELDS
    if unknown:
        raise ProfileConfigError(
            f"{origin}: profile '{name}' has unknown field(s) {sorted(unknown)}; "
            f"allowed: {sorted(_ALLOWED_BUNDLE_FIELDS)}"
        )

    host_bind_ip: str | None = None
    if "host_bind_ip" in raw:
        value = raw["host_bind_ip"]
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ProfileConfigError(
                f"{origin}: profile '{name}' host_bind_ip must be a string"
            )
        host_bind_ip = value

    sources_raw = raw.get("sources", {})
    if not isinstance(sources_raw, dict):
        raise ProfileConfigError(
            f"{origin}: profile '{name}' sources must be a mapping"
        )
    sources: dict[str, str] = {}
    for svc, sid in sources_raw.items():
        svc_s, sid_s = str(svc), str(sid)
        if not _SERVICE_NAME_RE.match(svc_s):
            raise ProfileConfigError(
                f"{origin}: profile '{name}' sources key '{svc_s}' is not a "
                f"valid service name"
            )
        if sid_s != "auto" and not _SOURCE_ID_RE.match(sid_s):
            raise ProfileConfigError(
                f"{origin}: profile '{name}' sources.{svc_s}='{sid_s}' is not a "
                f"valid source id (or 'auto')"
            )
        sources[svc_s] = sid_s

    env_raw = raw.get("env", {})
    if not isinstance(env_raw, dict):
        raise ProfileConfigError(
            f"{origin}: profile '{name}' env must be a mapping"
        )
    env: dict[str, str] = {}
    for var, value in env_raw.items():
        var_s = str(var)
        if not _ENV_VAR_RE.match(var_s):
            raise ProfileConfigError(
                f"{origin}: profile '{name}' env key '{var_s}' is not a valid "
                f"env var name"
            )
        env[var_s] = _env_value_text(value)

    return ProfileBundle(host_bind_ip=host_bind_ip, sources=sources, env=env)


def load_profile_bundles(path: Path | None = None) -> dict[str, ProfileBundle]:
    """Load the platform profiles.yml into {canonical name: bundle}.

    A missing file yields empty bundles for the canonical names — the applier
    then manages nothing beyond legacy-compatible no-ops, keeping headless /
    stripped checkouts working.
    """
    if path is None:
        path = Path(__file__).resolve().parent.parent / "profiles.yml"
    if not path.exists():
        return {name: ProfileBundle() for name in CANONICAL_PROFILES}
    try:
        raw = load_yaml_strict(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProfileConfigError(f"{path}: invalid YAML — {exc}") from exc
    profiles_raw = raw.get("profiles")
    if not isinstance(profiles_raw, dict):
        raise ProfileConfigError(f"{path}: missing/invalid top-level 'profiles' map")
    bundles: dict[str, ProfileBundle] = {}
    for name, bundle_raw in profiles_raw.items():
        cname = canonical_profile(str(name))
        if cname not in CANONICAL_PROFILES:
            raise ProfileConfigError(
                f"{path}: unknown profile '{name}' (allowed: "
                f"{', '.join(CANONICAL_PROFILES)}; 'dev' aliases 'default')"
            )
        bundles[cname] = _parse_bundle(cname, bundle_raw, origin=str(path))
    for name in CANONICAL_PROFILES:
        bundles.setdefault(name, ProfileBundle())
    return bundles


def merge_consumer_profile_overrides(
    bundles: dict[str, ProfileBundle],
    overrides: dict[str, dict] | None,
    *,
    origin: str = "atlas.consumer.yml",
) -> dict[str, ProfileBundle]:
    """Apply a consumer manifest's ``profile_overrides:`` on top of the
    platform bundles (override-only — unknown profile names are rejected).

    ``sources`` and ``env`` merge per-key (consumer wins); ``host_bind_ip``
    replaces. Returns a new dict; inputs are not mutated.
    """
    if not overrides:
        return dict(bundles)
    merged = dict(bundles)
    for name, raw in overrides.items():
        cname = canonical_profile(str(name))
        if cname not in CANONICAL_PROFILES:
            raise ProfileConfigError(
                f"{origin}: profile_overrides names unknown profile '{name}' "
                f"(allowed: {', '.join(CANONICAL_PROFILES)}; 'dev' aliases "
                f"'default'). Consumers may override the platform profiles, "
                f"not define new ones."
            )
        override_bundle = _parse_bundle(cname, raw, origin=origin)
        base = merged.get(cname, ProfileBundle())
        merged[cname] = ProfileBundle(
            host_bind_ip=(
                override_bundle.host_bind_ip
                if override_bundle.host_bind_ip is not None
                else base.host_bind_ip
            ),
            sources={**base.sources, **override_bundle.sources},
            env={**base.env, **override_bundle.env},
        )
    return merged


def profile_source_map() -> dict[str, dict[str, str]]:
    """Each platform profile bundle's declared sources (manifest name -> option)."""
    try:
        return {name: dict(bundle.sources) for name, bundle in load_profile_bundles().items()}
    except ProfileConfigError:
        return {}


def profile_source_default(
    profile_sources: dict[str, dict[str, str]], mname: str, profile: str, fallback,
):
    """The source a wizard step should default to under ``profile``."""
    value = profile_sources.get(canonical_profile(profile), {}).get(mname)
    return value if value and value != "auto" else fallback


def profile_launch_sources(services_info, profile, config_parser, pinned) -> dict:
    """display name -> the source ``profile`` asserts for each service whose
    SOURCE is not in ``pinned`` (CLI, consumer manifest, .env.user), as
    apply_profile_overrides applies it at launch; the CLI-flag overview
    showed .env's value instead (prod: Prometheus "disabled", then started)."""
    if not profile:
        return {}
    from services.manifests import load_manifests

    root_dir = Path(config_parser.root_dir)
    try:
        mname_by_var = {m.sources.var: m.name for m in load_manifests(root_dir / "services") if m.sources is not None}
    except Exception:  # noqa: BLE001 - manifest errors surface elsewhere
        return {}
    sources = _launch_profile_sources(config_parser)
    out = {}
    for svc in services_info:
        var = getattr(svc, "env_var_name", "")
        value = profile_source_default(sources, mname_by_var.get(var, ""), profile, None)
        if value and var not in pinned:
            out[svc.display_name] = value
    return out


def _launch_profile_sources(config_parser) -> dict[str, dict[str, str]]:
    """The per-profile sources apply_profile_overrides uses: the platform
    bundles merged with the consumer manifest's profile_overrides (consumer
    wins per key). The platform map alone showed a source the consumer had
    overridden (prod Prometheus "container" while the launch kept it off)."""
    try:
        overrides = getattr(config_parser.load_consumer_config(), "profile_overrides", None) or {}
        bundles = merge_consumer_profile_overrides(load_profile_bundles(), overrides)
    except Exception:  # noqa: BLE001 - malformed manifests surface via doctor
        return profile_source_map()
    return {name: dict(bundle.sources) for name, bundle in bundles.items()}


def consumer_declared_sources(config_parser) -> frozenset:
    """Lower-case SOURCE vars a consumer manifest declares in env.values,
    computed as start.py does for --no-tui. A malformed manifest declares
    nothing here; it surfaces via `doctor`, not by blocking the wizard."""
    try:
        overrides = config_parser.load_consumer_config().env_overrides or {}
    except Exception:  # noqa: BLE001 — malformed manifests surface via doctor
        return frozenset()
    return frozenset(var.lower() for var in overrides if var.endswith("_SOURCE"))


def pinned_source_vars(config_parser, starter) -> set:
    """Upper-case SOURCE vars a consumer manifest or .env.user pins; they beat
    the profile (apply_profile_overrides)."""
    pinned = {key.upper() for key in consumer_declared_sources(config_parser)}
    pinned |= {key for key in (getattr(starter, "_env_user_keys", None) or ()) if key.endswith("_SOURCE")}
    return pinned
