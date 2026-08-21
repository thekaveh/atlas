"""
Build AppState from the existing config — port assignments, SOURCE values,
hosts entries, brand metadata — so the renderable layer doesn't need to
know anything about .env, the per-service manifests, or the bootstrapper
internals.

Module-level helpers (`resolve_port`, `alias_for`, `lookup_service_meta`)
are also exposed so the wizard can mutate AppState in-place when the user
picks a new SOURCE for a service.
"""

from __future__ import annotations

import re

from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from core.config_parser import ConfigParser
from ui.state import AppState, CloudApiEntry, ConsumerEntry, ServiceEntry
from services.topology import get_topology, Topology


_SERVICES_ROOT = Path(__file__).resolve().parent.parent.parent / "services"


def _get_topology() -> Topology:
    """Back-compat shim: forward to the canonical cached accessor.

    Retained because legacy tests and a few module-private callers still
    import this name; new code should call ``services.topology.get_topology``
    directly.
    """
    return get_topology(_SERVICES_ROOT)


# Cloud LLM providers — API toggles, NOT services. Single source of
# truth in utils/cloud_providers.py. They live behind the LiteLLM
# gateway (no compose service of their own) and render in a dedicated
# "Cloud APIs" sub-section in the overview, not in the services grid.
from utils.cloud_providers import CLOUD_PROVIDERS as _CLOUD_PROVIDERS  # noqa: E402

_CLOUD_APIS = [
    (p.name, p.source_var, p.api_key_var) for p in _CLOUD_PROVIDERS
]


def lookup_service_meta(name: str) -> Optional[dict]:
    """Return {'name', 'source_var', 'port_var', 'scale_var'} for the given
    display name, or None if no row matches."""
    for r in _get_topology().rows:
        if r.display_name == name:
            return {
                "name": r.display_name,
                "source_var": r.source_var,
                "port_var": r.port_var,
                "scale_var": r.scale_var,
            }
    return None


@lru_cache(maxsize=1)
def _service_extras_map() -> dict:
    """display_name -> {'options': [source ids], 'depends': [service names]}.

    Built once from the manifests; drives the hover-card's 'Source options'
    and 'Depends on' rows. Cleared with the topology cache in tests.
    """
    from services.manifests import load_manifests

    out: dict = {}
    for m in load_manifests(_SERVICES_ROOT):
        manifest_opts = [o.id for o in m.sources.options] if m.sources else []
        manifest_var = m.sources.var if m.sources else None
        deps = list(m.depends_on.required) + list(m.depends_on.optional)
        for r in m.rows:
            opts = manifest_opts
            if r.source_var and r.source_var != manifest_var:
                opts = _row_source_options(m, r) or manifest_opts
            out[r.display_name] = {"options": opts, "depends": deps}
    return out


def _row_source_options(manifest, row) -> list:
    """Source options for a row whose SOURCE var is not the manifest's own.

    `weaviate` carries two rows — WEAVIATE_SOURCE and MULTI2VEC_CLIP_SOURCE —
    so stamping the manifest-level options onto every row advertised
    Weaviate's `container / localhost / disabled` on the Multi2Vec CLIP hover
    card. Every one of those was wrong: CLIP's real options are
    `container-cpu / container-gpu / disabled`, `localhost` is not offered for
    it at all, and both real container variants were missing.

    The `runtime_sc` slice keyed by the row's own stem is the authority — it is
    what the wizard itself reads to build the prompt.
    """
    stem = row.source_var.removesuffix("_SOURCE").lower().replace("_", "-")
    variants = (manifest.runtime_sc or {}).get(stem)
    return list(variants.keys()) if isinstance(variants, dict) else []


def service_extras(name: str) -> dict:
    """Source options + dependency names for a service display name."""
    return _service_extras_map().get(name, {"options": [], "depends": []})


#: `http://host:8188`, `http://host:${VAR}` and `http://host:${VAR:-8188}`.
_ENDPOINT_PORT_RE = re.compile(r":(?:(\d+)|\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(\d+))?\})(?:/|$)")


def resolve_localhost_port(row, env: dict) -> str:
    r"""The host port a localhost/managed-localhost row actually binds.

    THE definition, shared by the Textual `ServiceTable` and the `--no-tui`
    pre-launch summary. Two separate implementations had drifted apart:

    * This one consulted only `localhost_port_var`, so `vllm-metal` — which
      declares `localhost_endpoint_var` and no `localhost_port_var` — rendered
      an empty PORT cell while `--no-tui` printed `:8000`.
    * The other preferred the endpoint var but parsed it with a bare
      `:(\d+)`, which cannot match the stored literal
      `http://host.docker.internal:${COMFYUI_MPS_LOCALHOST_PORT:-8188}` —
      the `:` is followed by `$`. It fell through to `COMFYUI_LOCALHOST_PORT`
      (8000) even though the managed MPS process binds 8188, and the resulting
      phantom 8000-vs-8000 clash produced a FALSE port-collision warning
      against vLLM Metal.

    The endpoint var wins because it encodes the ACTIVE source variant's port;
    `localhost_port_var` is the fallback for rows that declare only a number.
    """
    endpoint_var = getattr(row, "localhost_endpoint_var", None)
    if endpoint_var:
        port = _port_from_endpoint(env.get(endpoint_var, "") or "", env)
        if port:
            return port
    port_var = getattr(row, "localhost_port_var", None)
    if port_var:
        return (env.get(port_var, "") or "").strip()
    return ""


def _port_from_endpoint(endpoint: str, env: dict) -> str:
    """Port from an endpoint URL, resolving `${VAR}` / `${VAR:-default}`."""
    match = _ENDPOINT_PORT_RE.search(endpoint)
    if not match:
        return ""
    literal, var_name, fallback = match.groups()
    if literal:
        return literal
    resolved = (env.get(var_name, "") or "").strip()
    return resolved or (fallback or "")


def resolve_port(name: str, source: str, port_var: Optional[str], env: dict) -> Optional[str]:
    """Compute the displayed port for a service given its current SOURCE, its
    port env var, and the parsed .env.

    For localhost sources the port comes from `resolve_localhost_port`, which
    prefers the row's ``localhost_endpoint_var`` (it encodes the ACTIVE source
    variant's port) and falls back to ``localhost_port_var``. Consulting only
    the latter reported ComfyUI-MPS on its plain-localhost port and left
    vLLM (Metal) — which declares no ``localhost_port_var`` — blank.
    Returns None when the var is unset or empty — the wizard's pending row
    state then surfaces the manifest default via .env.example backfill
    before the next read.
    """
    if source == "disabled":
        return None
    if "localhost" in source:
        for r in _get_topology().rows:
            if r.display_name == name:
                port = resolve_localhost_port(r, env)
                return f":{port}" if port else None
        return None
    if port_var:
        port = env.get(port_var, "")
        return f":{port}" if port else None
    return None


def alias_for(name: str) -> Optional[str]:
    """Hosts alias for a service display name, or None if no alias declared."""
    for r in _get_topology().rows:
        if r.display_name == name:
            return r.alias
    return None


def all_services():
    """Iterate canonical service tuples — display order from Topology.

    Yields (display_name, source_var, port_var, scale_var). Kept tuple-shaped
    for back-compat with start.py::build_pre_launch_summary_table.
    """
    return tuple(
        (r.display_name, r.source_var, r.port_var or None, r.scale_var or None)
        for r in _get_topology().rows
    )


def all_cloud_apis():
    """
    Iterate the canonical cloud-API definitions: yields
    (display_name, source_var, api_key_var) tuples. Single source of
    truth for the TUI overview, the no-TUI summary, and the wizard's
    secret-input prompt steps.
    """
    return tuple(_CLOUD_APIS)


def cloud_api_status_text(enabled: bool, key_set: bool) -> str:
    """Canonical display string for a cloud-API row's (enabled, key_set)
    pair. Shared by the Rich pre-launch panel in start.py and the
    Textual ``CloudApisRow`` so both render paths stay in lockstep —
    each renderer applies its own styling on top of the same string.
    """
    if enabled and key_set:
        return "enabled · key set ✓"
    if enabled and not key_set:
        return "enabled · key MISSING ⚠"
    return "disabled"


def _brand_field(env: dict, env_var: str, fallback: str) -> str:
    """Read a brand metadata value from .env, falling back to the canonical default."""
    value = env.get(env_var)
    return value if value else fallback


def build_app_state(
    config_parser: ConfigParser,
    hosts_manager=None,  # noqa: ARG001  # kept for back-compat with start.py / integration.py callers
    *,
    in_track: "Callable[[str], bool] | None" = None,
) -> AppState:
    """Build a fresh AppState snapshot from .env + per-service manifests.

    ``in_track`` is an optional predicate that receives a service display name
    and returns True when the service is included in the active track.  When
    provided (i.e. when a ``--track`` was selected), any service for which the
    predicate returns False will have its ``ServiceEntry.off_track`` flag set to
    True — driving the dim/annotation rendering in the service table.  Pass
    ``None`` (the default) to disable off-track marking entirely.
    """
    env = config_parser.parse_env_file()
    service_sources = config_parser.parse_service_sources()

    # Brand metadata — overridable via .env. Defaults match the canonical
    # Atlas project values declared on `AppState`.
    defaults = AppState()
    brand_name = _brand_field(env, "BRAND_NAME", defaults.brand_name)
    tagline = _brand_field(env, "BRAND_TAGLINE", defaults.tagline)
    version = _brand_field(env, "BRAND_VERSION", defaults.version)
    creator = _brand_field(env, "BRAND_AUTHOR", defaults.creator)
    creator_email = _brand_field(env, "BRAND_AUTHOR_EMAIL", defaults.creator_email)
    license_str = _brand_field(env, "BRAND_LICENSE", defaults.license)
    repo_url = _brand_field(env, "BRAND_REPO_URL", defaults.repo_url)

    services = []
    for r in _get_topology().rows:
        source = service_sources.get(r.source_var, env.get(r.source_var, "container"))
        # off_track: only mark when the caller supplied an in_track predicate
        # (i.e. a track is active) AND the predicate says this service is out.
        is_off = (in_track is not None) and (not in_track(r.display_name))
        services.append(ServiceEntry(
            name=r.display_name,
            port=resolve_port(r.display_name, source, r.port_var, env),
            source=source,
            alias=r.alias,
            category=r.category,
            pending=False,  # initial state; wizard sets True for unanswered rows
            off_track=is_off,
        ))

    cloud_apis = []
    for name, source_var, api_key_var in _CLOUD_APIS:
        source = (
            service_sources.get(source_var, env.get(source_var, "disabled")) or ""
        ).strip().lower()
        key_value = (env.get(api_key_var, "") or "").strip()
        cloud_apis.append(CloudApiEntry(
            name=name,
            source_var=source_var,
            api_key_var=api_key_var,
            enabled=(source == "enabled"),
            key_set=bool(key_value),
        ))

    consumer_entries = []
    for consumer in config_parser.load_consumer_config().consumers:
        consumer_entries.append(ConsumerEntry(
            name=consumer.name,
            manifest_path=str(consumer.manifest_path),
            compose_overlays=[str(path) for path in consumer.compose_overlays],
            backend_plugins=[str(path) for path in consumer.backend_plugins],
        ))

    return AppState(
        brand_name=brand_name,
        tagline=tagline,
        version=version,
        creator=creator,
        creator_email=creator_email,
        license=license_str,
        repo_url=repo_url,
        services=services,
        cloud_apis=cloud_apis,
        consumers=consumer_entries,
        kong_port=env.get("KONG_HTTP_PORT", "63000"),
    )
