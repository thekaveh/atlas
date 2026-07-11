"""Consumer manifest loading for parent-owned Atlas integrations."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


class ConsumerManifestError(ValueError):
    """Raised when one or more consumer manifests are invalid."""


@dataclass(frozen=True)
class StorageStore:
    """One consumer-declared object store: a scoped MinIO service account
    over one primary bucket (plus optional extra buckets sharing the account).

    ``key`` is the sanitized, upper-snake identifier used for every generated
    env var (``MINIO_BUCKET_<KEY>``, ``MINIO_<KEY>_ACCESS_KEY``, …).
    ``consumer_id`` is the lower-hyphen id used as the first field of the
    ``MINIO_EXTRA_CONSUMERS`` entry (init-minio.sh policy/log name).
    """

    consumer: str
    name: str
    key: str
    consumer_id: str
    bucket: str
    extra_buckets: tuple[str, ...] = ()

    @property
    def bucket_var(self) -> str:
        return f"MINIO_BUCKET_{self.key}"

    @property
    def access_var(self) -> str:
        return f"MINIO_{self.key}_ACCESS_KEY"

    @property
    def secret_var(self) -> str:
        return f"MINIO_{self.key}_SECRET_KEY"

    def extra_bucket_var(self, index: int) -> str:
        return f"MINIO_BUCKET_{self.key}_EXTRA_{index}"

    @property
    def all_buckets(self) -> tuple[str, ...]:
        return (self.bucket, *self.extra_buckets)

    def extra_consumer_entry(self) -> str:
        """Render the init-minio.sh MINIO_EXTRA_CONSUMERS entry for this store."""
        entry = f"{self.consumer_id}:{self.bucket_var}:{self.access_var}:{self.secret_var}"
        if self.extra_buckets:
            extra_vars = ",".join(
                self.extra_bucket_var(i) for i in range(len(self.extra_buckets))
            )
            entry = f"{entry}:{extra_vars}"
        return entry


@dataclass(frozen=True)
class StorageOverlay:
    """An Atlas-generated compose overlay wiring declared storage vars into
    ``minio-init`` so the consumer never hand-writes a compose override."""

    path: Path
    content: str


@dataclass(frozen=True)
class LitellmModel:
    """One consumer-owned LiteLLM model row (#411).

    ``api_base`` is the *resolved* concrete in-network URL (Atlas endpoint
    templates already substituted); ``model`` is the OpenAI-compatible provider
    handle (``openai/<alias>``). ``api_key_var`` is a **reference** to an env var
    name — never a literal secret. ``consumer`` is the manifest-derived owner
    (non-spoofable); it is stamped into ``model_info.atlas_owner`` so a removed
    manifest can drop exactly its own rows.
    """

    consumer: str
    name: str
    api_base: str
    model: str
    api_key_var: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    model_info: Mapping[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        """Render the LiteLLM ``model_list`` entry (config.yaml shape)."""
        params: dict[str, Any] = {"model": self.model, "api_base": self.api_base}
        if self.api_key_var:
            # LiteLLM resolves ``os.environ/<VAR>`` at request time (same form
            # as the stack hermes-agent row). The secret VALUE never appears here.
            params["api_key"] = f"os.environ/{self.api_key_var}"
        # Ownership markers first so a consumer-supplied model_info cannot
        # override them (they are filtered out of the user block below).
        info: dict[str, Any] = {"atlas_owner": self.consumer, "atlas_managed": True}
        if self.description:
            info["description"] = self.description
        if self.tags:
            info["tags"] = list(self.tags)
        for key, value in self.model_info.items():
            if key not in ("atlas_owner", "atlas_managed"):
                info[str(key)] = value
        return {
            "model_name": self.name,
            "litellm_params": params,
            "model_info": info,
        }


@dataclass(frozen=True)
class GeneratedArtifact:
    """An Atlas-generated runtime artifact (path + content) written under the
    gitignored ``volumes/`` tree and regenerated every start (LiteLLM config,
    n8n seed plan, compose overlays, …)."""

    path: Path
    content: str


# Back-compat alias: #411 introduced this as ``LitellmArtifact``; the type is
# generic (any generated path+content artifact), so #412 reuses it under the
# neutral name while keeping the original name importable.
LitellmArtifact = GeneratedArtifact


@dataclass(frozen=True)
class N8nWebhookProbe:
    """A declared production-webhook readiness probe for a seeded workflow (#412).

    ``probe`` gates whether Atlas actually *calls* the endpoint — a GET/HEAD probe
    is safe to issue, but a POST probe can trigger workflow side effects, so it is
    opt-in (must be explicitly ``probe: true``). ``expect_status`` is the status
    that means "webhook registered / ready".
    """

    path: str
    method: str
    expect_status: int
    probe: bool


@dataclass(frozen=True)
class N8nWorkflow:
    """One consumer-owned n8n workflow to seed (#412).

    ``consumer`` is the manifest-derived owner (non-spoofable); ``id`` is a stable,
    globally-unique workflow id used as the idempotency key for import/update — so
    repeated startup never duplicates the workflow and a removed manifest drops
    only its own workflows. ``source_path`` is the resolved workflow JSON on the
    host; ``active`` is the activation policy (``fromJson`` | ``true`` | ``false``).
    """

    consumer: str
    id: str
    source_path: Path
    active: str
    checksum: str | None = None
    version: str | None = None
    webhooks: tuple[N8nWebhookProbe, ...] = ()

    @property
    def container_path(self) -> str:
        """Deterministic mount path inside the Atlas-owned n8n-seed container."""
        return f"/consumer-workflows/{self.id}.json"

    @property
    def seed_id(self) -> str:
        """The namespaced id the workflow is imported under in n8n's DB.

        The declared ``id`` is the manifest identity (globally unique across
        consumers); the imported DB id is prefixed with the Atlas-reserved
        ``atlas-consumer-`` namespace so an ``n8n import:workflow`` upsert can
        never collide with — and silently overwrite — a hand-created or
        stack-staged workflow whose id is unprefixed (e.g. ``research-simple``).
        """
        return f"{N8N_SEED_ID_NAMESPACE}{self.id}"


@dataclass(frozen=True)
class ConsumerRecord:
    name: str
    manifest_path: Path
    compose_overlays: tuple[Path, ...] = ()
    backend_plugins: tuple[Path, ...] = ()
    comfyui_sidecars: tuple[Path, ...] = ()
    ollama_models: tuple[str, ...] = ()
    storage: tuple[StorageStore, ...] = ()
    litellm_models: tuple[LitellmModel, ...] = ()
    n8n_workflows: tuple[N8nWorkflow, ...] = ()


@dataclass(frozen=True)
class ConsumerConfig:
    consumers: tuple[ConsumerRecord, ...] = ()
    env_overrides: dict[str, str] = field(default_factory=dict)
    compose_overlays: list[Path] = field(default_factory=list)
    storage: tuple[StorageStore, ...] = ()
    storage_overlay: StorageOverlay | None = None
    litellm_models: tuple[LitellmModel, ...] = ()
    litellm_models_file: LitellmArtifact | None = None
    litellm_overlay: LitellmArtifact | None = None
    n8n_workflows: tuple[N8nWorkflow, ...] = ()
    # Generated seed artifacts: the normalized per-workflow JSON files (id
    # rewritten to the stable declared id) plus the plan.json the seed reads.
    n8n_artifacts: tuple[GeneratedArtifact, ...] = ()
    n8n_overlay: GeneratedArtifact | None = None

    @property
    def is_empty(self) -> bool:
        return not self.consumers


_BRAND_ENV_MAP = {
    "name": "BRAND_NAME",
    "tagline": "BRAND_TAGLINE",
    "version": "BRAND_VERSION",
    "author": "BRAND_AUTHOR",
    "author_email": "BRAND_AUTHOR_EMAIL",
    "license": "BRAND_LICENSE",
    "repo_url": "BRAND_REPO_URL",
    "logo_file": "BRAND_LOGO_FILE",
}


def _invoker_relative_base(root_dir: Path) -> Path:
    invoker = os.environ.get("ATLAS_INVOKER_CWD", "").strip()
    if invoker:
        return Path(invoker).expanduser()
    return root_dir


def _split_manifest_env(raw: str) -> list[str]:
    pieces: list[str] = []
    for part in raw.split(os.pathsep):
        for item in part.split(","):
            item = item.strip()
            if item:
                pieces.append(item)
    return pieces


def discover_consumer_manifest_paths(
    root_dir: Path | str,
    *,
    explicit_paths: Iterable[str] | None = None,
) -> list[Path]:
    """Resolve consumer manifest paths from CLI paths or environment."""
    root = Path(root_dir)
    raw_paths = list(explicit_paths or [])
    if not raw_paths:
        env_value = os.environ.get("ATLAS_CONSUMER_MANIFEST", "").strip()
        raw_paths = _split_manifest_env(env_value) if env_value else []

    base_dir = _invoker_relative_base(root)
    resolved: list[Path] = []
    for raw in raw_paths:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        resolved.append(path.resolve())
    return resolved


def _read_env_overlay(path: Path) -> dict[str, str]:
    env_vars: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if value[:1] in ('"', "'"):
            quote = value[0]
            end = value.find(quote, 1)
            if end != -1:
                value = value[1:end]
            else:
                value = value.strip('"').strip("'")
        else:
            for i, ch in enumerate(value):
                if ch == "#" and (i == 0 or value[i - 1] in " \t"):
                    value = value[:i]
                    break
            value = value.strip()
        env_vars[key.strip()] = value
    return env_vars


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _resolve_existing_file(base_dir: Path, raw_path: str, *, label: str) -> Path:
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_file():
        raise ConsumerManifestError(f"{label} does not exist or is not a file: {path}")
    return path


def _resolve_existing_dir(base_dir: Path, raw_path: str, *, label: str) -> Path:
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_dir():
        raise ConsumerManifestError(f"{label} does not exist or is not a directory: {path}")
    return path


def _set_scalar(
    env: dict[str, str],
    origins: dict[str, str],
    key: str,
    value: Any,
    origin: str,
) -> None:
    rendered = str(value)
    if key in env and env[key] != rendered:
        raise ConsumerManifestError(
            f"{key} has conflicting consumer manifest values: "
            f"{origins[key]}={env[key]!r}, {origin}={rendered!r}"
        )
    env[key] = rendered
    origins[key] = origin


def _ordered_union(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        if not value or value in seen:
            continue
        out.append(value)
        seen.add(value)
    return out


def _load_manifest(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ConsumerManifestError(f"consumer manifest does not exist: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConsumerManifestError(f"could not parse consumer manifest {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ConsumerManifestError(f"consumer manifest must be a mapping: {path}")
    return data


# ─── Consumer object-storage contract (#404) ─────────────────────────
#
# A consumer declares object stores in its manifest; Atlas compiles each to
# the existing #409 ``MINIO_EXTRA_CONSUMERS`` provisioning grammar (no init
# logic is duplicated) and exports stable per-store endpoint/credential fields
# for #345 to wire. The generated overlay injects the dynamic bucket/key vars
# into ``minio-init`` so the consumer writes no compose override.

# Generated minio-init storage overlay lives under the gitignored volumes/
# runtime-artifacts tree (never hand-edited; regenerated every start).
MINIO_STORAGE_OVERLAY_PATH = Path("volumes/minio/consumer-storage.compose.yml")

# Default bucket names Atlas provisions for its own built-in consumers. A
# consumer store may not reuse one of these (they are operator-configurable
# via MINIO_BUCKET_*, but the defaults are the safe collision set).
_BUILTIN_BUCKET_NAMES = frozenset(
    {
        "comfyui", "backend", "n8n", "jupyter", "docling", "langfuse",
        "mlflow", "label-studio", "lakehouse", "jars", "checkpoints", "landing",
    }
)

_STORE_NAME_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9-]*$")
_BUCKET_NAME_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")
_IPV4_RE = __import__("re").compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _sanitize_token(value: str) -> str:
    """Upper-snake identifier for env-var names (MINIO_BUCKET_<TOKEN>)."""
    import re

    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()


def _sanitize_consumer_id(value: str) -> str:
    """Lower-hyphen id for the MINIO_EXTRA_CONSUMERS entry / policy name."""
    import re

    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _validate_bucket_name(name: str, *, origin: str) -> None:
    """Enforce S3-compatible bucket naming (MinIO path-style)."""
    if not (3 <= len(name) <= 63):
        raise ConsumerManifestError(
            f"storage bucket {name!r} must be 3-63 characters ({origin})"
        )
    if not _BUCKET_NAME_RE.match(name):
        raise ConsumerManifestError(
            f"storage bucket {name!r} must be lowercase alphanumeric with "
            f"hyphens/dots, starting and ending alphanumeric ({origin})"
        )
    if ".." in name or _IPV4_RE.match(name):
        raise ConsumerManifestError(
            f"storage bucket {name!r} may not contain '..' or be IP-formatted ({origin})"
        )


def _parse_storage_block(
    data: Mapping[str, Any], consumer_name: str, manifest_path: Path
) -> list[StorageStore]:
    """Parse + validate a manifest ``storage:`` block into StorageStore rows."""
    raw_storage = data.get("storage")
    if not raw_storage:
        return []
    if not isinstance(raw_storage, Mapping):
        raise ConsumerManifestError(f"storage must be a mapping in {manifest_path}")
    raw_buckets = raw_storage.get("buckets")
    if raw_buckets is None:
        return []
    if not isinstance(raw_buckets, list) or not raw_buckets:
        raise ConsumerManifestError(
            f"storage.buckets must be a non-empty list in {manifest_path}"
        )

    origin = str(manifest_path)
    consumer_id_base = _sanitize_consumer_id(consumer_name)
    if not consumer_id_base:
        raise ConsumerManifestError(
            f"consumer name {consumer_name!r} yields an empty storage id ({origin})"
        )

    stores: list[StorageStore] = []
    seen_names: set[str] = set()
    for raw in raw_buckets:
        if not isinstance(raw, Mapping):
            raise ConsumerManifestError(
                f"storage.buckets entries must be mappings in {manifest_path}"
            )
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ConsumerManifestError(
                f"storage bucket entry requires a name in {manifest_path}"
            )
        if not _STORE_NAME_RE.match(name):
            raise ConsumerManifestError(
                f"storage bucket name {name!r} must match [a-z0-9][a-z0-9-]* ({origin})"
            )
        if name in seen_names:
            raise ConsumerManifestError(
                f"duplicate storage bucket name {name!r} for consumer "
                f"{consumer_name!r} ({origin})"
            )
        seen_names.add(name)

        bucket = str(raw.get("bucket") or f"{consumer_id_base}-{name}").strip()
        _validate_bucket_name(bucket, origin=origin)
        extra_raw = _as_list(raw.get("extra_buckets"))
        extra_buckets: list[str] = []
        for extra in extra_raw:
            extra_name = str(extra).strip()
            _validate_bucket_name(extra_name, origin=origin)
            extra_buckets.append(extra_name)

        key = f"{_sanitize_token(consumer_name)}_{_sanitize_token(name)}"
        consumer_id = f"{consumer_id_base}-{name}"
        stores.append(
            StorageStore(
                consumer=consumer_name,
                name=name,
                key=key,
                consumer_id=consumer_id,
                bucket=bucket,
                extra_buckets=tuple(extra_buckets),
            )
        )
    return stores


def _validate_storage_collisions(stores: Iterable[StorageStore]) -> None:
    """Reject bucket-name, key, and consumer-id collisions across all stores."""
    bucket_owner: dict[str, str] = {}
    keys: set[str] = set()
    consumer_ids: set[str] = set()
    for store in stores:
        if store.key in keys:
            raise ConsumerManifestError(
                f"storage key collision: {store.key} declared by two stores"
            )
        keys.add(store.key)
        if store.consumer_id in consumer_ids:
            raise ConsumerManifestError(
                f"storage consumer-id collision: {store.consumer_id}"
            )
        consumer_ids.add(store.consumer_id)
        for bucket in store.all_buckets:
            if bucket in _BUILTIN_BUCKET_NAMES:
                raise ConsumerManifestError(
                    f"storage bucket {bucket!r} collides with a built-in Atlas bucket"
                )
            if bucket in bucket_owner and bucket_owner[bucket] != store.key:
                raise ConsumerManifestError(
                    f"storage bucket {bucket!r} declared by multiple stores "
                    f"({bucket_owner[bucket]} and {store.key})"
                )
            bucket_owner[bucket] = store.key


def compile_storage_provisioning(stores: Iterable[StorageStore]) -> dict[str, str]:
    """Compile stores to #409 provisioning env: bucket vars + MINIO_EXTRA_CONSUMERS.

    Credentials are intentionally NOT emitted here — they are generated once by
    KeyGenerator (blank-only) so restarts don't rotate scoped secrets.
    """
    env: dict[str, str] = {}
    entries: list[str] = []
    for store in stores:
        env[store.bucket_var] = store.bucket
        for i, extra in enumerate(store.extra_buckets):
            env[store.extra_bucket_var(i)] = extra
        entries.append(store.extra_consumer_entry())
    if entries:
        env["MINIO_EXTRA_CONSUMERS"] = " ".join(entries)
    return env


def compile_storage_exports(
    stores: Iterable[StorageStore],
    *,
    minio_endpoint: str,
    minio_public_endpoint: str,
    minio_region: str,
) -> dict[str, str]:
    """Stable per-store export fields consumed by #345 endpoint wiring.

    Endpoints are resolved values (so they track BASE_PORT/host changes);
    credentials are exported as *references* (var names) — never copied as raw
    secret values into another variable.
    """
    env: dict[str, str] = {}
    for store in stores:
        prefix = f"ATLAS_STORE_{store.key}"
        env[f"{prefix}_BUCKET"] = store.bucket
        env[f"{prefix}_INTERNAL_ENDPOINT"] = minio_endpoint
        env[f"{prefix}_PUBLIC_ENDPOINT"] = minio_public_endpoint
        env[f"{prefix}_REGION"] = minio_region
        env[f"{prefix}_ACCESS_KEY_VAR"] = store.access_var
        env[f"{prefix}_SECRET_KEY_VAR"] = store.secret_var
        if store.extra_buckets:
            env[f"{prefix}_EXTRA_BUCKETS"] = ",".join(store.extra_buckets)
    return env


def storage_credential_tokens(stores: Iterable[StorageStore]) -> list[str]:
    """Return the ``<KEY>`` tokens whose MINIO_<KEY>_ACCESS/SECRET_KEY vars the
    KeyGenerator must backfill (blank-only)."""
    out: list[str] = []
    seen: set[str] = set()
    for store in stores:
        if store.key not in seen:
            out.append(store.key)
            seen.add(store.key)
    return out


def render_minio_storage_overlay(stores: Iterable[StorageStore]) -> str:
    """Render a compose overlay that provisions the declared stores via
    ``minio-init``. The overlay — NOT ``.env`` — is the single source of truth
    for storage provisioning: bucket names are literal and the store's
    ``MINIO_EXTRA_CONSUMERS`` entries are appended to any operator/``_user``
    value via ``${MINIO_EXTRA_CONSUMERS:-}``. Because the overlay is regenerated
    (or removed) from the current manifest every start, removing a store leaves
    no dangling ``.env`` provisioning that would crash ``minio-init`` on a warm
    restart. Only the scoped credentials are sourced from ``.env`` (generated
    once, blank-only, so they persist across restarts).
    """
    stores = list(stores)
    prov = compile_storage_provisioning(stores)
    entries = prov.get("MINIO_EXTRA_CONSUMERS", "")
    lines = [
        "# AUTO-GENERATED by Atlas consumer storage contract (#404) — do not edit.",
        "# Provisions manifest-declared MinIO stores; regenerated every start.",
        "services:",
        "  minio-init:",
        "    environment:",
        # Merge with any operator/_user MINIO_EXTRA_CONSUMERS from .env.
        f'      MINIO_EXTRA_CONSUMERS: "${{MINIO_EXTRA_CONSUMERS:-}} {entries}"',
    ]
    for store in stores:
        # Bucket names are literal (source of truth is the manifest).
        lines.append(f'      {store.bucket_var}: "{store.bucket}"')
        for i, extra in enumerate(store.extra_buckets):
            lines.append(f'      {store.extra_bucket_var(i)}: "{extra}"')
        # Scoped credentials come from .env (persisted, blank-only).
        lines.append(f"      {store.access_var}: ${{{store.access_var}:-}}")
        lines.append(f"      {store.secret_var}: ${{{store.secret_var}:-}}")
    return "\n".join(lines) + "\n"


# ─── Consumer LiteLLM model contract (#411) ──────────────────────────
#
# A consumer declares OpenAI-compatible model aliases (typically served by its
# own #402 backend plugin routes) in a versioned ``litellm_models:`` block.
# Atlas resolves each ``api_base`` against an allowlist of approved in-network
# Atlas endpoints, stamps manifest-derived ownership, and compiles the rows to a
# generated file that ``litellm-init`` merges into the LiteLLM config BEFORE
# LiteLLM starts (declarative merge — no admin API calls). A companion compose
# overlay injects the referenced api-key env vars into the ``litellm`` container
# so it can resolve ``os.environ/<VAR>`` at request time. Both artifacts are
# regenerated every start, so removing a manifest drops only its rows.

# Generated LiteLLM artifacts live under the gitignored volumes/ runtime tree.
LITELLM_CONSUMER_MODELS_PATH = Path("volumes/litellm/consumer-models.yaml")
LITELLM_CONSUMER_OVERLAY_PATH = Path("volumes/litellm/consumer-models.compose.yml")

# Approved Atlas in-network endpoint templates for consumer ``api_base``. A
# consumer references a token (``${ATLAS_BACKEND_INTERNAL}``) and Atlas
# substitutes the concrete in-network base URL. This is an *allowlist*: the
# resolved host:port MUST be one of these Atlas services, so a generated LiteLLM
# row can never point at an arbitrary external host (SSRF / exfiltration surface)
# and arbitrary unresolved ``${...}`` interpolation or a URL that carries a
# credential (userinfo / query / fragment) is rejected. In-network URLs use fixed
# container-internal ports, so they are BASE_PORT-independent and resolve without env.
LITELLM_ENDPOINT_TEMPLATES: dict[str, str] = {
    # Atlas backend (FastAPI) — where #402 backend plugins mount their
    # OpenAI-compatible routes. The container listens on a fixed internal :8000.
    "ATLAS_BACKEND_INTERNAL": "http://backend:8000",
}

# The two runtime-stitched stack rows (see init.py hermes/lightrag). The full
# reserved set (``_reserved_litellm_aliases()``) unions these with every YAML
# catalog model name so a consumer can't hijack a stack model alias.
_RESERVED_LITELLM_ALIASES_BASE = frozenset({"hermes-agent", "lightrag"})

# Only these keys are accepted on a model entry; ``api_key`` (a literal secret)
# is rejected with a dedicated message pointing at ``api_key_var``.
_LITELLM_ALLOWED_MODEL_KEYS = frozenset(
    {"name", "api_base", "api_key_var", "description", "tags", "model_info", "owner"}
)

_LITELLM_ALIAS_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9._-]*$")
_LITELLM_ENV_VAR_RE = __import__("re").compile(r"^[A-Z][A-Z0-9_]*$")
_LITELLM_TOKEN_RE = __import__("re").compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _reserved_litellm_aliases() -> frozenset[str]:
    """Stack-owned aliases a consumer may not claim.

    Unions the two runtime-stitched rows (hermes-agent, lightrag) with every
    model name declared in the YAML catalogs (openai/anthropic/openrouter/ollama),
    so a consumer alias like ``gpt-4o`` or ``nomic-embed-text`` is rejected up
    front instead of silently shadowing the stack row in ``model_list``. The set
    is catalog-derived (single source of truth). A catalog-load failure degrades
    to the static pair — init.py's merge-time skip still protects the stack rows
    regardless (defense in depth), so this stays a fail-fast convenience.
    """
    reserved = set(_RESERVED_LITELLM_ALIASES_BASE)
    try:
        try:
            from utils import llm_catalog  # bootstrapper package context
        except ImportError:
            import llm_catalog  # container / loose-import context
        for entry in llm_catalog.all_catalog_entries():
            name = str(getattr(entry, "name", "")).strip().lower()
            if name:
                reserved.add(name)
    except Exception:  # noqa: BLE001 — best-effort; init.py is the hard guard
        pass
    return frozenset(reserved)


def _approved_litellm_hosts() -> set[str]:
    import urllib.parse

    return {
        urllib.parse.urlparse(base).netloc
        for base in LITELLM_ENDPOINT_TEMPLATES.values()
    }


def _resolve_litellm_api_base(raw: str, *, alias: str, origin: str) -> str:
    """Resolve + validate a consumer ``api_base`` to a concrete in-network URL.

    Substitutes approved ``${ATLAS_*}`` templates, then rejects (a) any remaining
    interpolation, (b) a URL carrying a credential — userinfo, query string, or
    fragment (structural, so no denylist to bypass), and (c) a resolved host that
    is not an approved Atlas endpoint. The result is a clean ``scheme://host/path``.
    """
    import urllib.parse

    value = str(raw).strip()
    if not value:
        raise ConsumerManifestError(
            f"litellm_models entry {alias!r} requires a non-empty api_base ({origin})"
        )

    def _sub(match: "Any") -> str:
        token = match.group(1)
        if token not in LITELLM_ENDPOINT_TEMPLATES:
            raise ConsumerManifestError(
                f"litellm_models entry {alias!r} references unapproved endpoint "
                f"template ${{{token}}}; approved: "
                f"{', '.join(sorted(LITELLM_ENDPOINT_TEMPLATES))} ({origin})"
            )
        return LITELLM_ENDPOINT_TEMPLATES[token]

    resolved = _LITELLM_TOKEN_RE.sub(_sub, value)
    if "${" in resolved or "$(" in resolved:
        raise ConsumerManifestError(
            f"litellm_models entry {alias!r} api_base has unresolved interpolation "
            f"after template substitution: {resolved!r} ({origin})"
        )
    parsed = urllib.parse.urlparse(resolved)
    if parsed.scheme not in ("http", "https"):
        raise ConsumerManifestError(
            f"litellm_models entry {alias!r} api_base must be an http(s) URL: "
            f"{resolved!r} ({origin})"
        )
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise ConsumerManifestError(
            f"litellm_models entry {alias!r} api_base may not embed userinfo "
            f"credentials ({origin})"
        )
    # A LiteLLM api_base is a clean base URL (scheme://host/path). A query string
    # or fragment has no legitimate role here and is the usual carrier for a
    # credential literal (?authorization=…, ?api_key=…, #token). Reject the whole
    # class STRUCTURALLY rather than denylisting parameter names — a denylist
    # ("api_key=" only) misses "authorization=", bare bearer tokens, and %-encoded
    # variants, leaking the secret into the generated file and the init log.
    if parsed.query or parsed.fragment:
        raise ConsumerManifestError(
            f"litellm_models entry {alias!r} api_base may not contain a query string "
            f"or fragment (credentials belong in api_key_var, not the URL): "
            f"{resolved!r} ({origin})"
        )
    allowed = _approved_litellm_hosts()
    if parsed.netloc not in allowed:
        raise ConsumerManifestError(
            f"litellm_models entry {alias!r} api_base host {parsed.netloc!r} is not an "
            f"approved Atlas endpoint; resolve via one of "
            f"{', '.join('${' + t + '}' for t in sorted(LITELLM_ENDPOINT_TEMPLATES))} "
            f"(approved hosts: {', '.join(sorted(allowed))}) ({origin})"
        )
    # Strip a trailing slash for byte-stable output; LiteLLM expects a bare base.
    return resolved.rstrip("/")


def _parse_litellm_models_block(
    data: Mapping[str, Any], consumer_name: str, manifest_path: Path
) -> list[LitellmModel]:
    """Parse + validate a manifest ``litellm_models:`` block into rows."""
    raw_block = data.get("litellm_models")
    if not raw_block:
        return []
    origin = str(manifest_path)
    if not isinstance(raw_block, Mapping):
        raise ConsumerManifestError(f"litellm_models must be a mapping in {manifest_path}")
    version = raw_block.get("version")
    if version != 1:
        raise ConsumerManifestError(
            f"litellm_models.version must be 1 (got {version!r}) in {manifest_path}"
        )
    raw_models = raw_block.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise ConsumerManifestError(
            f"litellm_models.models must be a non-empty list in {manifest_path}"
        )

    reserved = _reserved_litellm_aliases()
    models: list[LitellmModel] = []
    seen: set[str] = set()
    for raw in raw_models:
        if not isinstance(raw, Mapping):
            raise ConsumerManifestError(
                f"litellm_models.models entries must be mappings in {manifest_path}"
            )
        keys = {str(k) for k in raw.keys()}
        if "api_key" in keys:
            raise ConsumerManifestError(
                f"litellm_models entry may not set a literal api_key; use api_key_var "
                f"(an env var NAME) for secret references ({origin})"
            )
        unknown = keys - _LITELLM_ALLOWED_MODEL_KEYS
        if unknown:
            raise ConsumerManifestError(
                f"litellm_models entry has unknown field(s) {sorted(unknown)}; allowed: "
                f"{sorted(_LITELLM_ALLOWED_MODEL_KEYS)} ({origin})"
            )
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ConsumerManifestError(
                f"litellm_models entry requires a name in {manifest_path}"
            )
        if not _LITELLM_ALIAS_RE.match(name):
            raise ConsumerManifestError(
                f"litellm_models alias {name!r} must match [a-z0-9][a-z0-9._-]* ({origin})"
            )
        if name in reserved:
            raise ConsumerManifestError(
                f"litellm_models alias {name!r} is reserved for a stack-owned model "
                f"(a runtime row or a YAML catalog model) — pick a distinct alias ({origin})"
            )
        if name in seen:
            raise ConsumerManifestError(
                f"duplicate litellm_models alias {name!r} for consumer "
                f"{consumer_name!r} ({origin})"
            )
        seen.add(name)

        # Ownership is derived from the manifest; an explicit owner may only
        # RESTATE the consumer's own name, never claim another's.
        owner = raw.get("owner")
        if owner is not None and str(owner).strip() != consumer_name:
            raise ConsumerManifestError(
                f"litellm_models entry {name!r} declares owner {str(owner)!r} but "
                f"ownership is derived from the manifest ({consumer_name!r}) and cannot "
                f"be spoofed ({origin})"
            )

        raw_api_base = raw.get("api_base")
        if raw_api_base is None:
            raise ConsumerManifestError(
                f"litellm_models entry {name!r} requires an api_base ({origin})"
            )
        api_base = _resolve_litellm_api_base(str(raw_api_base), alias=name, origin=origin)

        api_key_var = raw.get("api_key_var")
        if api_key_var is not None:
            api_key_var = str(api_key_var).strip()
            if not _LITELLM_ENV_VAR_RE.match(api_key_var):
                raise ConsumerManifestError(
                    f"litellm_models entry {name!r} api_key_var {api_key_var!r} must be an "
                    f"UPPER_SNAKE env var NAME (a reference, not a literal secret) ({origin})"
                )

        description = raw.get("description")
        if description is not None:
            description = str(description)
        tags = tuple(
            str(tag).strip() for tag in _as_list(raw.get("tags")) if str(tag).strip()
        )
        model_info_raw = raw.get("model_info") or {}
        if model_info_raw and not isinstance(model_info_raw, Mapping):
            raise ConsumerManifestError(
                f"litellm_models entry {name!r} model_info must be a mapping ({origin})"
            )

        models.append(
            LitellmModel(
                consumer=consumer_name,
                name=name,
                api_base=api_base,
                model=f"openai/{name}",
                api_key_var=api_key_var or None,
                description=description,
                tags=tags,
                model_info=dict(model_info_raw),
            )
        )
    return models


def _validate_litellm_collisions(models: Iterable[LitellmModel]) -> None:
    """Reject alias collisions across every consumer (aliases are globally unique
    in the single generated LiteLLM config)."""
    owner: dict[str, str] = {}
    for model in models:
        if model.name in owner and owner[model.name] != model.consumer:
            raise ConsumerManifestError(
                f"litellm_models alias {model.name!r} declared by multiple consumers "
                f"({owner[model.name]} and {model.consumer})"
            )
        if model.name in owner:
            raise ConsumerManifestError(
                f"duplicate litellm_models alias {model.name!r} for consumer "
                f"{model.consumer!r}"
            )
        owner[model.name] = model.consumer


def compile_litellm_model_rows(models: Iterable[LitellmModel]) -> list[dict[str, Any]]:
    """Compile rows to the LiteLLM ``model_list`` shape (deterministic order)."""
    return [model.to_row() for model in models]


def render_litellm_models_file(models: Iterable[LitellmModel]) -> str:
    """Render the generated ``consumer-models.yaml`` merged by litellm-init."""
    rows = compile_litellm_model_rows(models)
    body = yaml.safe_dump({"model_list": rows}, sort_keys=False, default_flow_style=False)
    header = (
        "# AUTO-GENERATED by Atlas consumer LiteLLM contract (#411) — do not edit.\n"
        "# Consumer-owned model rows merged into the LiteLLM config by\n"
        "# services/litellm/init/scripts/init.py BEFORE LiteLLM starts.\n"
        "# Regenerated every ./start.sh from the consumer manifest(s); a removed\n"
        "# manifest removes only its rows — stack rows (hermes/lightrag) untouched.\n"
    )
    return header + body


def litellm_credential_vars(models: Iterable[LitellmModel]) -> list[str]:
    """Ordered-unique api-key var NAMES the litellm container must receive."""
    out: list[str] = []
    seen: set[str] = set()
    for model in models:
        if model.api_key_var and model.api_key_var not in seen:
            out.append(model.api_key_var)
            seen.add(model.api_key_var)
    return out


def render_litellm_models_overlay(models: Iterable[LitellmModel]) -> str | None:
    """Render a compose overlay injecting consumer api-key *references* into the
    ``litellm`` container. Returns None when no model declares an api_key_var
    (nothing to inject). Only var NAMES appear — never resolved secret values."""
    cred_vars = litellm_credential_vars(models)
    if not cred_vars:
        return None
    lines = [
        "# AUTO-GENERATED by Atlas consumer LiteLLM contract (#411) — do not edit.",
        "# Injects consumer-declared api-key references into the litellm container",
        "# so it resolves os.environ/<VAR> at request time. Regenerated every start.",
        "services:",
        "  litellm:",
        "    environment:",
    ]
    for var in cred_vars:
        # Reference only — the value is sourced from .env (consumer-supplied).
        lines.append(f"      {var}: ${{{var}:-}}")
    return "\n".join(lines) + "\n"


# ─── Consumer n8n workflow seeding contract (#412) ───────────────────
#
# A consumer declares n8n workflows to seed in a versioned ``n8n_workflows``
# block. Atlas normalizes each workflow JSON to a stable, consumer-owned id (the
# idempotency key), strips it of embedded credential material, compiles a seed
# plan, and generates a compose overlay that runs an Atlas-owned ``n8n-seed``
# container (the n8n image, sharing the n8n schema) which imports/updates each
# workflow AFTER n8n is healthy — keyed by the stable id so repeated startup
# never duplicates an active workflow and never touches another consumer's or a
# user's workflow. Declared production webhooks can be probed for readiness
# (opt-in; a POST probe requires explicit ``probe: true`` because it can trigger
# side effects). All artifacts are regenerated every start, so removing a
# manifest drops only that consumer's workflows.

# Generated artifacts live under the gitignored volumes/ runtime tree.
N8N_CONSUMER_WORKFLOWS_DIR = Path("volumes/n8n/consumer-workflows")
N8N_CONSUMER_PLAN_PATH = N8N_CONSUMER_WORKFLOWS_DIR / "plan.json"
N8N_CONSUMER_OVERLAY_PATH = Path("volumes/n8n/consumer-workflows.compose.yml")

# Atlas-reserved id namespace for seeded workflows. Every imported workflow's DB
# id is prefixed with this so an upsert can never collide with a user/stack
# workflow — Atlas owns (and may reconcile/delete) exactly the ids under it.
N8N_SEED_ID_NAMESPACE = "atlas-consumer-"

# Top-level workflow fields stripped during normalization: they carry runtime
# state / pinned execution payloads (a secret-leak carrier) and have no place in
# a fresh seed template.
_N8N_STRIPPED_FIELDS = ("staticData", "pinData")

_N8N_ID_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9._-]*$")
_N8N_ACTIVE_POLICIES = frozenset({"fromJson", "true", "false"})
_N8N_WEBHOOK_METHODS = frozenset({"GET", "HEAD", "POST"})
_N8N_ALLOWED_WORKFLOW_KEYS = frozenset(
    {"id", "path", "active", "checksum", "version", "required_webhooks", "owner"}
)
_N8N_ALLOWED_WEBHOOK_KEYS = frozenset({"path", "method", "expect_status", "probe"})


def _parse_n8n_webhook(
    raw: Any, *, workflow_id: str, origin: str
) -> N8nWebhookProbe:
    if not isinstance(raw, Mapping):
        raise ConsumerManifestError(
            f"n8n_workflows[{workflow_id!r}].required_webhooks entries must be "
            f"mappings ({origin})"
        )
    unknown = {str(k) for k in raw.keys()} - _N8N_ALLOWED_WEBHOOK_KEYS
    if unknown:
        raise ConsumerManifestError(
            f"n8n_workflows[{workflow_id!r}] webhook has unknown field(s) "
            f"{sorted(unknown)}; allowed: {sorted(_N8N_ALLOWED_WEBHOOK_KEYS)} ({origin})"
        )
    path = str(raw.get("path") or "").strip()
    if not path.startswith("/"):
        raise ConsumerManifestError(
            f"n8n_workflows[{workflow_id!r}] webhook path {path!r} must start with "
            f"'/' ({origin})"
        )
    method = str(raw.get("method") or "GET").strip().upper()
    if method not in _N8N_WEBHOOK_METHODS:
        raise ConsumerManifestError(
            f"n8n_workflows[{workflow_id!r}] webhook method {method!r} must be one of "
            f"{sorted(_N8N_WEBHOOK_METHODS)} ({origin})"
        )
    raw_status = raw.get("expect_status", 200)
    if isinstance(raw_status, bool) or not isinstance(raw_status, int):
        raise ConsumerManifestError(
            f"n8n_workflows[{workflow_id!r}] webhook expect_status must be an integer "
            f"({origin})"
        )
    raw_probe = raw.get("probe", False)
    if not isinstance(raw_probe, bool):
        raise ConsumerManifestError(
            f"n8n_workflows[{workflow_id!r}] webhook probe must be a boolean ({origin})"
        )
    # A POST probe can trigger workflow side effects, so it is opt-in: it is only
    # actually issued when explicitly ``probe: true``. (Non-probe POST webhooks
    # are still tracked for route-collision detection but never called.)
    return N8nWebhookProbe(
        path=path, method=method, expect_status=int(raw_status), probe=bool(raw_probe)
    )


def _parse_n8n_workflows_block(
    data: Mapping[str, Any],
    consumer_name: str,
    base_dir: Path,
    manifest_path: Path,
) -> list[N8nWorkflow]:
    """Parse + validate a manifest ``n8n_workflows:`` block into rows."""
    raw_block = data.get("n8n_workflows")
    if not raw_block:
        return []
    origin = str(manifest_path)
    if not isinstance(raw_block, Mapping):
        raise ConsumerManifestError(f"n8n_workflows must be a mapping in {manifest_path}")
    version = raw_block.get("version")
    if version != 1:
        raise ConsumerManifestError(
            f"n8n_workflows.version must be 1 (got {version!r}) in {manifest_path}"
        )
    raw_workflows = raw_block.get("workflows")
    if not isinstance(raw_workflows, list) or not raw_workflows:
        raise ConsumerManifestError(
            f"n8n_workflows.workflows must be a non-empty list in {manifest_path}"
        )

    workflows: list[N8nWorkflow] = []
    seen: set[str] = set()
    for raw in raw_workflows:
        if not isinstance(raw, Mapping):
            raise ConsumerManifestError(
                f"n8n_workflows.workflows entries must be mappings in {manifest_path}"
            )
        unknown = {str(k) for k in raw.keys()} - _N8N_ALLOWED_WORKFLOW_KEYS
        if unknown:
            raise ConsumerManifestError(
                f"n8n_workflows entry has unknown field(s) {sorted(unknown)}; allowed: "
                f"{sorted(_N8N_ALLOWED_WORKFLOW_KEYS)} ({origin})"
            )
        wid = str(raw.get("id") or "").strip()
        if not wid:
            raise ConsumerManifestError(
                f"n8n_workflows entry requires a stable id in {manifest_path}"
            )
        if not _N8N_ID_RE.match(wid):
            raise ConsumerManifestError(
                f"n8n_workflows id {wid!r} must match [a-z0-9][a-z0-9._-]* ({origin})"
            )
        if wid in seen:
            raise ConsumerManifestError(
                f"duplicate n8n_workflows id {wid!r} for consumer {consumer_name!r} "
                f"({origin})"
            )
        seen.add(wid)

        # Ownership is derived from the manifest; an explicit owner may only
        # RESTATE the consumer's own name, never claim another's.
        owner = raw.get("owner")
        if owner is not None and str(owner).strip() != consumer_name:
            raise ConsumerManifestError(
                f"n8n_workflows entry {wid!r} declares owner {str(owner)!r} but ownership "
                f"is derived from the manifest ({consumer_name!r}) and cannot be spoofed "
                f"({origin})"
            )

        raw_path = raw.get("path")
        if raw_path is None:
            raise ConsumerManifestError(
                f"n8n_workflows entry {wid!r} requires a path ({origin})"
            )
        source_path = _resolve_existing_file(
            base_dir, str(raw_path), label=f"n8n_workflows[{wid!r}].path"
        )

        active = str(raw.get("active") or "fromJson").strip()
        if active not in _N8N_ACTIVE_POLICIES:
            raise ConsumerManifestError(
                f"n8n_workflows entry {wid!r} active {active!r} must be one of "
                f"{sorted(_N8N_ACTIVE_POLICIES)} ({origin})"
            )

        checksum = raw.get("checksum")
        if checksum is not None:
            checksum = str(checksum).strip()
            _validate_n8n_checksum(source_path, checksum, workflow_id=wid, origin=origin)

        version_field = raw.get("version")
        version_field = None if version_field is None else str(version_field)

        webhooks = tuple(
            _parse_n8n_webhook(w, workflow_id=wid, origin=origin)
            for w in _as_list(raw.get("required_webhooks"))
        )

        # Load + validate the workflow JSON (parseable, mapping, credential-safe).
        _load_and_check_workflow_json(source_path, workflow_id=wid, origin=origin)

        workflows.append(
            N8nWorkflow(
                consumer=consumer_name,
                id=wid,
                source_path=source_path,
                active=active,
                checksum=checksum,
                version=version_field,
                webhooks=webhooks,
            )
        )
    return workflows


def _validate_n8n_checksum(
    path: Path, checksum: str, *, workflow_id: str, origin: str
) -> None:
    """Verify an optional ``sha256:<hex>`` checksum against the workflow file."""
    import hashlib

    algo, _, expected = checksum.partition(":")
    if algo.lower() != "sha256" or not expected:
        raise ConsumerManifestError(
            f"n8n_workflows[{workflow_id!r}] checksum must be 'sha256:<hex>' ({origin})"
        )
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected.strip().lower():
        raise ConsumerManifestError(
            f"n8n_workflows[{workflow_id!r}] checksum mismatch for {path}: expected "
            f"{expected}, got {actual} ({origin})"
        )


def _load_and_check_workflow_json(
    path: Path, *, workflow_id: str, origin: str
) -> dict[str, Any]:
    """Load a workflow JSON and reject embedded credential material.

    n8n stores credential *secrets* separately; a node references a credential by
    a ``{id, name}`` mapping only. Anything else under a node's ``credentials`` —
    a string/list value (a raw secret), or a mapping with keys beyond id/name (a
    ``data`` payload) — means secret material was exported into the workflow,
    which must never live in a seeded file or a generated log. Malformed JSON is
    rejected here so a bad file surfaces at load, not at container runtime.

    Note: ``staticData`` (runtime cursor/token state) and ``pinData`` (pinned
    execution payloads, which routinely carry response bodies with tokens) are
    the other secret carriers in an n8n export; they are *stripped* during
    normalization (see ``compile_n8n_normalized_workflow``) rather than rejected,
    since a fresh seed template has no business shipping either.
    """
    import json

    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConsumerManifestError(
            f"n8n_workflows[{workflow_id!r}] {path} is not valid JSON: {exc} ({origin})"
        ) from exc
    if not isinstance(doc, dict):
        raise ConsumerManifestError(
            f"n8n_workflows[{workflow_id!r}] {path} must be a JSON object ({origin})"
        )
    for node in doc.get("nodes", []) or []:
        if not isinstance(node, Mapping):
            continue
        creds = node.get("credentials")
        if not isinstance(creds, Mapping):
            continue
        for cred_type, cred_ref in creds.items():
            # A credential reference MUST be a mapping of exactly {id, name}. A
            # non-mapping value (string/list/number) is a raw inline secret; a
            # mapping with extra keys is an embedded credential ``data`` payload.
            if not isinstance(cred_ref, Mapping):
                raise ConsumerManifestError(
                    f"n8n_workflows[{workflow_id!r}] credential {cred_type!r} must be a "
                    f"{{id, name}} reference, not an inline value; never embed secrets "
                    f"({origin})"
                )
            extra = {str(k) for k in cred_ref.keys()} - {"id", "name"}
            if extra:
                raise ConsumerManifestError(
                    f"n8n_workflows[{workflow_id!r}] embeds credential material "
                    f"({cred_type}: {sorted(extra)}); reference credentials by "
                    f"id/name only, never inline secrets ({origin})"
                )
    return doc


def _validate_n8n_collisions(workflows: Iterable[N8nWorkflow]) -> None:
    """Reject id collisions across consumers and duplicate webhook routes."""
    owner: dict[str, str] = {}
    route_owner: dict[str, str] = {}
    for wf in workflows:
        if wf.id in owner and owner[wf.id] != wf.consumer:
            raise ConsumerManifestError(
                f"n8n_workflows id {wf.id!r} declared by multiple consumers "
                f"({owner[wf.id]} and {wf.consumer})"
            )
        if wf.id in owner:
            raise ConsumerManifestError(
                f"duplicate n8n_workflows id {wf.id!r} for consumer {wf.consumer!r}"
            )
        owner[wf.id] = wf.consumer
        for probe in wf.webhooks:
            key = f"{probe.method} {probe.path}"
            if key in route_owner:
                raise ConsumerManifestError(
                    f"n8n_workflows webhook route {key!r} declared by two workflows "
                    f"({route_owner[key]} and {wf.id})"
                )
            route_owner[key] = wf.id


def compile_n8n_normalized_workflow(workflow: N8nWorkflow) -> str:
    """Render the normalized workflow JSON the seed imports.

    The top-level ``id`` is set to the Atlas-reserved namespaced ``seed_id`` (the
    idempotency key that can't collide with a user/stack workflow), the
    activation policy is baked in when it is not ``fromJson`` (so
    ``n8n import:workflow`` upserts with the intended active state), and
    runtime/pinned state fields that could carry secrets are stripped.
    """
    import json

    doc = _load_and_check_workflow_json(
        workflow.source_path, workflow_id=workflow.id, origin=str(workflow.source_path)
    )
    doc["id"] = workflow.seed_id
    if workflow.active == "true":
        doc["active"] = True
    elif workflow.active == "false":
        doc["active"] = False
    # ``fromJson`` leaves the file's own ``active`` field untouched.
    for stripped in _N8N_STRIPPED_FIELDS:
        doc.pop(stripped, None)
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def compile_n8n_plan(workflows: Iterable[N8nWorkflow]) -> str:
    """Render the seed plan the n8n-seed container reads (deterministic)."""
    import json

    plan = {
        "version": 1,
        # The reserved id namespace the seed reconciles: any workflow in n8n
        # under this prefix that is NOT in ``workflows`` below is an orphan from a
        # since-removed manifest entry and is deactivated + deleted by the seed.
        "namespace": N8N_SEED_ID_NAMESPACE,
        "workflows": [
            {
                "id": wf.id,               # declared id (manifest identity, logs)
                "seed_id": wf.seed_id,     # namespaced DB id (import/activate key)
                "consumer": wf.consumer,
                "file": wf.container_path,
                "active": wf.active,
                "webhooks": [
                    {
                        "path": p.path,
                        "method": p.method,
                        "expect_status": p.expect_status,
                        "probe": p.probe,
                    }
                    for p in wf.webhooks
                ],
            }
            for wf in workflows
        ],
    }
    return json.dumps(plan, indent=2, sort_keys=True) + "\n"


def compile_n8n_artifacts(
    workflows: Iterable[N8nWorkflow], root: Path
) -> list[GeneratedArtifact]:
    """Build every generated seed file: one normalized workflow JSON per row plus
    the plan.json the seed container reads."""
    workflows = list(workflows)
    artifacts: list[GeneratedArtifact] = []
    for wf in workflows:
        artifacts.append(
            GeneratedArtifact(
                path=root / N8N_CONSUMER_WORKFLOWS_DIR / f"{wf.id}.json",
                content=compile_n8n_normalized_workflow(wf),
            )
        )
    artifacts.append(
        GeneratedArtifact(
            path=root / N8N_CONSUMER_PLAN_PATH,
            content=compile_n8n_plan(workflows),
        )
    )
    return artifacts


def render_n8n_seed_overlay(workflows: Iterable[N8nWorkflow]) -> str:
    """Render the compose overlay that runs the Atlas-owned ``n8n-seed`` container.

    The container uses the n8n image (so it has the ``n8n`` CLI + the n8n schema)
    and imports the normalized workflows AFTER n8n is healthy. Bind-mount paths
    are repo-root-relative because the overlay is merged via ``-f`` (project
    directory = repo root), NOT the native ``include:`` directive.
    """
    workflows = list(workflows)
    lines = [
        "# AUTO-GENERATED by Atlas consumer n8n workflow contract (#412) — do not edit.",
        "# Seeds consumer-declared workflows into n8n after it is healthy, keyed by",
        "# a stable id (idempotent). Regenerated every start; a removed manifest",
        "# drops only its own workflows.",
        "services:",
        "  n8n-seed:",
        "    image: ${N8N_IMAGE}",
        "    container_name: ${PROJECT_NAME}-n8n-seed",
        '    restart: "no"',
        "    depends_on:",
        "      n8n:",
        "        condition: service_healthy",
        "    environment:",
        "      DB_TYPE: postgresdb",
        "      DB_POSTGRESDB_HOST: ${SUPAVISOR_DB_HOST:-supabase-db}",
        "      DB_POSTGRESDB_PORT: ${SUPAVISOR_DB_PORT_VALUE:-5432}",
        "      DB_POSTGRESDB_DATABASE: ${SUPABASE_DB_NAME}",
        "      DB_POSTGRESDB_USER: ${SUPAVISOR_DB_USER:-${SUPABASE_DB_USER}}",
        "      DB_POSTGRESDB_PASSWORD: ${SUPABASE_DB_PASSWORD}",
        "      DB_POSTGRESDB_SCHEMA: n8n",
        "      DB_SCHEMA: n8n",
        "      N8N_ENCRYPTION_KEY: ${N8N_ENCRYPTION_KEY}",
        "      # Public-API key (optional) — when set, the seed activates workflows",
        "      # via the API so production webhooks register on the RUNNING n8n",
        "      # without a restart. Empty → import-only; webhook registers next boot.",
        "      N8N_API_KEY: ${N8N_API_KEY:-}",
        "      # In-network base URL the seed uses for /healthz + webhook probes.",
        "      N8N_SEED_BASE_URL: http://n8n:5678",
        "      N8N_SEED_PLAN: /consumer-workflows/plan.json",
        '    entrypoint: ["/bin/sh", "/scripts/seed-workflows.sh"]',
        "    volumes:",
        "      - ./services/n8n/init/scripts:/scripts:ro",
        "      - ./volumes/n8n/consumer-workflows:/consumer-workflows:ro",
        "    networks:",
        "      - backend-network",
    ]
    return "\n".join(lines) + "\n"


def load_consumer_config(
    root_dir: Path | str,
    *,
    explicit_paths: Iterable[str] | None = None,
) -> ConsumerConfig:
    """Load and validate all configured consumer manifests."""
    root = Path(root_dir)
    manifest_paths = discover_consumer_manifest_paths(root, explicit_paths=explicit_paths)
    if not manifest_paths:
        return ConsumerConfig()

    env_overrides: dict[str, str] = {}
    env_origins: dict[str, str] = {}
    consumers: list[ConsumerRecord] = []
    compose_overlays: list[Path] = []
    backend_plugins: list[Path] = []
    comfyui_sidecars: list[Path] = []
    ollama_models: list[str] = []
    all_storage: list[StorageStore] = []
    all_litellm: list[LitellmModel] = []
    all_n8n: list[N8nWorkflow] = []

    for manifest_path in manifest_paths:
        data = _load_manifest(manifest_path)
        base_dir = manifest_path.parent
        consumer_name = str(data.get("name") or manifest_path.parent.name)
        origin = str(manifest_path)

        if project_name := data.get("project_name"):
            _set_scalar(env_overrides, env_origins, "PROJECT_NAME", project_name, origin)

        brand = data.get("brand") or {}
        if brand:
            if not isinstance(brand, Mapping):
                raise ConsumerManifestError(f"brand must be a mapping in {manifest_path}")
            for key, env_key in _BRAND_ENV_MAP.items():
                if key in brand and brand[key] is not None:
                    value = brand[key]
                    if key.endswith("_file"):
                        value_path = Path(str(value)).expanduser()
                        if not value_path.is_absolute():
                            value = str((base_dir / value_path).resolve())
                    _set_scalar(env_overrides, env_origins, env_key, value, origin)

        env_block = data.get("env") or {}
        if env_block:
            if not isinstance(env_block, Mapping):
                raise ConsumerManifestError(f"env must be a mapping in {manifest_path}")
            for raw_file in _as_list(env_block.get("file")):
                env_path = _resolve_existing_file(base_dir, str(raw_file), label="env.file")
                for key, value in _read_env_overlay(env_path).items():
                    _set_scalar(env_overrides, env_origins, key, value, str(env_path))
            values = env_block.get("values") or {}
            if values:
                if not isinstance(values, Mapping):
                    raise ConsumerManifestError(f"env.values must be a mapping in {manifest_path}")
                for key, value in values.items():
                    _set_scalar(env_overrides, env_origins, str(key), value, origin)

        record_overlays: list[Path] = []
        for raw_overlay in _as_list(data.get("compose_overlays")):
            overlay = _resolve_existing_file(
                base_dir, str(raw_overlay), label="compose_overlays entry"
            )
            if overlay not in compose_overlays:
                compose_overlays.append(overlay)
            record_overlays.append(overlay)

        record_plugins: list[Path] = []
        for raw_plugins in _as_list(data.get("backend_plugins")):
            plugin_dir = _resolve_existing_dir(
                base_dir, str(raw_plugins), label="backend_plugins entry"
            )
            if plugin_dir not in backend_plugins:
                backend_plugins.append(plugin_dir)
            record_plugins.append(plugin_dir)

        model_sidecars = data.get("model_sidecars") or {}
        record_comfyui: list[Path] = []
        record_ollama: list[str] = []
        if model_sidecars:
            if not isinstance(model_sidecars, Mapping):
                raise ConsumerManifestError(f"model_sidecars must be a mapping in {manifest_path}")
            for raw_sidecar in _as_list(model_sidecars.get("comfyui")):
                sidecar = _resolve_existing_file(
                    base_dir, str(raw_sidecar), label="model_sidecars.comfyui entry"
                )
                if sidecar not in comfyui_sidecars:
                    comfyui_sidecars.append(sidecar)
                record_comfyui.append(sidecar)
            for raw_model in _as_list(model_sidecars.get("ollama")):
                model = str(raw_model).strip()
                if model and model not in ollama_models:
                    ollama_models.append(model)
                if model:
                    record_ollama.append(model)

        record_storage = _parse_storage_block(data, consumer_name, manifest_path)
        all_storage.extend(record_storage)

        record_litellm = _parse_litellm_models_block(data, consumer_name, manifest_path)
        all_litellm.extend(record_litellm)

        record_n8n = _parse_n8n_workflows_block(
            data, consumer_name, base_dir, manifest_path
        )
        all_n8n.extend(record_n8n)

        consumers.append(
            ConsumerRecord(
                name=consumer_name,
                manifest_path=manifest_path,
                compose_overlays=tuple(record_overlays),
                backend_plugins=tuple(record_plugins),
                comfyui_sidecars=tuple(record_comfyui),
                ollama_models=tuple(_ordered_union(record_ollama)),
                storage=tuple(record_storage),
                litellm_models=tuple(record_litellm),
                n8n_workflows=tuple(record_n8n),
            )
        )

    if backend_plugins:
        env_overrides["BACKEND_PLUGINS_DIR"] = os.pathsep.join(str(path) for path in backend_plugins)
    if comfyui_sidecars:
        env_overrides["COMFYUI_CUSTOM_MODELS_FILE"] = os.pathsep.join(
            str(path) for path in comfyui_sidecars
        )
    if ollama_models:
        env_overrides["OLLAMA_CUSTOM_MODELS"] = ",".join(_ordered_union(ollama_models))

    storage_overlay: StorageOverlay | None = None
    if all_storage:
        _validate_storage_collisions(all_storage)
        # Provisioning (bucket names + MINIO_EXTRA_CONSUMERS entries) lives ONLY
        # in the generated overlay — never persisted to .env — so removing a
        # store leaves no dangling entry to crash minio-init on a warm restart.
        # The overlay merges its entries onto any operator/_user
        # MINIO_EXTRA_CONSUMERS via ${MINIO_EXTRA_CONSUMERS:-}. Only scoped
        # credentials persist in .env (generated blank-only).
        storage_overlay = StorageOverlay(
            path=root / MINIO_STORAGE_OVERLAY_PATH,
            content=render_minio_storage_overlay(all_storage),
        )

    litellm_models_file: LitellmArtifact | None = None
    litellm_overlay: LitellmArtifact | None = None
    if all_litellm:
        # Aliases are globally unique across consumers (one generated config).
        _validate_litellm_collisions(all_litellm)
        litellm_models_file = LitellmArtifact(
            path=root / LITELLM_CONSUMER_MODELS_PATH,
            content=render_litellm_models_file(all_litellm),
        )
        overlay_content = render_litellm_models_overlay(all_litellm)
        if overlay_content:
            litellm_overlay = LitellmArtifact(
                path=root / LITELLM_CONSUMER_OVERLAY_PATH,
                content=overlay_content,
            )

    n8n_artifacts: tuple[GeneratedArtifact, ...] = ()
    n8n_overlay: GeneratedArtifact | None = None
    if all_n8n:
        # Workflow ids are globally unique + webhook routes non-colliding.
        _validate_n8n_collisions(all_n8n)
        n8n_artifacts = tuple(compile_n8n_artifacts(all_n8n, root))
        n8n_overlay = GeneratedArtifact(
            path=root / N8N_CONSUMER_OVERLAY_PATH,
            content=render_n8n_seed_overlay(all_n8n),
        )

    return ConsumerConfig(
        consumers=tuple(consumers),
        env_overrides=env_overrides,
        compose_overlays=compose_overlays,
        storage=tuple(all_storage),
        storage_overlay=storage_overlay,
        litellm_models=tuple(all_litellm),
        litellm_models_file=litellm_models_file,
        litellm_overlay=litellm_overlay,
        n8n_workflows=tuple(all_n8n),
        n8n_artifacts=n8n_artifacts,
        n8n_overlay=n8n_overlay,
    )
