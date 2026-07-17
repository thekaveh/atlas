"""Tests for the consumer LightRAG query profile registry (#414).

Covers the canonical acceptance matrix: valid profile loading (single +
multi-consumer namespacing), the deterministic generated registry + backend
overlay, byte stability, optional-bound inheritance, no-profile byte/behavior
compatibility, invalid-mode/-bound/-rerank rejection, collisions, owner-spoof
rejection, and the optional #411 LiteLLM alias generation.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from core.consumer_manifest import (
    ConsumerManifestError,
    compile_lightrag_query_profiles_file,
    load_consumer_config,
    render_lightrag_query_profiles_overlay,
)


def _write_root(root: Path) -> None:
    (root / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")


def _write_consumer(root: Path, name: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(f"name: {name}\n" + textwrap.dedent(body), encoding="utf-8")
    return manifest


def _load(root: Path, manifest: Path):
    return load_consumer_config(root, explicit_paths=[str(manifest)])


_FULL = """
    lightrag_query_profiles:
      version: 1
      profiles:
        - name: graph-hybrid-default
          mode: hybrid
          top_k: 10
          chunk_top_k: 5
          max_total_tokens: 12000
          enable_rerank: false
        - name: graph-local-wide
          mode: local
          top_k: 30
          chunk_top_k: 10
          max_total_tokens: 16000
        - name: graph-global-compact
          mode: global
          top_k: 8
"""


# ── happy path ──────────────────────────────────────────────────────

def test_profiles_parsed(tmp_path: Path) -> None:
    manifest = _write_consumer(tmp_path, "rag-showcase", _FULL)
    _write_root(tmp_path)
    config = _load(tmp_path, manifest)
    assert len(config.lightrag_query_profiles) == 3
    p = config.lightrag_query_profiles[0]
    assert p.name == "graph-hybrid-default"
    assert p.consumer == "rag-showcase"
    assert p.mode == "hybrid"
    assert p.top_k == 10 and p.chunk_top_k == 5 and p.max_total_tokens == 12000
    assert p.enable_rerank is False
    assert len(p.revision) == 16  # stable content hash


def test_registry_and_overlay_generated(tmp_path: Path) -> None:
    manifest = _write_consumer(tmp_path, "rag-showcase", _FULL)
    _write_root(tmp_path)
    config = _load(tmp_path, manifest)

    assert config.lightrag_query_profiles_file is not None
    assert config.lightrag_query_profiles_file.path == (
        tmp_path / "volumes/backend/lightrag-query-profiles.json"
    )
    doc = json.loads(config.lightrag_query_profiles_file.content)
    assert doc["version"] == 1
    # Precedence contract is machine-discoverable in the artifact.
    assert doc["precedence"] == ["request", "profile", "service_env_default"]
    # Declaration order is preserved (deterministic per manifest).
    names = [p["name"] for p in doc["profiles"]]
    assert names == ["graph-hybrid-default", "graph-local-wide", "graph-global-compact"]
    for entry in doc["profiles"]:
        assert entry["consumer"] == "rag-showcase"
        assert len(entry["revision"]) == 16

    assert config.lightrag_query_profiles_overlay is not None
    overlay = config.lightrag_query_profiles_overlay.content
    assert "LIGHTRAG_QUERY_PROFILES_FILE: /atlas-consumer-config/lightrag-query-profiles.json" in overlay
    assert (
        "./volumes/backend/lightrag-query-profiles.json"
        ":/atlas-consumer-config/lightrag-query-profiles.json:ro"
    ) in overlay


def test_optional_bounds_omitted_inherit_env_default(tmp_path: Path) -> None:
    """A profile that omits a numeric bound leaves it OUT of the registry so the
    backend inherits the LIGHTRAG_QUERY_* env default (precedence contract)."""
    manifest = _write_consumer(tmp_path, "rag-showcase", _FULL)
    _write_root(tmp_path)
    config = _load(tmp_path, manifest)
    doc = json.loads(config.lightrag_query_profiles_file.content)
    compact = next(p for p in doc["profiles"] if p["name"] == "graph-global-compact")
    assert compact["mode"] == "global"
    assert compact["top_k"] == 8
    # chunk_top_k + max_total_tokens omitted → inherit env default at runtime.
    assert "chunk_top_k" not in compact
    assert "max_total_tokens" not in compact
    # enable_rerank always present (safe default False).
    assert compact["enable_rerank"] is False


def test_generated_output_is_byte_stable(tmp_path: Path) -> None:
    manifest = _write_consumer(tmp_path, "rag-showcase", _FULL)
    _write_root(tmp_path)
    config = _load(tmp_path, manifest)
    profiles = config.lightrag_query_profiles
    assert compile_lightrag_query_profiles_file(profiles) == (
        config.lightrag_query_profiles_file.content
    )
    # Deterministic across repeated renders (sorted keys, trailing newline).
    a = compile_lightrag_query_profiles_file(profiles)
    b = compile_lightrag_query_profiles_file(profiles)
    assert a == b and a.endswith("\n")
    render_lightrag_query_profiles_overlay(profiles)  # accepts any iterable


def test_optional_model_and_description_recorded(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path,
        "rag-showcase",
        """
        lightrag_query_profiles:
          version: 1
          profiles:
            - name: graph-tuned
              mode: mix
              query_llm_model: gpt-4o
              embedding_model: nomic-embed-text
              description: Tuned mix flavor
        """,
    )
    _write_root(tmp_path)
    config = _load(tmp_path, manifest)
    p = config.lightrag_query_profiles[0]
    assert p.query_llm_model == "gpt-4o"
    assert p.embedding_model == "nomic-embed-text"
    assert p.description == "Tuned mix flavor"
    doc = json.loads(config.lightrag_query_profiles_file.content)
    entry = doc["profiles"][0]
    assert entry["query_llm_model"] == "gpt-4o"
    assert entry["embedding_model"] == "nomic-embed-text"


# ── backward compatibility ──────────────────────────────────────────

def test_no_profiles_yields_no_artifacts(tmp_path: Path) -> None:
    manifest = _write_consumer(tmp_path, "plain", "env:\n  values:\n    X: \"1\"\n")
    _write_root(tmp_path)
    config = _load(tmp_path, manifest)
    assert config.lightrag_query_profiles == ()
    assert config.lightrag_query_profiles_file is None
    assert config.lightrag_query_profiles_overlay is None


def test_profiles_independent_of_lightrag_being_enabled(tmp_path: Path) -> None:
    """Profiles are declarative: the registry compiles regardless of whether
    LightRAG is enabled/disabled in the deployment (disabled-LightRAG case)."""
    manifest = _write_consumer(tmp_path, "rag-showcase", _FULL)
    _write_root(tmp_path)  # no LIGHTRAG_ENDPOINT anywhere
    config = _load(tmp_path, manifest)
    assert config.lightrag_query_profiles_file is not None
    assert len(config.lightrag_query_profiles) == 3


# ── multi-consumer namespacing ──────────────────────────────────────

def test_multi_consumer_namespacing(tmp_path: Path) -> None:
    a = _write_consumer(
        tmp_path,
        "team-a",
        """
        lightrag_query_profiles:
          version: 1
          profiles:
            - {name: a-hybrid, mode: hybrid, top_k: 10}
        """,
    )
    b = _write_consumer(
        tmp_path,
        "team-b",
        """
        lightrag_query_profiles:
          version: 1
          profiles:
            - {name: b-local, mode: local, top_k: 20}
        """,
    )
    _write_root(tmp_path)
    config = load_consumer_config(tmp_path, explicit_paths=[str(a), str(b)])
    doc = json.loads(config.lightrag_query_profiles_file.content)
    owners = {p["name"]: p["consumer"] for p in doc["profiles"]}
    assert owners == {"a-hybrid": "team-a", "b-local": "team-b"}


def test_duplicate_name_within_consumer_rejected(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        """
        lightrag_query_profiles:
          version: 1
          profiles:
            - {name: dup, mode: hybrid, top_k: 10}
            - {name: dup, mode: local, top_k: 20}
        """,
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="duplicate lightrag_query_profiles name 'dup'"):
        _load(tmp_path, manifest)


def test_duplicate_name_across_consumers_rejected(tmp_path: Path) -> None:
    a = _write_consumer(
        tmp_path,
        "team-a",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: shared, mode: hybrid}\n",
    )
    b = _write_consumer(
        tmp_path,
        "team-b",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: shared, mode: local}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="declared by multiple consumers"):
        load_consumer_config(tmp_path, explicit_paths=[str(a), str(b)])


def test_spoofed_owner_rejected(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path,
        "real-owner",
        """
        lightrag_query_profiles:
          version: 1
          profiles:
            - {name: p, mode: hybrid, owner: someone-else}
        """,
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="cannot be\\s+spoofed"):
        _load(tmp_path, manifest)


# ── schema / bound validation ───────────────────────────────────────

@pytest.mark.parametrize("version", ["0", "2", "'1'"])
def test_version_must_be_one(tmp_path: Path, version: str) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        f"lightrag_query_profiles:\n  version: {version}\n  profiles:\n    - {{name: p, mode: hybrid}}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="version must be 1"):
        _load(tmp_path, manifest)


def test_empty_profiles_rejected(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path, "c", "lightrag_query_profiles:\n  version: 1\n  profiles: []\n"
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="must be a non-empty list"):
        _load(tmp_path, manifest)


def test_unknown_profile_field_rejected(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        """
        lightrag_query_profiles:
          version: 1
          profiles:
            - {name: p, mode: hybrid, bogus: 1}
        """,
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="unknown field.*bogus"):
        _load(tmp_path, manifest)


@pytest.mark.parametrize("bad_mode", ["semantic", "vector", "HYBRID", ""])
def test_invalid_mode_rejected(tmp_path: Path, bad_mode: str) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        f"lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {{name: p, mode: {bad_mode!r}}}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="mode .* must be one of"):
        _load(tmp_path, manifest)


@pytest.mark.parametrize("field", ["top_k", "chunk_top_k", "max_total_tokens"])
@pytest.mark.parametrize("value", ["0", "-5"])
def test_non_positive_bounds_rejected(tmp_path: Path, field: str, value: str) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        f"lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {{name: p, mode: hybrid, {field}: {value}}}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match=f"{field} must be a positive integer"):
        _load(tmp_path, manifest)


@pytest.mark.parametrize("field", ["top_k", "chunk_top_k", "max_total_tokens"])
def test_non_integer_bounds_rejected(tmp_path: Path, field: str) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        f"lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {{name: p, mode: hybrid, {field}: 1.5}}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match=f"{field} must be an integer"):
        _load(tmp_path, manifest)


@pytest.mark.parametrize("field", ["top_k", "chunk_top_k", "max_total_tokens"])
def test_boolean_bounds_rejected(tmp_path: Path, field: str) -> None:
    """A YAML bool must NOT count as an int (isinstance(True, int) is truthy)."""
    manifest = _write_consumer(
        tmp_path,
        "c",
        f"lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {{name: p, mode: hybrid, {field}: true}}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match=f"{field} must be an integer"):
        _load(tmp_path, manifest)


def test_over_maximum_bound_rejected(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: p, mode: hybrid, top_k: 99999}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="exceeds the maximum"):
        _load(tmp_path, manifest)


# ── rerank rejection (needs #415 adapter) ───────────────────────────

def test_rerank_enabled_rejected(tmp_path: Path) -> None:
    # Default (adapter disabled): rerank-on profiles are rejected so they cannot
    # be pointed at TEI's incompatible /rerank payload (#414/#415).
    manifest = _write_consumer(
        tmp_path,
        "c",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: p, mode: hybrid, enable_rerank: true}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(
        ConsumerManifestError,
        match="enable_rerank=true requires the LightRAG rerank adapter to be enabled",
    ):
        _load(tmp_path, manifest)


def test_rerank_enabled_allowed_when_adapter_enabled(tmp_path: Path) -> None:
    # #415: with the backend adapter enabled, a rerank-on profile is valid and
    # its enable_rerank flag is carried through to the compiled registry.
    manifest = _write_consumer(
        tmp_path,
        "c",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: p, mode: hybrid, enable_rerank: true}\n",
    )
    _write_root(tmp_path)
    config = load_consumer_config(
        tmp_path,
        explicit_paths=[str(manifest)],
        lightrag_rerank_adapter_enabled=True,
    )
    assert config.lightrag_query_profiles[0].enable_rerank is True
    compiled = json.loads(
        compile_lightrag_query_profiles_file(config.lightrag_query_profiles)
    )
    assert compiled["profiles"][0]["enable_rerank"] is True


def test_rerank_non_bool_rejected(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: p, mode: hybrid, enable_rerank: maybe}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="enable_rerank must be a boolean"):
        _load(tmp_path, manifest)


def test_rerank_false_allowed(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: p, mode: hybrid, enable_rerank: false}\n",
    )
    _write_root(tmp_path)
    config = _load(tmp_path, manifest)
    assert config.lightrag_query_profiles[0].enable_rerank is False


@pytest.mark.parametrize("field", ["query_llm_model", "embedding_model"])
def test_invalid_model_ref_rejected(tmp_path: Path, field: str) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        f"lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {{name: p, mode: hybrid, {field}: 'bad model!'}}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match=f"{field} .* must match"):
        _load(tmp_path, manifest)


# ── optional #411 LiteLLM alias generation ──────────────────────────

def test_litellm_alias_generates_row(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path,
        "rag-showcase",
        """
        lightrag_query_profiles:
          version: 1
          profiles:
            - name: graph-local-wide
              mode: local
              top_k: 30
              chunk_top_k: 10
              max_total_tokens: 16000
              query_llm_model: gpt-4o
              litellm_alias: graph-rag-local-wide
        """,
    )
    _write_root(tmp_path)
    config = _load(tmp_path, manifest)
    rows = {m.name: m for m in config.litellm_models}
    assert "graph-rag-local-wide" in rows
    alias = rows["graph-rag-local-wide"]
    assert alias.consumer == "rag-showcase"
    # Points at the backend's profile-aware OpenAI route (approved in-network base).
    assert alias.api_base == "http://backend:8000"
    assert alias.model == "openai/graph-rag-local-wide"
    row = alias.to_row()
    info = row["model_info"]
    assert info["atlas_lightrag_profile"] == "graph-local-wide"
    assert info["lightrag_mode"] == "local"
    assert info["lightrag_top_k"] == 30
    assert info["lightrag_query_llm_model"] == "gpt-4o"
    # A generated consumer-models.yaml is emitted (mergeable by litellm-init).
    assert config.litellm_models_file is not None


def test_no_alias_generates_no_row(tmp_path: Path) -> None:
    manifest = _write_consumer(tmp_path, "rag-showcase", _FULL)
    _write_root(tmp_path)
    config = _load(tmp_path, manifest)
    # Opt-in: none of the _FULL profiles set litellm_alias → zero rows.
    assert config.litellm_models == ()
    assert config.litellm_models_file is None


def test_alias_collision_with_explicit_litellm_model_rejected(tmp_path: Path) -> None:
    """A profile alias shares the global LiteLLM alias namespace, so it collides
    with an explicit #411 row of the same name (same consumer → duplicate)."""
    manifest = _write_consumer(
        tmp_path,
        "rag-showcase",
        """
        litellm_models:
          version: 1
          models:
            - {name: graph-rag-local, api_base: "${ATLAS_BACKEND_INTERNAL}/v1"}
        lightrag_query_profiles:
          version: 1
          profiles:
            - {name: p, mode: local, litellm_alias: graph-rag-local}
        """,
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="duplicate litellm_models alias 'graph-rag-local'"):
        _load(tmp_path, manifest)


def test_alias_across_consumers_rejected(tmp_path: Path) -> None:
    a = _write_consumer(
        tmp_path,
        "team-a",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: pa, mode: local, litellm_alias: shared-alias}\n",
    )
    b = _write_consumer(
        tmp_path,
        "team-b",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: pb, mode: hybrid, litellm_alias: shared-alias}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="alias 'shared-alias' declared by multiple consumers"):
        load_consumer_config(tmp_path, explicit_paths=[str(a), str(b)])


def test_reserved_alias_rejected(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: p, mode: local, litellm_alias: lightrag}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="reserved for a stack-owned model"):
        _load(tmp_path, manifest)


def test_bad_alias_charset_rejected(tmp_path: Path) -> None:
    manifest = _write_consumer(
        tmp_path,
        "c",
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n    - {name: p, mode: local, litellm_alias: 'Bad Alias'}\n",
    )
    _write_root(tmp_path)
    with pytest.raises(ConsumerManifestError, match="litellm_alias 'Bad Alias' must match"):
        _load(tmp_path, manifest)


# ── #654: consumer env overlay can enable the rerank adapter gate ────────────
_RERANK_ON = (
    "lightrag_query_profiles:\n  version: 1\n  profiles:\n"
    "    - {name: p, mode: hybrid, enable_rerank: true}\n"
)


def _consumer_with_env_file(root: Path, name: str, env_text: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "adapter.env").write_text(env_text, encoding="utf-8")
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(
        f"name: {name}\nenv:\n  file: ./adapter.env\n" + textwrap.dedent(body),
        encoding="utf-8",
    )
    return manifest


def test_rerank_enabled_via_consumer_env_file(tmp_path: Path) -> None:
    """A consumer may enable the adapter in its own `env.file` and declare
    `enable_rerank: true` in one manifest — no pre-editing Atlas `.env`. The
    host gate defaults to False (fresh checkout), the overlay flips it (#654)."""
    _write_root(tmp_path)
    manifest = _consumer_with_env_file(
        tmp_path, "c", "LIGHTRAG_RERANK_ADAPTER_ENABLED=true\n", _RERANK_ON
    )
    config = _load(tmp_path, manifest)  # default host gate = False
    assert config.lightrag_query_profiles[0].enable_rerank is True
    assert config.env_overrides["LIGHTRAG_RERANK_ADAPTER_ENABLED"] == "true"


def test_rerank_enabled_via_consumer_env_values(tmp_path: Path) -> None:
    _write_root(tmp_path)
    d = tmp_path / "c"
    d.mkdir(parents=True, exist_ok=True)
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(
        'name: c\nenv:\n  values:\n    LIGHTRAG_RERANK_ADAPTER_ENABLED: "true"\n'
        + _RERANK_ON,
        encoding="utf-8",
    )
    assert _load(tmp_path, manifest).lightrag_query_profiles[0].enable_rerank is True


def test_rerank_still_fails_when_effective_flag_false(tmp_path: Path) -> None:
    """A rerank profile still fails clearly when the effective merged flag is
    false — the overlay explicitly disables it here."""
    _write_root(tmp_path)
    manifest = _consumer_with_env_file(
        tmp_path, "c", "LIGHTRAG_RERANK_ADAPTER_ENABLED=false\n", _RERANK_ON
    )
    with pytest.raises(
        ConsumerManifestError,
        match="requires the LightRAG rerank adapter to be enabled",
    ):
        _load(tmp_path, manifest)


def test_consumer_env_overrides_operator_disabled(tmp_path: Path) -> None:
    """Precedence: a consumer manifest env value overrides the base `.env`, so
    the overlay enables the gate even when the host flag is False."""
    _write_root(tmp_path)
    manifest = _consumer_with_env_file(
        tmp_path, "c", "LIGHTRAG_RERANK_ADAPTER_ENABLED=true\n", _RERANK_ON
    )
    config = load_consumer_config(
        tmp_path, explicit_paths=[str(manifest)], lightrag_rerank_adapter_enabled=False
    )
    assert config.lightrag_query_profiles[0].enable_rerank is True


def test_consumer_env_false_overrides_operator_enabled(tmp_path: Path) -> None:
    """Precedence, other direction: a consumer value of false wins over a host
    flag of true (consumer env overrides base `.env`), so the gate closes."""
    _write_root(tmp_path)
    manifest = _consumer_with_env_file(
        tmp_path, "c", "LIGHTRAG_RERANK_ADAPTER_ENABLED=false\n", _RERANK_ON
    )
    with pytest.raises(
        ConsumerManifestError,
        match="requires the LightRAG rerank adapter to be enabled",
    ):
        load_consumer_config(
            tmp_path, explicit_paths=[str(manifest)], lightrag_rerank_adapter_enabled=True
        )
