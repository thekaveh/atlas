"""
Source-permutation matrix.

For every SOURCE-configurable service, iterate every valid SOURCE value, write
it into a throwaway .env, and assert `docker compose config -q` succeeds. This
proves that:

  - Every declared source variant produces a parseable compose shape.
  - The `${VAR:-default}` patterns and `replicas: ${X_SCALE:-N}` fallbacks
    behave correctly when AUTO-MANAGED env vars are at their defaults.
  - No fragment hard-codes a source-specific value that breaks parse-time.

Skipped if `docker` is not on PATH or `.env.example` is missing.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


pytestmark = pytest.mark.skipif(
    not _docker_available() or not ENV_EXAMPLE.is_file(),
    reason="docker not on PATH or .env.example missing",
)


def _manifest_permutations() -> list[tuple[str, list[str]]]:
    """Derive the matrix from the manifest contract so new sources cannot be omitted."""
    from services.manifests import load_manifests

    return [
        (manifest.sources.var, [option.id for option in manifest.sources.options])
        for manifest in load_manifests(REPO_ROOT / "services")
        if manifest.sources is not None
    ]


# Cloud providers predate the single-``sources`` block: one virtual manifest
# deliberately owns three independent toggles. Keep only this irreducible
# multi-source exception explicit; every normal source comes from its manifest.
_CLOUD_PROVIDER_PERMUTATIONS = [
    ("CLOUD_OPENAI_SOURCE", ["enabled", "disabled"]),
    ("CLOUD_ANTHROPIC_SOURCE", ["enabled", "disabled"]),
    ("CLOUD_OPENROUTER_SOURCE", ["enabled", "disabled"]),
]

_PERMUTATIONS = _manifest_permutations() + _CLOUD_PROVIDER_PERMUTATIONS


def _write_env_with_override(target: Path, var: str, value: str) -> None:
    """Copy .env.example to `target` with one variable overridden."""
    lines = ENV_EXAMPLE.read_text().splitlines()
    out = []
    found = False
    for line in lines:
        if line.startswith(f"{var}=") and not found:
            out.append(f"{var}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{var}={value}")
    target.write_text("\n".join(out) + "\n")


def _compose_config_ok(env_file: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(COMPOSE),
            "config",
            "-q",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "var,value",
    [(v, val) for v, vals in _PERMUTATIONS for val in vals],
    ids=[f"{v}={val}" for v, vals in _PERMUTATIONS for val in vals],
)
def test_source_value_produces_valid_compose(var: str, value: str, tmp_path: Path):
    """Every source value, when written to .env, must produce a parseable
    compose shape via `docker compose -f docker-compose.yml config -q`."""
    env_file = tmp_path / ".env"
    _write_env_with_override(env_file, var, value)
    ok, stderr = _compose_config_ok(env_file)
    assert ok, f"{var}={value} produced invalid compose:\n{stderr}"


def test_source_permutation_matrix_matches_every_manifest_option():
    from services.manifests import load_manifests

    declared = {
        (manifest.sources.var, option.id)
        for manifest in load_manifests(REPO_ROOT / "services")
        if manifest.sources is not None
        for option in manifest.sources.options
    }
    exercised = {
        (source_var, value)
        for source_var, values in _PERMUTATIONS
        for value in values
    }
    cloud = {
        (source_var, value)
        for source_var, values in _CLOUD_PROVIDER_PERMUTATIONS
        for value in values
    }
    assert exercised == declared | cloud


def test_explicit_remote_host_bind_remains_a_valid_compose_override(tmp_path: Path):
    env_file = tmp_path / ".env"
    _write_env_with_override(env_file, "HOST_BIND_IP", "0.0.0.0:")

    ok, stderr = _compose_config_ok(env_file)
    assert ok, f"HOST_BIND_IP=0.0.0.0: produced invalid compose:\n{stderr}"


# ─── Scaled-family rendering tests ──────────────────────────────────────
# These exercise multi-replica scales that ``test_source_value_produces_valid_compose``
# can't catch — that test only flips SOURCE vars; the auto-managed *_SCALE
# vars stay empty (= 0 at render time). When a user actually enables a
# scaled service (``--spark-source container --spark-workers 2``), the
# bootstrapper sets the *_SCALE values in .env, and Compose then evaluates
# rules like "no container_name when replicas > 1".

@pytest.mark.parametrize("worker_count", ["1", "2", "8"])
def test_spark_renders_at_every_supported_worker_count(worker_count: str, tmp_path: Path):
    """Spark workers can scale 1-8 via SPARK_WORKER_COUNT. At every
    supported count the merged compose shape must render — in particular,
    ``container_name`` MUST NOT be set on spark-worker because Compose
    forbids it when ``deploy.replicas > 1`` (Docker can't make multiple
    containers share the same name). Ray's ray-worker has the same
    constraint and is the precedent for the no-container_name pattern."""
    env_file = tmp_path / ".env"
    # Apply all five scales the bootstrapper would write when source=container.
    # (Pass 2 added the dedicated spark-connect sidecar — SPARK_CONNECT_SCALE
    # must be 1 alongside the rest for the merged compose to render the
    # full topology that ships at runtime.)
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    out_lines = []
    for line in env_text.splitlines():
        if line.startswith("SPARK_SOURCE="):
            out_lines.append("SPARK_SOURCE=container")
        elif line.startswith("SPARK_MASTER_SCALE="):
            out_lines.append("SPARK_MASTER_SCALE=1")
        elif line.startswith("SPARK_WORKER_SCALE="):
            out_lines.append(f"SPARK_WORKER_SCALE={worker_count}")
        elif line.startswith("SPARK_HISTORY_SCALE="):
            out_lines.append("SPARK_HISTORY_SCALE=1")
        elif line.startswith("SPARK_INIT_SCALE="):
            out_lines.append("SPARK_INIT_SCALE=1")
        elif line.startswith("SPARK_CONNECT_SCALE="):
            out_lines.append("SPARK_CONNECT_SCALE=1")
        else:
            out_lines.append(line)
    env_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    ok, stderr = _compose_config_ok(env_file)
    assert ok, f"SPARK_WORKER_SCALE={worker_count} produced invalid compose:\n{stderr}"
# Endpoint vars a manifest blanks for `disabled` must not be re-defaulted by a
# consumer. `${VAR:-x}` substitutes on EMPTY, not merely unset, so a non-empty
# default there overwrites the very signal that says "provider is off".
# Each entry below is a known instance awaiting its consumer being checked; the
# fix is only safe where that consumer branches on the empty value. This list
# may shrink, never grow.
_KNOWN_ENDPOINT_DEFAULTS = {
    "services/asset-baker/compose.yml: ASSET_BAKER_MINIO_ENDPOINT",
    "services/asset-worker/compose.yml: ASSET_WORKER_MINIO_ENDPOINT",
    "services/backend/compose.yml: NEO4J_URI",
    "services/grafana/compose.yml: PROMETHEUS_ENDPOINT",
    "services/grafana/compose.yml: TEMPO_ENDPOINT",
    "services/grafana/compose.yml: LOKI_ENDPOINT",
    "services/jupyterhub/compose.yml: NEO4J_URI",
    "services/mcp-servers/compose.yml: NEO4J_URI",
}


def _env_vars_blanked_when_disabled() -> set[str]:
    """Env vars a manifest explicitly sets to "" in its `disabled` slice."""
    import yaml

    blanked: set[str] = set()
    for manifest_path in sorted((REPO_ROOT / "services").glob("*/service.yml")):
        doc = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        for family in (doc.get("runtime_sc") or {}).values():
            environment = ((family or {}).get("disabled") or {}).get("environment") or {}
            blanked.update(key for key, value in environment.items() if value == "")
    return blanked


def _has_non_empty_default(line: str, var: str) -> bool:
    marker = "${" + var + ":-"
    index = line.find(marker)
    # `${VAR:-}` is the established "empty when unset" idiom and preserves blank.
    return index != -1 and not line[index + len(marker):].startswith("}")


def _endpoint_defaults_that_override_a_blank(blanked: set[str]) -> set[str]:
    found: set[str] = set()
    for fragment in sorted((REPO_ROOT / "services").glob("*/compose.yml")):
        relative = fragment.relative_to(REPO_ROOT)
        for line in fragment.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            found.update(
                f"{relative}: {var}"
                for var in blanked
                if _has_non_empty_default(stripped, var)
            )
    return found


def test_no_consumer_default_resurrects_an_endpoint_its_manifest_blanks():
    """An empty auto-managed endpoint is how a consumer learns a provider is off.

    weaviate's manifest sets `WEAVIATE_URL: ""` for `disabled` on purpose and the
    backend branches on the blank; a `${VAR:-url}` default in the consumer handed
    it an endpoint for a container that was not running, so a disabled provider
    failed like a broken one.
    """
    blanked = _env_vars_blanked_when_disabled()
    assert blanked, "no manifest blanks an endpoint on `disabled` -- re-derive this contract"

    offenders = _endpoint_defaults_that_override_a_blank(blanked)

    assert not (offenders - _KNOWN_ENDPOINT_DEFAULTS), (
        "a consumer fragment defaults an endpoint its owning manifest blanks for "
        "`disabled`, so the not-configured signal never reaches it:\n  "
        + "\n  ".join(sorted(offenders - _KNOWN_ENDPOINT_DEFAULTS))
    )
    assert not (_KNOWN_ENDPOINT_DEFAULTS - offenders), (
        "these were fixed -- drop them from _KNOWN_ENDPOINT_DEFAULTS so the list "
        f"keeps ratcheting down: {sorted(_KNOWN_ENDPOINT_DEFAULTS - offenders)}"
    )
