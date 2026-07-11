"""Consumer LiteLLM model contract (#411).

A consumer declares OpenAI-compatible model aliases in a versioned
``litellm_models:`` block. Atlas resolves each ``api_base`` against an allowlist
of approved in-network Atlas endpoints, stamps manifest-derived (non-spoofable)
ownership, rejects credential literals / unresolved interpolation / reserved and
colliding aliases, and compiles the rows to a generated file (merged by
litellm-init before LiteLLM starts) plus a companion api-key compose overlay.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from core.consumer_manifest import (
    ConsumerManifestError,
    LITELLM_ENDPOINT_TEMPLATES,
    litellm_credential_vars,
    load_consumer_config,
    render_litellm_models_file,
    render_litellm_models_overlay,
)


def _write_root(root: Path) -> None:
    (root / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")


def _write_manifest(root: Path, name: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(
        f"name: {name}\n" + textwrap.dedent(body), encoding="utf-8"
    )
    return manifest


# ── happy path ──────────────────────────────────────────────────────

def test_single_consumer_single_model(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "rag-showcase",
        """
        litellm_models:
          version: 1
          models:
            - name: graph-rag
              api_base: "${ATLAS_BACKEND_INTERNAL}/graph-rag/v1"
              api_key_var: RAG_SHOWCASE_API_KEY
              description: Graph RAG over Neo4j
              tags: [rag, graph]
              model_info:
                mode: chat
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert len(config.litellm_models) == 1
    model = config.litellm_models[0]
    assert model.name == "graph-rag"
    assert model.consumer == "rag-showcase"
    assert model.api_base == "http://backend:8000/graph-rag/v1"

    row = model.to_row()
    assert row["model_name"] == "graph-rag"
    assert row["litellm_params"]["model"] == "openai/graph-rag"
    assert row["litellm_params"]["api_base"] == "http://backend:8000/graph-rag/v1"
    # Secret is a REFERENCE, never a literal value.
    assert row["litellm_params"]["api_key"] == "os.environ/RAG_SHOWCASE_API_KEY"
    # Ownership is stamped from the manifest.
    assert row["model_info"]["atlas_owner"] == "rag-showcase"
    assert row["model_info"]["atlas_managed"] is True
    assert row["model_info"]["mode"] == "chat"
    assert row["model_info"]["tags"] == ["rag", "graph"]


def test_generated_file_and_overlay_present(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "acme",
        """
        litellm_models:
          version: 1
          models:
            - name: acme-chat
              api_base: "${ATLAS_BACKEND_INTERNAL}/acme/v1"
              api_key_var: ACME_API_KEY
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])

    assert config.litellm_models_file is not None
    assert config.litellm_models_file.path == tmp_path / "volumes/litellm/consumer-models.yaml"
    parsed = yaml.safe_load(config.litellm_models_file.content)
    assert [r["model_name"] for r in parsed["model_list"]] == ["acme-chat"]

    assert config.litellm_overlay is not None
    assert config.litellm_overlay.path == tmp_path / "volumes/litellm/consumer-models.compose.yml"
    overlay = yaml.safe_load(config.litellm_overlay.content)
    assert overlay["services"]["litellm"]["environment"]["ACME_API_KEY"] == "${ACME_API_KEY:-}"


def test_model_without_api_key_var_has_no_overlay(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "keyless",
        """
        litellm_models:
          version: 1
          models:
            - name: keyless-model
              api_base: "${ATLAS_BACKEND_INTERNAL}/keyless/v1"
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.litellm_models_file is not None
    # No api_key_var declared → no key injected, no overlay generated.
    assert config.litellm_overlay is None
    row = config.litellm_models[0].to_row()
    assert "api_key" not in row["litellm_params"]


# ── multiple consumers / ordering / byte-stability ──────────────────

def test_multiple_consumers_ordering_and_isolation(tmp_path: Path) -> None:
    _write_root(tmp_path)
    a = _write_manifest(
        tmp_path,
        "alpha",
        """
        litellm_models:
          version: 1
          models:
            - name: alpha-one
              api_base: "${ATLAS_BACKEND_INTERNAL}/alpha/v1"
        """,
    )
    b = _write_manifest(
        tmp_path,
        "beta",
        """
        litellm_models:
          version: 1
          models:
            - name: beta-one
              api_base: "${ATLAS_BACKEND_INTERNAL}/beta/v1"
              api_key_var: BETA_KEY
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(a), str(b)])
    names = [m.name for m in config.litellm_models]
    assert names == ["alpha-one", "beta-one"]  # manifest order preserved
    owners = {m.name: m.consumer for m in config.litellm_models}
    assert owners == {"alpha-one": "alpha", "beta-one": "beta"}
    # Only beta declares a key → overlay carries exactly BETA_KEY.
    assert litellm_credential_vars(config.litellm_models) == ["BETA_KEY"]


def test_generated_output_is_byte_stable(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "stable",
        """
        litellm_models:
          version: 1
          models:
            - name: stable-a
              api_base: "${ATLAS_BACKEND_INTERNAL}/a/v1"
              api_key_var: STABLE_KEY
            - name: stable-b
              api_base: "${ATLAS_BACKEND_INTERNAL}/b/v1"
        """,
    )
    first = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    second = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert first.litellm_models_file.content == second.litellm_models_file.content
    assert first.litellm_overlay.content == second.litellm_overlay.content
    # Deterministic direct render.
    assert render_litellm_models_file(first.litellm_models) == first.litellm_models_file.content
    assert render_litellm_models_overlay(first.litellm_models) == first.litellm_overlay.content


# ── removal semantics ───────────────────────────────────────────────

def test_no_litellm_models_yields_no_artifacts(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "plain",
        """
        env:
          values:
            SOME_VAR: "1"
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.litellm_models == ()
    assert config.litellm_models_file is None
    assert config.litellm_overlay is None


# ── collisions / reserved ───────────────────────────────────────────

def test_duplicate_alias_within_consumer_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "dup",
        """
        litellm_models:
          version: 1
          models:
            - name: same
              api_base: "${ATLAS_BACKEND_INTERNAL}/a/v1"
            - name: same
              api_base: "${ATLAS_BACKEND_INTERNAL}/b/v1"
        """,
    )
    with pytest.raises(ConsumerManifestError, match="duplicate litellm_models alias 'same'"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_duplicate_alias_across_consumers_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    a = _write_manifest(
        tmp_path,
        "alpha",
        """
        litellm_models:
          version: 1
          models:
            - name: shared
              api_base: "${ATLAS_BACKEND_INTERNAL}/a/v1"
        """,
    )
    b = _write_manifest(
        tmp_path,
        "beta",
        """
        litellm_models:
          version: 1
          models:
            - name: shared
              api_base: "${ATLAS_BACKEND_INTERNAL}/b/v1"
        """,
    )
    with pytest.raises(ConsumerManifestError, match="declared by multiple consumers"):
        load_consumer_config(tmp_path, explicit_paths=[str(a), str(b)])


@pytest.mark.parametrize("reserved", ["hermes-agent", "lightrag"])
def test_reserved_alias_rejected(tmp_path: Path, reserved: str) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "shadow",
        f"""
        litellm_models:
          version: 1
          models:
            - name: {reserved}
              api_base: "${{ATLAS_BACKEND_INTERNAL}}/x/v1"
        """,
    )
    with pytest.raises(ConsumerManifestError, match="reserved for a stack-owned model"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


# ── ownership (non-spoofable) ───────────────────────────────────────

def test_owner_may_restate_own_name(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "self-own",
        """
        litellm_models:
          version: 1
          models:
            - name: mine
              api_base: "${ATLAS_BACKEND_INTERNAL}/mine/v1"
              owner: self-own
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.litellm_models[0].consumer == "self-own"


def test_spoofed_owner_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "honest",
        """
        litellm_models:
          version: 1
          models:
            - name: sneaky
              api_base: "${ATLAS_BACKEND_INTERNAL}/x/v1"
              owner: someone-else
        """,
    )
    with pytest.raises(ConsumerManifestError, match="cannot\\s+be spoofed"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


# ── api_base resolution / rejection ─────────────────────────────────

def test_literal_api_key_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "leaky",
        """
        litellm_models:
          version: 1
          models:
            - name: leaky
              api_base: "${ATLAS_BACKEND_INTERNAL}/x/v1"
              api_key: sk-super-secret
        """,
    )
    with pytest.raises(ConsumerManifestError, match="may not set a literal api_key"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_unapproved_host_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "ssrf",
        """
        litellm_models:
          version: 1
          models:
            - name: exfil
              api_base: "http://evil.example.com/v1"
        """,
    )
    with pytest.raises(ConsumerManifestError, match="not an approved Atlas endpoint"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_unapproved_template_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "badtpl",
        """
        litellm_models:
          version: 1
          models:
            - name: bad
              api_base: "${ATLAS_SECRET_STORE}/v1"
        """,
    )
    with pytest.raises(ConsumerManifestError, match="unapproved endpoint template"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_unresolved_interpolation_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    # A non-template ${...} shape that our token regex won't substitute must be
    # rejected rather than passed through verbatim into a generated row.
    manifest = _write_manifest(
        tmp_path,
        "interp",
        """
        litellm_models:
          version: 1
          models:
            - name: bad
              api_base: "http://backend:8000/$(whoami)/v1"
        """,
    )
    with pytest.raises(ConsumerManifestError, match="unresolved interpolation"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_userinfo_credentials_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "userinfo",
        """
        litellm_models:
          version: 1
          models:
            - name: bad
              api_base: "http://user:pass@backend:8000/v1"
        """,
    )
    with pytest.raises(ConsumerManifestError, match="userinfo credentials|credential literals"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


@pytest.mark.parametrize(
    "bad_base",
    [
        "${ATLAS_BACKEND_INTERNAL}/x/v1?api_key=sk-leak",
        # The regression case: a denylist keyed on "api_key=" misses these.
        "${ATLAS_BACKEND_INTERNAL}/v1?authorization=Bearer%20sk-live-REALSECRET",
        "${ATLAS_BACKEND_INTERNAL}/v1?auth=sk-leak",
        "${ATLAS_BACKEND_INTERNAL}/v1?token=abc123",
    ],
)
def test_query_string_rejected(tmp_path: Path, bad_base: str) -> None:
    # Any query string is rejected structurally — it is the usual credential
    # carrier and a denylist of parameter names is trivially bypassed.
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "querykey",
        f"""
        litellm_models:
          version: 1
          models:
            - name: bad
              api_base: "{bad_base}"
        """,
    )
    with pytest.raises(ConsumerManifestError, match="query string or fragment"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_fragment_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "fragkey",
        """
        litellm_models:
          version: 1
          models:
            - name: bad
              api_base: "${ATLAS_BACKEND_INTERNAL}/v1#sk-live-REALSECRET"
        """,
    )
    with pytest.raises(ConsumerManifestError, match="query string or fragment"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_generated_row_and_config_never_carry_a_query_secret(tmp_path: Path) -> None:
    # End-to-end guard for the leak surface: a secret embedded in the api_base
    # query must never reach the generated file (it is rejected before rendering).
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "leaky2",
        """
        litellm_models:
          version: 1
          models:
            - name: ok-model
              api_base: "${ATLAS_BACKEND_INTERNAL}/ok/v1"
            - name: bad-model
              api_base: "${ATLAS_BACKEND_INTERNAL}/v1?authorization=Bearer%20sk-live-LEAK"
        """,
    )
    with pytest.raises(ConsumerManifestError):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


@pytest.mark.parametrize("catalog_name", ["gpt-4o", "gpt-4o-mini", "nomic-embed-text"])
def test_catalog_model_alias_rejected(tmp_path: Path, catalog_name: str) -> None:
    # A consumer alias that matches a YAML-catalog model name is rejected up
    # front so it can't shadow / load-balance against the real stack model.
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "hijack",
        f"""
        litellm_models:
          version: 1
          models:
            - name: {catalog_name}
              api_base: "${{ATLAS_BACKEND_INTERNAL}}/evil/v1"
              api_key_var: EVIL_KEY
        """,
    )
    with pytest.raises(ConsumerManifestError, match="reserved for a stack-owned model"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_bad_api_key_var_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "badvar",
        """
        litellm_models:
          version: 1
          models:
            - name: x
              api_base: "${ATLAS_BACKEND_INTERNAL}/x/v1"
              api_key_var: not-an-env-name
        """,
    )
    with pytest.raises(ConsumerManifestError, match="UPPER_SNAKE env var NAME"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


# ── schema / versioning ─────────────────────────────────────────────

@pytest.mark.parametrize("version", ["2", "0", "missing"])
def test_version_must_be_one(tmp_path: Path, version: str) -> None:
    _write_root(tmp_path)
    version_line = "" if version == "missing" else f"  version: {version}\n"
    manifest = _write_manifest(
        tmp_path,
        "ver",
        "litellm_models:\n"
        + version_line
        + "  models:\n"
        + "    - name: x\n"
        + '      api_base: "${ATLAS_BACKEND_INTERNAL}/x/v1"\n',
    )
    with pytest.raises(ConsumerManifestError, match="litellm_models.version must be 1"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_empty_models_list_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "empty",
        """
        litellm_models:
          version: 1
          models: []
        """,
    )
    with pytest.raises(ConsumerManifestError, match="must be a non-empty list"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_unknown_field_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "typo",
        """
        litellm_models:
          version: 1
          models:
            - name: x
              api_base: "${ATLAS_BACKEND_INTERNAL}/x/v1"
              descriptionn: typo
        """,
    )
    with pytest.raises(ConsumerManifestError, match="unknown field"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_bad_alias_shape_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "badalias",
        """
        litellm_models:
          version: 1
          models:
            - name: Bad_Alias
              api_base: "${ATLAS_BACKEND_INTERNAL}/x/v1"
        """,
    )
    with pytest.raises(ConsumerManifestError, match="must match"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


# ── secret masking (no raw values anywhere) ─────────────────────────

def test_generated_artifacts_never_contain_secret_values(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "masked",
        """
        env:
          values:
            MASKED_API_KEY: "sk-this-must-never-leak"
        litellm_models:
          version: 1
          models:
            - name: masked-model
              api_base: "${ATLAS_BACKEND_INTERNAL}/masked/v1"
              api_key_var: MASKED_API_KEY
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    # The literal value the consumer set for the key var never appears in the
    # generated file or overlay — only the var NAME / os.environ reference.
    assert "sk-this-must-never-leak" not in config.litellm_models_file.content
    assert "sk-this-must-never-leak" not in config.litellm_overlay.content
    assert "os.environ/MASKED_API_KEY" in config.litellm_models_file.content


# ── contract sanity ─────────────────────────────────────────────────

def test_backend_template_resolves_to_in_network_url() -> None:
    # BASE_PORT-independent in-network base (container-internal port).
    assert LITELLM_ENDPOINT_TEMPLATES["ATLAS_BACKEND_INTERNAL"] == "http://backend:8000"
