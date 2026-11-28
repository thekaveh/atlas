"""Storage rename map: old MINIO_* Atlas names -> SILO_* (one-release alias window).

Atlas retired the MinIO naming when its storage container moved to Silo
(pgsty/silo, a fork of MinIO). Every Atlas-owned storage name is now SILO_*.
This module is the only place the mapping lives. Two readers use it:

* ``core/config_parser.py`` ``parse_env_file`` reads the old MINIO_* value when
  the SILO_* key is absent, and prints one deprecation warning per process.
* ``services/migrations/migration_v6.py`` rewrites the names in a .env file.

Silo server keys are not Atlas names. The pgsty/silo container still reads its
own MINIO_* environment names, which the silo compose fragment sets on the
server container. Those keys are never read from or rewritten in a .env file.
Three server names are also old Atlas .env names (MINIO_ROOT_USER,
MINIO_ROOT_PASSWORD, MINIO_REGION). Their .env name is mapped here; the compose
container-side key keeps its MINIO_* spelling.

Delete the fallback and migration_v6 in the release after 1.2.
"""

from __future__ import annotations

import sys

OLD_PREFIX = "MINIO_"
NEW_PREFIX = "SILO_"

# Silo server keys: container-side names that are never Atlas .env names. The
# alias fallback and migration_v6 leave them alone.
SILO_SERVER_KEYS = frozenset({"MINIO_BROWSER_REDIRECT_URL", "MINIO_PROMETHEUS_AUTH_TYPE"})
SILO_SERVER_KEY_PREFIXES = ("MINIO_IDENTITY_OPENID_",)

# Static Atlas names: the shipped .env.example keys (plus the ASSET_*_MINIO_*
# names). Consumer-declared keys (MINIO_BUCKET_<KEY>, MINIO_<KEY>_ACCESS_KEY)
# are not listed here; the MINIO_ prefix rule in silo_name_for covers them.
STATIC_RENAMES: dict[str, str] = {
    "ASSET_BAKER_MINIO_ACCESS_KEY": "ASSET_BAKER_SILO_ACCESS_KEY",
    "ASSET_BAKER_MINIO_BUCKET": "ASSET_BAKER_SILO_BUCKET",
    "ASSET_BAKER_MINIO_ENABLED": "ASSET_BAKER_SILO_ENABLED",
    "ASSET_BAKER_MINIO_ENDPOINT": "ASSET_BAKER_SILO_ENDPOINT",
    "ASSET_BAKER_MINIO_SECRET_KEY": "ASSET_BAKER_SILO_SECRET_KEY",
    "ASSET_WORKER_MINIO_ACCESS_KEY": "ASSET_WORKER_SILO_ACCESS_KEY",
    "ASSET_WORKER_MINIO_BUCKET": "ASSET_WORKER_SILO_BUCKET",
    "ASSET_WORKER_MINIO_ENABLED": "ASSET_WORKER_SILO_ENABLED",
    "ASSET_WORKER_MINIO_ENDPOINT": "ASSET_WORKER_SILO_ENDPOINT",
    "ASSET_WORKER_MINIO_SECRET_KEY": "ASSET_WORKER_SILO_SECRET_KEY",
    "MINIO_ASSET_BAKER_ACCESS_KEY": "SILO_ASSET_BAKER_ACCESS_KEY",
    "MINIO_ASSET_BAKER_SECRET_KEY": "SILO_ASSET_BAKER_SECRET_KEY",
    "MINIO_ASSET_INGEST_ACCESS_KEY": "SILO_ASSET_INGEST_ACCESS_KEY",
    "MINIO_ASSET_INGEST_SECRET_KEY": "SILO_ASSET_INGEST_SECRET_KEY",
    "MINIO_ASSET_WORKER_ACCESS_KEY": "SILO_ASSET_WORKER_ACCESS_KEY",
    "MINIO_ASSET_WORKER_SECRET_KEY": "SILO_ASSET_WORKER_SECRET_KEY",
    "MINIO_BACKEND_ACCESS_KEY": "SILO_BACKEND_ACCESS_KEY",
    "MINIO_BACKEND_SECRET_KEY": "SILO_BACKEND_SECRET_KEY",
    "MINIO_BUCKET_ASSET_INPUTS": "SILO_BUCKET_ASSET_INPUTS",
    "MINIO_BUCKET_BACKEND": "SILO_BUCKET_BACKEND",
    "MINIO_BUCKET_COMFYUI": "SILO_BUCKET_COMFYUI",
    "MINIO_BUCKET_DOCLING": "SILO_BUCKET_DOCLING",
    "MINIO_BUCKET_ICEBERG_CHECKPOINTS": "SILO_BUCKET_ICEBERG_CHECKPOINTS",
    "MINIO_BUCKET_ICEBERG_JARS": "SILO_BUCKET_ICEBERG_JARS",
    "MINIO_BUCKET_ICEBERG_LAKEHOUSE": "SILO_BUCKET_ICEBERG_LAKEHOUSE",
    "MINIO_BUCKET_ICEBERG_LANDING": "SILO_BUCKET_ICEBERG_LANDING",
    "MINIO_BUCKET_JUPYTER": "SILO_BUCKET_JUPYTER",
    "MINIO_BUCKET_LABEL_STUDIO": "SILO_BUCKET_LABEL_STUDIO",
    "MINIO_BUCKET_LANGFUSE": "SILO_BUCKET_LANGFUSE",
    "MINIO_BUCKET_MLFLOW": "SILO_BUCKET_MLFLOW",
    "MINIO_BUCKET_N8N": "SILO_BUCKET_N8N",
    "MINIO_BUCKET_SPARK_HISTORY": "SILO_BUCKET_SPARK_HISTORY",
    "MINIO_COMFYUI_ACCESS_KEY": "SILO_COMFYUI_ACCESS_KEY",
    "MINIO_COMFYUI_SECRET_KEY": "SILO_COMFYUI_SECRET_KEY",
    "MINIO_CONSOLE_PORT": "SILO_CONSOLE_PORT",
    "MINIO_DOCLING_ACCESS_KEY": "SILO_DOCLING_ACCESS_KEY",
    "MINIO_DOCLING_SECRET_KEY": "SILO_DOCLING_SECRET_KEY",
    "MINIO_ENDPOINT": "SILO_ENDPOINT",
    "MINIO_EXTRA_CONSUMERS": "SILO_EXTRA_CONSUMERS",
    "MINIO_ICEBERG_ACCESS_KEY": "SILO_ICEBERG_ACCESS_KEY",
    "MINIO_ICEBERG_SECRET_KEY": "SILO_ICEBERG_SECRET_KEY",
    "MINIO_IMAGE": "SILO_IMAGE",
    "MINIO_INIT_IMAGE": "SILO_INIT_IMAGE",
    "MINIO_INIT_SCALE": "SILO_INIT_SCALE",
    "MINIO_INIT_SOURCE": "SILO_INIT_SOURCE",
    "MINIO_JENKINS_ACCESS_KEY": "SILO_JENKINS_ACCESS_KEY",
    "MINIO_JENKINS_SECRET_KEY": "SILO_JENKINS_SECRET_KEY",
    "MINIO_JUPYTER_ACCESS_KEY": "SILO_JUPYTER_ACCESS_KEY",
    "MINIO_JUPYTER_SECRET_KEY": "SILO_JUPYTER_SECRET_KEY",
    "MINIO_LABEL_STUDIO_ACCESS_KEY": "SILO_LABEL_STUDIO_ACCESS_KEY",
    "MINIO_LABEL_STUDIO_SECRET_KEY": "SILO_LABEL_STUDIO_SECRET_KEY",
    "MINIO_LANGFUSE_ACCESS_KEY": "SILO_LANGFUSE_ACCESS_KEY",
    "MINIO_LANGFUSE_SECRET_KEY": "SILO_LANGFUSE_SECRET_KEY",
    "MINIO_MLFLOW_ACCESS_KEY": "SILO_MLFLOW_ACCESS_KEY",
    "MINIO_MLFLOW_SECRET_KEY": "SILO_MLFLOW_SECRET_KEY",
    "MINIO_N8N_ACCESS_KEY": "SILO_N8N_ACCESS_KEY",
    "MINIO_N8N_SECRET_KEY": "SILO_N8N_SECRET_KEY",
    "MINIO_PORT": "SILO_PORT",
    "MINIO_PUBLIC_CONSOLE_ENDPOINT": "SILO_PUBLIC_CONSOLE_ENDPOINT",
    "MINIO_PUBLIC_ENDPOINT": "SILO_PUBLIC_ENDPOINT",
    "MINIO_REGION": "SILO_REGION",
    "MINIO_ROOT_PASSWORD": "SILO_ROOT_PASSWORD",
    "MINIO_ROOT_USER": "SILO_ROOT_USER",
    "MINIO_SCALE": "SILO_SCALE",
    "MINIO_SOURCE": "SILO_SOURCE",
    "MINIO_SPARK_ACCESS_KEY": "SILO_SPARK_ACCESS_KEY",
    "MINIO_SPARK_SECRET_KEY": "SILO_SPARK_SECRET_KEY",
}

_WARNED = False


def silo_name_for(old: str) -> str | None:
    """Return the SILO_* name that replaces an old Atlas name, or None.

    None means the name is not an Atlas storage variable: a Silo server key, or
    a name outside the MINIO_ family.
    """
    if old in SILO_SERVER_KEYS or old.startswith(SILO_SERVER_KEY_PREFIXES):
        return None
    if old in STATIC_RENAMES:
        return STATIC_RENAMES[old]
    if old.startswith(OLD_PREFIX) and len(old) > len(OLD_PREFIX):
        return NEW_PREFIX + old[len(OLD_PREFIX):]
    return None


def apply_alias_fallback(values: dict[str, str]) -> dict[str, str]:
    """Return ``values`` with each absent SILO_* key filled from its MINIO_* key.

    The old value is used in memory only. The file is not changed; migration_v6
    rewrites it. Prints one deprecation warning per process, naming the first
    aliased pair and the number of keys read through the alias.
    """
    global _WARNED
    resolved = dict(values)
    aliased: list[tuple[str, str]] = []
    for old, value in values.items():
        new = silo_name_for(old)
        if new is None or new in values:
            continue
        resolved[new] = value
        aliased.append((old, new))
    if aliased and not _WARNED:
        _WARNED = True
        old, new = aliased[0]
        print(
            f"WARNING: {old} is deprecated; use {new} instead (the storage "
            f"variables moved from MINIO_* to SILO_*). {len(aliased)} key(s) are "
            "read from their MINIO_* names for this run. Start ./start.sh once to "
            "migrate .env (migration v6).",
            file=sys.stderr,
        )
    return resolved
