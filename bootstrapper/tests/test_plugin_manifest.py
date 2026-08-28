"""Host-time plugin.yml validation, discovery, and Kong-auth derivation (#402)."""
from __future__ import annotations

import textwrap
import subprocess
import sys
from pathlib import Path

import pytest

from core.plugin_manifest import (
    PluginManifestError,
    derive_route_auth,
    derive_route_timeouts,
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


def test_timeout_fields_load_and_derive_explicit_values_only(tmp_path):
    timed = textwrap.dedent(
        """
        plugin_manifest_version: 1
        name: timed
        route_prefix: /timed
        connect_timeout: 1
        read_timeout: 2147483646
        """
    )
    _pkg(tmp_path, "timed", timed)
    _pkg(tmp_path, "rag", RAG_YML)

    result = discover_plugin_manifests([tmp_path])
    manifest = next(item for item in result.manifests if item.name == "timed")

    assert manifest.connect_timeout == 1
    assert manifest.write_timeout is None
    assert manifest.read_timeout == 2147483646
    assert derive_route_timeouts(result.manifests) == [
        (
            "timed",
            "/timed",
            {"connect_timeout": 1, "read_timeout": 2147483646},
        )
    ]


@pytest.mark.parametrize("field", ["connect_timeout", "write_timeout", "read_timeout"])
@pytest.mark.parametrize(
    "value",
    ["0", "-1", "2147483647", "true", "1.0", "1.5", '"60000"', "null"],
)
def test_timeout_fields_reject_values_outside_kong_integer_contract(
    tmp_path, field, value
):
    body = (
        "plugin_manifest_version: 1\n"
        "name: timed\n"
        "route_prefix: /timed\n"
        f"{field}: {value}\n"
    )

    with pytest.raises(PluginManifestError):
        load_plugin_manifest(_pkg(tmp_path, "timed", body))


def test_malformed_manifest_raises(tmp_path):
    with pytest.raises(PluginManifestError):
        load_plugin_manifest(_pkg(tmp_path, "plug", "name: [unclosed\n"))


def test_non_utf8_manifest_is_a_plugin_scoped_error(tmp_path):
    invalid = _pkg(tmp_path, "invalid", TABLEAU_YML)
    (invalid / "plugin.yml").write_bytes(b"\xff")

    with pytest.raises(PluginManifestError, match="could not parse YAML"):
        load_plugin_manifest(invalid)


def test_discovery_continues_after_non_utf8_manifest(tmp_path):
    invalid = _pkg(tmp_path, "invalid", TABLEAU_YML)
    (invalid / "plugin.yml").write_bytes(b"\xff")
    _pkg(tmp_path, "valid", RAG_YML)

    result = discover_plugin_manifests([tmp_path])

    assert [manifest.name for manifest in result.manifests] == ["rag"]
    assert any("invalid plugin.yml for 'invalid'" in error for error in result.errors)


def test_plugin_manifest_supports_package_style_import(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.delenv("PYTHONPATH", raising=False)

    completed = subprocess.run(
        [sys.executable, "-c", "import bootstrapper.core.plugin_manifest"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_schema_violation_raises(tmp_path):
    body = "plugin_manifest_version: 2\nname: x\nroute_prefix: /x\n"
    with pytest.raises(PluginManifestError) as exc:
        load_plugin_manifest(_pkg(tmp_path, "plug", body))
    assert "schema violation" in exc.value.message


def test_unknown_field_rejected(tmp_path):
    body = "plugin_manifest_version: 1\nname: x\nroute_prefix: /x\nbogus: 1\n"
    with pytest.raises(PluginManifestError):
        load_plugin_manifest(_pkg(tmp_path, "plug", body))


@pytest.mark.parametrize(
    "duplicate",
    (
        "auth: key-auth\nauth: open\n",
        "route_prefix: /first\nroute_prefix: /second\n",
        "env:\n  - name: TOKEN\n    required: true\n    required: false\n",
    ),
)
def test_duplicate_plugin_policy_fields_are_rejected(tmp_path, duplicate):
    body = "plugin_manifest_version: 1\nname: strict\n"
    if "route_prefix:" not in duplicate:
        body += "route_prefix: /strict\n"
    body += duplicate

    with pytest.raises(PluginManifestError, match="duplicate key"):
        load_plugin_manifest(_pkg(tmp_path, "strict", body))


def test_plugin_manifest_allows_explicit_yaml_merge_override(tmp_path):
    body = (
        "plugin_manifest_version: 1\nname: strict\nroute_prefix: /strict\n"
        "<<: &defaults {auth: inherit}\nauth: key-auth\n"
    )

    assert load_plugin_manifest(_pkg(tmp_path, "strict", body)).auth == "key-auth"


def test_plugin_manifest_allows_merge_sequence_with_explicit_override(tmp_path):
    body = (
        "plugin_manifest_version: 1\nname: strict\nroute_prefix: /strict\n"
        "<<: [{auth: inherit}, {docs_url: https://example.test/docs}]\n"
        "auth: key-auth\n"
    )

    manifest = load_plugin_manifest(_pkg(tmp_path, "strict", body))

    assert manifest.auth == "key-auth"
    assert manifest.docs_url == "https://example.test/docs"


def test_plugin_manifest_rejects_duplicate_inside_merge_sequence(tmp_path):
    body = (
        "plugin_manifest_version: 1\nname: strict\nroute_prefix: /strict\n"
        "<<: [{auth: inherit, auth: open}]\n"
    )

    with pytest.raises(PluginManifestError, match="duplicate key"):
        load_plugin_manifest(_pkg(tmp_path, "strict", body))


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


def test_starter_derives_auth_and_timeout_policies_from_one_discovery(
    tmp_path, monkeypatch
):
    import start as start_module

    timed = textwrap.dedent(
        """
        plugin_manifest_version: 1
        name: tableau
        route_prefix: /tableau
        auth: key-auth
        read_timeout: 900000
        """
    )
    _pkg(tmp_path, "tableau", timed)
    monkeypatch.setattr(start_module, "_resolve_plugin_dirs", lambda _starter: [tmp_path])
    starter = start_module.AtlasStarter.__new__(start_module.AtlasStarter)

    route_auth, route_timeouts = starter._derive_plugin_route_policies()

    assert route_auth == [("/tableau", "key-auth")]
    assert route_timeouts == [
        ("tableau", "/tableau", {"read_timeout": 900000})
    ]


def test_generate_kong_configuration_assigns_both_plugin_policy_lists(
    tmp_path, monkeypatch
):
    import start as start_module
    from utils import kong_config_generator as kong_module

    _pkg(
        tmp_path,
        "tableau",
        "plugin_manifest_version: 1\nname: tableau\n"
        "route_prefix: /tableau\nauth: key-auth\nread_timeout: 900000\n",
    )
    _pkg(tmp_path, "broken", "plugin_manifest_version: 2\nname: broken\nroute_prefix: /broken\n")
    monkeypatch.setattr(start_module, "_resolve_plugin_dirs", lambda _starter: [tmp_path])
    captured = {}

    class FakeGenerator:
        def __init__(self, config_parser):
            self.config_parser = config_parser
            self.plugin_route_auth = []
            self.plugin_route_timeouts = []

        def generate_kong_config(self):
            captured["auth"] = self.plugin_route_auth
            captured["timeouts"] = self.plugin_route_timeouts
            return {"services": []}

        def validate_config(self, _config):
            return []

        def write_config(self, _config, _path):
            return True

    monkeypatch.setattr(kong_module, "KongConfigGenerator", FakeGenerator)
    starter = start_module.AtlasStarter.__new__(start_module.AtlasStarter)
    starter.config_parser = object()
    starter.root_dir = tmp_path
    starter.banner = type(
        "Banner",
        (),
        {
            "show_status_message": lambda *_args: None,
            "console": type("Console", (), {"print": lambda *_args: None})(),
        },
    )()
    starter._ensure_volume_dir_writable = lambda _path: None

    assert starter.generate_kong_configuration() is True
    assert captured == {
        "auth": [("/tableau", "key-auth")],
        "timeouts": [("tableau", "/tableau", {"read_timeout": 900000})],
    }


def test_validate_plugin_env_flags_required_missing_and_masks_secret(tmp_path):
    m = load_plugin_manifest(_pkg(tmp_path, "tableau", TABLEAU_YML))
    warnings = validate_plugin_env(m, {"TABLEAU_EXECUTION": "comfyui"})
    assert any("LITELLM_MASTER_KEY" in w and "required" in w for w in warnings)
    # enum mismatch, secret masking
    warnings2 = validate_plugin_env(m, {"TABLEAU_EXECUTION": "banana", "LITELLM_MASTER_KEY": "sk-x"})
    assert any("allowed values" in w for w in warnings2)
    assert "sk-x" not in " ".join(warnings2)
