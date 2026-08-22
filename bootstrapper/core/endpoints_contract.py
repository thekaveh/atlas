"""Stable, machine-readable consumer endpoint export contract (#345).

Downstream consumers that reuse Atlas over a shared network (submodule parents,
web/desktop shells, devservers) need to resolve service endpoints and their
active SOURCE modes without hand-grepping ``.env`` or re-deriving Atlas
internals. ``./start.sh endpoints export`` emits this contract.

The field NAMES here are a **stable compatibility contract** — renaming one is a
breaking change for consumers. Endpoints are grouped per service into distinct
named kinds so a consumer can pick the right URL for its context:

- ``ATLAS_<SVC>_SOURCE``            active SOURCE mode (container/localhost/disabled/…)
- ``ATLAS_<SVC>_CONTAINER_ENDPOINT`` in-network URL (e.g. ``http://minio:9000``)
- ``ATLAS_<SVC>_HOST_ENDPOINT``      host URL (e.g. ``http://localhost:63020``)
- ``ATLAS_<SVC>_KONG_ENDPOINT``      Kong ``*.localhost`` route (when exposed)
- ``ATLAS_<SVC>_PUBLIC_ENDPOINT``    browser-facing public read base (MinIO presigned reads)

Plus ``ATLAS_KONG_GATEWAY`` and every per-consumer ``ATLAS_STORE_*`` field from
the storage contract (#404). Secrets are emitted as ``${VAR}`` references by
default; ``--with-secrets`` resolves only consumer-scoped credentials (the
storage access/secret keys), never infra secrets such as the Redis password.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class HostPortOverride:
    """Per-source host-port resolution for host-process sources.

    For host-process sources (``localhost``, ``managed-localhost-mps``,
    ``ollama-localhost``) the compose *published* port is either dead (no
    container listens on it) or unset, so ``ATLAS_<SVC>_HOST_ENDPOINT`` must
    render from the port the host process actually serves on — not the default
    ``host_port_var``. This is the exporter-side sibling of #610's source-aware
    Kong route fix. ``default`` is the manifest's default host port, used when
    the resolved ``.env`` doesn't carry ``port_var`` (host-source port vars keep
    a fixed default and skip the topology slot allocator).
    """

    port_var: str
    default: str | None = None


@dataclass(frozen=True)
class ServiceEndpoints:
    """One consumer-relevant service's endpoint sources."""

    service: str
    source_var: str | None = None
    container_var: str | None = None      # canonical in-network URL var
    container_secret: bool = False        # value embeds a secret (e.g. REDIS_URL)
    public_var: str | None = None         # browser-facing public base var
    host_port_var: str | None = None      # → <host_scheme>://localhost:${port}
    #: Scheme for the HOST endpoint. `_managed_host_endpoint` already states
    #: the rule this exists to keep: "A raw-socket service exported as
    #: `http://` hands a consumer a URL no HTTP client can use." REDIS
    #: advertised `redis://…` for its container endpoint and `http://…` for
    #: its host endpoint — the same service disagreeing with itself.
    host_scheme: str = "http"
    # source value → host port for host-process sources (overrides host_port_var)
    host_port_overrides: Mapping[str, HostPortOverride] = field(default_factory=dict)
    kong_alias: str | None = None         # → http://<alias>:${KONG_HTTP_PORT}


# Curated, ordered contract. Aliases are the stable Kong *.localhost hostnames.
# The ``source_var`` for each row is the manifest's real SOURCE knob (verified
# against ``services/<name>/service.yml``) so a service the operator disabled
# (e.g. Ollama in cloud-only ``none`` mode, or Neo4j ``disabled``) advertises
# only its ``ATLAS_<SVC>_SOURCE`` and NO stale endpoints. Always-on tier
# services (Backend/LiteLLM/Redis/Supabase) still carry their real SOURCE var so
# consumers can read the active mode; those simply never hit the disable path.
CONSUMER_SERVICES: tuple[ServiceEndpoints, ...] = (
    ServiceEndpoints(
        service="BACKEND",
        source_var="BACKEND_SOURCE",
        host_port_var="BACKEND_PORT",
        kong_alias="api.localhost",
    ),
    ServiceEndpoints(
        service="LITELLM",
        source_var="LITELLM_SOURCE",
        container_var="LITELLM_BASE_URL",
        host_port_var="LITELLM_PORT",
        kong_alias="litellm.localhost",
    ),
    ServiceEndpoints(
        service="COMFYUI",
        source_var="COMFYUI_SOURCE",
        container_var="COMFYUI_ENDPOINT",
        host_port_var="COMFYUI_PORT",
        # Host-process sources serve on a fixed host port, not the (dead) compose
        # published COMFYUI_PORT: managed-localhost-mps → 8188, localhost → 8000.
        host_port_overrides={
            "localhost": HostPortOverride("COMFYUI_LOCALHOST_PORT", "8000"),
            "managed-localhost-mps": HostPortOverride(
                "COMFYUI_MPS_LOCALHOST_PORT", "8188"
            ),
        },
        kong_alias="comfyui.localhost",
    ),
    ServiceEndpoints(
        service="ASSET_WORKER",
        source_var="ASSET_WORKER_SOURCE",
        container_var="ASSET_WORKER_ENDPOINT",
        host_port_var="ASSET_WORKER_PORT",
    ),
    ServiceEndpoints(
        service="OLLAMA",
        source_var="LLM_PROVIDER_SOURCE",
        container_var="OLLAMA_ENDPOINT",
        host_port_var="OLLAMA_PORT",
        # ollama-localhost runs on the host; OLLAMA_PORT is unset under it, so the
        # host endpoint must come from the host listen port (default 11434).
        host_port_overrides={
            "ollama-localhost": HostPortOverride("OLLAMA_LOCALHOST_PORT", "11434"),
        },
        kong_alias="ollama.localhost",
    ),
    ServiceEndpoints(
        service="MINIO",
        source_var="MINIO_SOURCE",
        container_var="MINIO_ENDPOINT",
        public_var="MINIO_PUBLIC_ENDPOINT",
        host_port_var="MINIO_PORT",
        kong_alias="s3.minio.localhost",
    ),
    ServiceEndpoints(
        service="WEAVIATE",
        source_var="WEAVIATE_SOURCE",
        container_var="WEAVIATE_URL",
        host_port_var="WEAVIATE_PORT",
        # Under `localhost` the container is `scale: 0` (see the manifest's
        # runtime_sc), so the compose-published WEAVIATE_PORT is dead and the
        # host process serves on WEAVIATE_LOCALHOST_PORT — exactly the signal
        # HostPortOverride exists to consume. Without this the export handed
        # consumers a port nothing listens on.
        host_port_overrides={
            "localhost": HostPortOverride("WEAVIATE_LOCALHOST_PORT", "8080"),
        },
        kong_alias="weaviate.localhost",
    ),
    ServiceEndpoints(
        service="NEO4J",
        source_var="NEO4J_GRAPH_DB_SOURCE",
        container_var="NEO4J_URI",
        kong_alias="graph.localhost",
    ),
    ServiceEndpoints(
        service="N8N",
        source_var="N8N_SOURCE",
        host_port_var="N8N_PORT",
        kong_alias="n8n.localhost",
    ),
    ServiceEndpoints(
        service="REDIS",
        source_var="REDIS_SOURCE",
        container_var="REDIS_URL",
        container_secret=True,
        host_port_var="REDIS_PORT",
        host_scheme="redis",
    ),
    ServiceEndpoints(
        service="SUPABASE",
        source_var="SUPABASE_DB_SOURCE",
        host_port_var="SUPABASE_API_PORT",
        kong_alias="supabase-studio.localhost",
    ),
)

# Prefix for the per-consumer storage fields emitted by the #404 contract.
STORAGE_EXPORT_PREFIX = "ATLAS_STORE_"
# Suffixes of storage credential-reference fields (their target vars hold the
# consumer-scoped secret resolved only under --with-secrets).
_STORAGE_CRED_SUFFIXES = ("_ACCESS_KEY_VAR", "_SECRET_KEY_VAR")
# Suffixes of *resolved* credential-value fields. Atlas's generator never emits
# these bare forms (only the ``_VAR`` reference above), so a bare
# ``ATLAS_STORE_*_SECRET_KEY`` in the raw ``.env`` is hand-authored; mask its
# value in default output so a mis-named operator var can't leak.
_STORAGE_CRED_VALUE_SUFFIXES = ("_ACCESS_KEY", "_SECRET_KEY")
# A ``_VAR`` reference is only resolved under --with-secrets when its target var
# matches the consumer-scoped MinIO credential naming the #404 generator emits
# (``MINIO_<KEY>_ACCESS_KEY`` / ``MINIO_<KEY>_SECRET_KEY``). This makes the
# "never resolves an infra secret" guarantee an enforced invariant rather than a
# trusted-input assumption — a hand-mangled ``_VAR=REDIS_PASSWORD`` stays a
# reference, never a resolved value.
_STORAGE_CRED_TARGET_PREFIX = "MINIO_"

DISABLED_SOURCES = frozenset({"disabled", "none", ""})


def _is_scoped_cred_target(var_name: str) -> bool:
    """True iff ``var_name`` is a consumer-scoped MinIO credential var."""
    return var_name.startswith(_STORAGE_CRED_TARGET_PREFIX) and var_name.endswith(
        _STORAGE_CRED_VALUE_SUFFIXES
    )


@dataclass
class ExportField:
    name: str
    value: str
    secret: bool = False  # value is a ${ref}, not a resolved secret


# ``${VAR}``, ``${VAR:-default}``, ``${VAR-default}``, and bare ``$VAR``. Matches
# the compose-interpolation forms the source synthesizer writes into `.env`
# values (e.g. ``http://host.docker.internal:${COMFYUI_MPS_LOCALHOST_PORT:-8188}``).
_INTERP_RE = re.compile(
    r"\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?::?-(?P<default>[^}]*))?\}"
    r"|\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)"
)


def _expand_interpolation(value: str, env: Mapping[str, str]) -> str:
    """Resolve compose-style ``${…}`` / ``$VAR`` interpolations against ``env``.

    The source synthesizer stores compose-interpolation *strings* in `.env` (a
    host-source ComfyUI endpoint is ``http://host.docker.internal:${COMFYUI_MPS_LOCALHOST_PORT:-8188}``),
    which are correct through compose but a broken literal for a consumer reading
    the exported artifact raw (#646). Fully resolve them so no ``${…}`` survives
    in a non-secret exported value. An unset/empty variable falls back to the
    ``:-``/``-`` default (or empty). Bounded re-expansion handles the rare case
    where a resolved value itself contains an interpolation.
    """
    if not value or "$" not in value:
        return value

    def _sub(match: "re.Match[str]") -> str:
        braced = match.group("braced")
        if braced is not None:
            resolved = env.get(braced, "")
            if resolved:
                return resolved
            default = match.group("default")
            return default if default is not None else ""
        return env.get(match.group("bare"), "")

    result = value
    for _ in range(5):  # bounded: guards against a pathological self-reference
        expanded = _INTERP_RE.sub(_sub, result)
        if expanded == result:
            break
        result = expanded
    return result


def _host_url(
    env: Mapping[str, str],
    port_var: str,
    default: str | None = None,
    scheme: str = "http",
) -> str | None:
    port = env.get(port_var, "").strip() or (default or "")
    if not port:
        return None
    return f"{scheme}://localhost:{port}"


def _kong_url(env: Mapping[str, str], alias: str) -> str | None:
    port = env.get("KONG_HTTP_PORT", "").strip()
    if not port:
        return None
    return f"http://{alias}:{port}"


def build_export(
    env: Mapping[str, str],
    *,
    with_secrets: bool = False,
    host_services: "Sequence[Any] | None" = None,
) -> list[ExportField]:
    """Project the resolved .env into the stable endpoint contract.

    Disabled services contribute only their ``ATLAS_<SVC>_SOURCE`` (=disabled);
    their endpoint fields are omitted. Output ordering is deterministic.
    """
    out: list[ExportField] = []

    kong_port = env.get("KONG_HTTP_PORT", "").strip()
    if kong_port:
        out.append(ExportField("ATLAS_KONG_GATEWAY", f"http://localhost:{kong_port}"))

    for svc in CONSUMER_SERVICES:
        prefix = f"ATLAS_{svc.service}"
        source = None
        if svc.source_var:
            source = env.get(svc.source_var, "").strip()
            out.append(ExportField(f"{prefix}_SOURCE", source or "container"))
        # A service explicitly disabled contributes no endpoints.
        if source is not None and source in DISABLED_SOURCES:
            continue

        if svc.container_var:
            value = env.get(svc.container_var, "").strip()
            if value:
                if svc.container_secret:
                    # Infra secrets are ALWAYS references — --with-secrets
                    # resolves only consumer-scoped credentials, never these.
                    out.append(
                        ExportField(
                            f"{prefix}_CONTAINER_ENDPOINT",
                            f"${{{svc.container_var}}}",
                            secret=True,
                        )
                    )
                else:
                    out.append(
                        ExportField(
                            f"{prefix}_CONTAINER_ENDPOINT",
                            _expand_interpolation(value, env),
                        )
                    )
        if svc.public_var:
            value = env.get(svc.public_var, "").strip()
            if value:
                out.append(
                    ExportField(
                        f"{prefix}_PUBLIC_ENDPOINT", _expand_interpolation(value, env)
                    )
                )
        # HOST_ENDPOINT is source-aware (#643): host-process sources
        # (localhost / managed-localhost-mps / ollama-localhost) serve on a fixed
        # host port, not the compose published port, which is dead or unset there.
        host_port_var = svc.host_port_var
        host_port_default: str | None = None
        if source is not None and source in svc.host_port_overrides:
            override = svc.host_port_overrides[source]
            host_port_var = override.port_var
            host_port_default = override.default
        if host_port_var:
            host = _host_url(env, host_port_var, host_port_default, svc.host_scheme)
            if host:
                out.append(ExportField(f"{prefix}_HOST_ENDPOINT", host))
        if svc.kong_alias:
            kong = _kong_url(env, svc.kong_alias)
            if kong:
                out.append(ExportField(f"{prefix}_KONG_ENDPOINT", kong))

    out.extend(_storage_fields(env, with_secrets=with_secrets))

    # The managed Apple-Silicon/MPS ComfyUI source runs as a host process that
    # writes generated images to a host filesystem path (the native process's
    # output dir = ``$COMFYUI_MPS_STATE_DIR/ComfyUI/output``). Surface it so
    # consumers reading images off disk don't hardcode the internal layout (#612).
    # Other ComfyUI sources write into a container volume / unknown host path,
    # so the field is emitted only for managed-localhost-mps.
    if env.get("COMFYUI_SOURCE", "").strip() == "managed-localhost-mps":
        state_dir = env.get("COMFYUI_MPS_STATE_DIR", "").strip() or "~/.atlas/comfyui-mps"
        # Expand ~ at export time (#644): tilde expansion is a shell feature, so
        # a consumer reading the artifact programmatically would treat a literal
        # `~` as a directory name. Path.expanduser() is the same expansion the
        # MPS manager applies when launching the process (comfyui_mps_manager
        # `Path(state_dir).expanduser()`), so the artifact and the process agree.
        expanded = Path(state_dir).expanduser()
        out.append(ExportField("ATLAS_COMFYUI_OUTPUT_DIR", f"{expanded}/ComfyUI/output"))
        # Input dir (#758): the img2img / img2mesh staging path consumers write
        # INTO before submitting a workflow. Same managed-mps-only scoping —
        # under container sources `input` is a Docker named volume
        # (comfyui-input), not a host-writable path, so no field is emitted.
        out.append(ExportField("ATLAS_COMFYUI_INPUT_DIR", f"{expanded}/ComfyUI/input"))

    # Blender MCP (#758): a host-only bridge (virtual manifest, no container).
    # NOTE the scheme — the Blender add-on serves a RAW TCP socket, not HTTP,
    # so the field carries ``tcp://`` (matching the manifest's own
    # BLENDER_MCP_ENDPOINT hint). Exporting ``http://`` here would hand naive
    # consumers a URL that no HTTP client can use. Emitted for both host
    # sources — user-run (localhost) and Atlas-managed headless
    # (managed-localhost, #759).
    if env.get("BLENDER_MCP_SOURCE", "").strip() in ("localhost", "managed-localhost"):
        blender_port = env.get("BLENDER_MCP_LOCALHOST_PORT", "").strip() or "9876"
        out.append(
            ExportField(
                "ATLAS_BLENDER_MCP_HOST_ENDPOINT", f"tcp://localhost:{blender_port}"
            )
        )

    # #795: consumer-declared managed host processes. They never appear in
    # compose, so nothing above can find them — the specs are passed in from
    # the consumer manifest. Sorted by name so the export stays byte-stable
    # regardless of manifest discovery order.
    for spec in sorted(host_services or [], key=lambda s: s.name):
        out.append(ExportField(spec.endpoint_var, _managed_host_endpoint(spec)))

    return out


def _managed_host_endpoint(spec: Any) -> str:
    """``http://`` only when the declared probe proves HTTP.

    A raw-socket service exported as ``http://`` hands a consumer a URL no
    HTTP client can use — the mistake ``ATLAS_BLENDER_MCP_HOST_ENDPOINT``
    above exists to avoid.
    """
    scheme = "http" if getattr(spec.health, "kind", "tcp") == "http" else "tcp"
    return f"{scheme}://localhost:{spec.port}"


def _storage_fields(
    env: Mapping[str, str], *, with_secrets: bool
) -> list[ExportField]:
    """Pass through per-consumer ATLAS_STORE_* fields (#404). Credential
    references are resolved to values only under --with-secrets (consumer-scoped).
    """
    fields: list[ExportField] = []
    keys = sorted(k for k in env if k.startswith(STORAGE_EXPORT_PREFIX))
    for key in keys:
        value = env.get(key, "").strip()
        if key.endswith(_STORAGE_CRED_SUFFIXES):
            # value is the name of the var holding the scoped secret.
            if with_secrets and value and _is_scoped_cred_target(value):
                resolved = env.get(value, "").strip()
                base = key.rsplit("_VAR", 1)[0]  # ..._ACCESS_KEY_VAR → ..._ACCESS_KEY
                if resolved:
                    fields.append(ExportField(base, resolved))
                # keep the reference too for non-secret consumers
                fields.append(ExportField(key, value))
            else:
                # Default mode, or a --with-secrets reference pointing outside the
                # consumer-scoped MinIO namespace: keep it as a masked reference.
                fields.append(ExportField(key, value, secret=True))
        elif key.endswith(_STORAGE_CRED_VALUE_SUFFIXES):
            # Hand-authored bare credential value (Atlas never emits these).
            # Only surface it under --with-secrets; never leak in default output.
            if with_secrets:
                fields.append(ExportField(key, value))
            else:
                fields.append(ExportField(key, "", secret=True))
        else:
            fields.append(ExportField(key, value))
    return fields


def render_env(fields: list[ExportField]) -> str:
    """Deterministic env-file rendering (KEY=value, one per line)."""
    return "".join(f"{f.name}={f.value}\n" for f in fields)


def render_json(fields: list[ExportField]) -> str:
    """Deterministic JSON object rendering (sorted keys)."""
    import json

    return json.dumps({f.name: f.value for f in fields}, indent=2, sort_keys=True) + "\n"
