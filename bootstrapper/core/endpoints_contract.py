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

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ServiceEndpoints:
    """One consumer-relevant service's endpoint sources."""

    service: str
    source_var: str | None = None
    container_var: str | None = None      # canonical in-network URL var
    container_secret: bool = False        # value embeds a secret (e.g. REDIS_URL)
    public_var: str | None = None         # browser-facing public base var
    host_port_var: str | None = None      # → http://localhost:${port}
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
        kong_alias="comfyui.localhost",
    ),
    ServiceEndpoints(
        service="OLLAMA",
        source_var="LLM_PROVIDER_SOURCE",
        container_var="OLLAMA_ENDPOINT",
        host_port_var="OLLAMA_PORT",
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


def _host_url(env: Mapping[str, str], port_var: str) -> str | None:
    port = env.get(port_var, "").strip()
    if not port:
        return None
    return f"http://localhost:{port}"


def _kong_url(env: Mapping[str, str], alias: str) -> str | None:
    port = env.get("KONG_HTTP_PORT", "").strip()
    if not port:
        return None
    return f"http://{alias}:{port}"


def build_export(
    env: Mapping[str, str],
    *,
    with_secrets: bool = False,
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
                    out.append(ExportField(f"{prefix}_CONTAINER_ENDPOINT", value))
        if svc.public_var:
            value = env.get(svc.public_var, "").strip()
            if value:
                out.append(ExportField(f"{prefix}_PUBLIC_ENDPOINT", value))
        if svc.host_port_var:
            host = _host_url(env, svc.host_port_var)
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
        out.append(ExportField("ATLAS_COMFYUI_OUTPUT_DIR", f"{state_dir}/ComfyUI/output"))

    return out


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
