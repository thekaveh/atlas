"""Host-time plugin.yml validation, discovery, and Kong-auth derivation (#402)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from core.plugin_manifest import (
    PluginManifestError,
    derive_route_auth,
    discover_plugin_manifests,
    load_plugin_manifest,
    prefixes_overlap,
    validate_plugin_env,
)

TABLEAU_YML = textwrap.dedent(
    """
    plugin_manifest_version: 1
    name: tableau
    route_prefix: /tableau
    health_path: /tableau/health
    auth: key-auth
    env:
      - name: TABLEAU_EXECUTION
        type: enum
        values: [fake, comfyui]
        default: comfyui
      - name: LITELLM_MASTER_KEY
        required: true
        secret: true
    """
)

RAG_YML = textwrap.dedent(
    """
    plugin_manifest_version: 1
    name: rag
    route_prefix: /rag
    auth: inherit
    depends_on: [litellm, weaviate, lightrag, n8n]
    env:
      - name: RAG_ROLES_FILE
        required: true
    """
)


def _pkg(root: Path, dirname: str, body: str | None) -> Path:
    pkg = root / dirname
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("router = None\n")
    if body is not None:
        (pkg / "plugin.yml").write_text(body, encoding="utf-8")
    return pkg


def test_absent_manifest_returns_none(tmp_path):
    pkg = _pkg(tmp_path, "plug", None)
    assert load_plugin_manifest(pkg) is None


def test_valid_manifest_loads(tmp_path):
    m = load_plugin_manifest(_pkg(tmp_path, "tableau", TABLEAU_YML))
    assert m.name == "tableau"
    assert m.route_prefix == "/tableau"
    assert m.auth == "key-auth"
    assert m.prefix_head == "tableau"


def test_malformed_manifest_raises(tmp_path):
    with pytest.raises(PluginManifestError):
        load_plugin_manifest(_pkg(tmp_path, "plug", "name: [unclosed\n"))


def test_schema_violation_raises(tmp_path):
    body = "plugin_manifest_version: 2\nname: x\nroute_prefix: /x\n"
    with pytest.raises(PluginManifestError) as exc:
        load_plugin_manifest(_pkg(tmp_path, "plug", body))
    assert "schema violation" in exc.value.message


def test_unknown_field_rejected(tmp_path):
    body = "plugin_manifest_version: 1\nname: x\nroute_prefix: /x\nbogus: 1\n"
    with pytest.raises(PluginManifestError):
        load_plugin_manifest(_pkg(tmp_path, "plug", body))


def test_discover_collects_valid_and_reports_conflicts(tmp_path):
    _pkg(tmp_path, "tableau", TABLEAU_YML)
    _pkg(tmp_path, "rag", RAG_YML)
    # reserved prefix → rejected
    _pkg(tmp_path, "sneaky", "plugin_manifest_version: 1\nname: sneaky\nroute_prefix: /health\n")
    # duplicate name → rejected
    _pkg(tmp_path, "dup", "plugin_manifest_version: 1\nname: tableau\nroute_prefix: /other\n")

    result = discover_plugin_manifests([tmp_path])
    names = {m.name for m in result.manifests}
    assert names == {"tableau", "rag"}
    assert any("shadows built-in" in e for e in result.errors)
    assert any("duplicate plugin name" in e for e in result.errors)


def test_overlapping_prefix_reported(tmp_path):
    _pkg(tmp_path, "one", "plugin_manifest_version: 1\nname: one\nroute_prefix: /shared\n")
    _pkg(tmp_path, "two", "plugin_manifest_version: 1\nname: two\nroute_prefix: /shared/sub\n")
    result = discover_plugin_manifests([tmp_path])
    assert {m.name for m in result.manifests} == {"one"}
    assert any("overlaps prefix" in e for e in result.errors)


def test_bare_root_prefix_rejected(tmp_path):
    """jsonschema must reject route_prefix: '/' (auth-neutering, review B1)."""
    with pytest.raises(PluginManifestError):
        load_plugin_manifest(_pkg(tmp_path, "p", "plugin_manifest_version: 1\nname: p\nroute_prefix: /\n"))


def test_prefix_containment_overlap_reported(tmp_path):
    """`/zeta` and `/zetax` overlap under Kong raw-prefix matching (review M1)."""
    _pkg(tmp_path, "a", "plugin_manifest_version: 1\nname: aa\nroute_prefix: /zeta\n")
    _pkg(tmp_path, "b", "plugin_manifest_version: 1\nname: bb\nroute_prefix: /zetax\n")
    result = discover_plugin_manifests([tmp_path])
    assert {m.name for m in result.manifests} == {"aa"}
    assert any("overlaps prefix" in e for e in result.errors)


def test_reserved_overlap_shorter_prefix_reported(tmp_path):
    """`/heal` intercepts the built-in `/health` and must be rejected (M1)."""
    _pkg(tmp_path, "h", "plugin_manifest_version: 1\nname: heal\nroute_prefix: /heal\n")
    result = discover_plugin_manifests([tmp_path])
    assert not result.manifests
    assert any("shadows built-in" in e for e in result.errors)


def test_framework_and_lightrag_prefixes_are_reserved():
    from core.plugin_manifest import RESERVED_ROUTE_PREFIXES

    assert {"docs", "lightrag", "metrics", "openapi.json", "ready", "redoc"}.issubset(
        RESERVED_ROUTE_PREFIXES
    )


def test_prefixes_overlap_semantics():
    assert prefixes_overlap("/a", "/ab")
    assert prefixes_overlap("/heal", "/health")
    assert not prefixes_overlap("/tableau", "/rag")


def test_derive_route_auth_skips_inherit(tmp_path):
    _pkg(tmp_path, "tableau", TABLEAU_YML)  # key-auth
    _pkg(tmp_path, "rag", RAG_YML)          # inherit → excluded
    _pkg(tmp_path, "pub", "plugin_manifest_version: 1\nname: pub\nroute_prefix: /pub\nauth: open\n")
    result = discover_plugin_manifests([tmp_path])
    policy = derive_route_auth(result.manifests)
    assert ("/tableau", "key-auth") in policy
    assert ("/pub", "open") in policy
    assert all(prefix != "/rag" for prefix, _ in policy)  # inherit contributes nothing


def test_validate_plugin_env_flags_required_missing_and_masks_secret(tmp_path):
    m = load_plugin_manifest(_pkg(tmp_path, "tableau", TABLEAU_YML))
    warnings = validate_plugin_env(m, {"TABLEAU_EXECUTION": "comfyui"})
    assert any("LITELLM_MASTER_KEY" in w and "required" in w for w in warnings)
    # enum mismatch, secret masking
    warnings2 = validate_plugin_env(m, {"TABLEAU_EXECUTION": "banana", "LITELLM_MASTER_KEY": "sk-x"})
    assert any("allowed values" in w for w in warnings2)
    assert "sk-x" not in " ".join(warnings2)
