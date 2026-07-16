"""Backend plugin manifest (plugin.yml) validation — #402."""
import textwrap

import pytest

from plugin_manifest import (
    PluginManifest,
    PluginManifestError,
    load_manifest,
    prefixes_overlap,
    validate_env,
)

TABLEAU_YML = textwrap.dedent(
    """
    plugin_manifest_version: 1
    name: tableau
    route_prefix: /tableau
    health_path: /tableau/health
    docs_url: https://github.com/thekaveh/tableau
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

# RAG-shaped fixture — the rag-showcase acceptance note asks for route/model
# visibility + health deps, not just env validation.
RAG_YML = textwrap.dedent(
    """
    plugin_manifest_version: 1
    name: rag
    route_prefix: /rag
    health_path: /rag/health
    docs_url: https://github.com/thekaveh/rag-showcase
    auth: inherit
    depends_on: [litellm, weaviate, lightrag, n8n]
    env:
      - name: RAG_ROLES_FILE
        required: true
      - name: RAG_MODELS_FILE
        required: true
      - name: RAG_FLAVORS_FILE
        required: true
      - name: LITELLM_MASTER_KEY
        secret: true
    """
)


def _write(tmp_path, body: str):
    pkg = tmp_path / "plug"
    pkg.mkdir()
    (pkg / "plugin.yml").write_text(body, encoding="utf-8")
    return pkg


def test_absent_manifest_returns_none(tmp_path):
    pkg = tmp_path / "plug"
    pkg.mkdir()
    assert load_manifest(pkg) is None


def test_tableau_manifest_parses(tmp_path):
    m = load_manifest(_write(tmp_path, TABLEAU_YML))
    assert isinstance(m, PluginManifest)
    assert m.name == "tableau"
    assert m.route_prefix == "/tableau"
    assert m.auth == "key-auth"
    assert m.prefix_head == "tableau"
    assert [e.name for e in m.env] == ["TABLEAU_EXECUTION", "LITELLM_MASTER_KEY"]


def test_rag_manifest_parses(tmp_path):
    m = load_manifest(_write(tmp_path, RAG_YML))
    assert m.name == "rag"
    assert m.depends_on == ["litellm", "weaviate", "lightrag", "n8n"]
    assert {e.name for e in m.env if e.required} == {
        "RAG_ROLES_FILE",
        "RAG_MODELS_FILE",
        "RAG_FLAVORS_FILE",
    }


def test_malformed_yaml_raises(tmp_path):
    with pytest.raises(PluginManifestError) as exc:
        load_manifest(_write(tmp_path, "name: [unclosed\n"))
    assert exc.value.plugin == "plug"


def test_wrong_version_raises(tmp_path):
    with pytest.raises(PluginManifestError) as exc:
        load_manifest(_write(tmp_path, "plugin_manifest_version: 2\nname: x\nroute_prefix: /x\n"))
    assert "plugin_manifest_version" in exc.value.message


def test_unknown_field_raises(tmp_path):
    body = "plugin_manifest_version: 1\nname: x\nroute_prefix: /x\nbogus: 1\n"
    with pytest.raises(PluginManifestError):
        load_manifest(_write(tmp_path, body))


def test_bad_auth_enum_raises(tmp_path):
    body = "plugin_manifest_version: 1\nname: x\nroute_prefix: /x\nauth: superuser\n"
    with pytest.raises(PluginManifestError) as exc:
        load_manifest(_write(tmp_path, body))
    assert "auth" in exc.value.message


def test_route_prefix_requires_leading_slash(tmp_path):
    body = "plugin_manifest_version: 1\nname: x\nroute_prefix: tableau\n"
    with pytest.raises(PluginManifestError):
        load_manifest(_write(tmp_path, body))


# ── review B1/B2 regressions: bare '/' and validator drift are auth bypasses ──

def test_bare_root_prefix_rejected(tmp_path):
    """route_prefix: '/' would match the whole backend and neuter auth (B1)."""
    with pytest.raises(PluginManifestError):
        load_manifest(_write(tmp_path, "plugin_manifest_version: 1\nname: x\nroute_prefix: /\n"))


def test_route_prefix_charset_rejected(tmp_path):
    """A prefix jsonschema would reject (space) must also be rejected here, else
    the container mounts a plugin the host-side auth derivation drops (B2)."""
    with pytest.raises(PluginManifestError):
        load_manifest(_write(tmp_path, 'plugin_manifest_version: 1\nname: x\nroute_prefix: "/a b"\n'))


def test_version_string_not_coerced(tmp_path):
    """Strict typing: plugin_manifest_version: "1" (string) must be rejected, not
    coerced — jsonschema rejects it too (B2 drift)."""
    with pytest.raises(PluginManifestError):
        load_manifest(_write(tmp_path, 'plugin_manifest_version: "1"\nname: x\nroute_prefix: /x\n'))


def test_bool_not_coerced(tmp_path):
    """Strict typing: required: "yes" (string) must be rejected, not coerced to
    True — jsonschema rejects it too (B2 drift)."""
    body = (
        "plugin_manifest_version: 1\nname: x\nroute_prefix: /x\n"
        'env:\n  - name: A\n    required: "yes"\n'
    )
    with pytest.raises(PluginManifestError):
        load_manifest(_write(tmp_path, body))


def test_prefixes_overlap_semantics():
    assert prefixes_overlap("/a", "/ab")          # raw-prefix containment
    assert prefixes_overlap("/heal", "/health")   # shorter shadows longer
    assert prefixes_overlap("/x", "/x")           # equal
    assert not prefixes_overlap("/tableau", "/rag")


def test_lightrag_and_framework_routes_are_reserved():
    from plugin_manifest import RESERVED_ROUTE_PREFIXES

    assert {"docs", "lightrag", "metrics", "openapi.json", "ready", "redoc"}.issubset(
        RESERVED_ROUTE_PREFIXES
    )


def test_env_summary_masks_secrets(tmp_path):
    m = load_manifest(_write(tmp_path, TABLEAU_YML))
    summary = m.env_summary({"TABLEAU_EXECUTION": "comfyui", "LITELLM_MASTER_KEY": "sk-super-secret"})
    by_name = {row["name"]: row for row in summary}
    assert by_name["TABLEAU_EXECUTION"]["value"] == "comfyui"
    assert by_name["LITELLM_MASTER_KEY"]["value"] == "***"
    # the raw secret must not appear anywhere in the summary
    assert "sk-super-secret" not in repr(summary)


def test_validate_env_flags_missing_required(tmp_path):
    m = load_manifest(_write(tmp_path, TABLEAU_YML))
    warnings = validate_env(m, {"TABLEAU_EXECUTION": "comfyui"})
    assert any("LITELLM_MASTER_KEY" in w and "required" in w for w in warnings)


def test_validate_env_flags_enum_mismatch(tmp_path):
    m = load_manifest(_write(tmp_path, TABLEAU_YML))
    warnings = validate_env(m, {"TABLEAU_EXECUTION": "banana", "LITELLM_MASTER_KEY": "k"})
    assert any("TABLEAU_EXECUTION" in w and "allowed values" in w for w in warnings)


def test_validate_env_never_echoes_secret_value(tmp_path):
    body = textwrap.dedent(
        """
        plugin_manifest_version: 1
        name: secretplug
        route_prefix: /secretplug
        env:
          - name: SECRET_INT
            type: int
            secret: true
        """
    )
    m = load_manifest(_write(tmp_path, body))
    warnings = validate_env(m, {"SECRET_INT": "not-a-number-leak"})
    assert warnings  # mismatch flagged
    assert "not-a-number-leak" not in " ".join(warnings)
    assert "***" in " ".join(warnings)
