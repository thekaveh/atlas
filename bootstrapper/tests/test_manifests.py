"""Tests for bootstrapper.services.manifests (loader)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from services.manifests import (
    Manifest,
    ManifestLoadError,
    load_manifests,
)


_TASK3_REVIEWED_INFRA_DATA_MANIFESTS = frozenset(
    {
        "backup",
        "cloudflared",
        "globals",
        "grafana",
        "iceberg-rest",
        "kong",
        "langfuse",
        "loki",
        "minio",
        "neo4j",
        "otel-collector",
        "prometheus",
        "ray",
        "redis",
        "redpanda",
        "spark",
        "supabase",
        "supavisor",
        "tempo",
        "trino",
        "weaviate",
    }
)

_TASK4_REVIEWED_LLM_MEDIA_MANIFESTS = frozenset(
    {
        "asset-baker",
        "asset-worker",
        "blender-mcp",
        "chatterbox",
        "cloud-providers",
        "comfyui",
        "crawl4ai",
        "docling",
        "docling-lightrag-adapter",
        "fal",
        "litellm",
        "ollama",
        "parakeet",
        "searxng",
        "speaches",
        "tei-reranker",
        "tika",
        "tts-provider",
        "vllm-metal",
    }
)

_TASK5_REVIEWED_AGENTS_APPS_MANIFESTS = frozenset(
    {
        "airflow",
        "backend",
        "celery",
        "hermes",
        "jenkins",
        "jupyterhub",
        "label-studio",
        "lightrag",
        "llm-graph-builder",
        "local-deep-researcher",
        "mcp-servers",
        "mlflow",
        "n8n",
        "open-webui",
        "openclaw",
        "verba",
        "zeppelin",
    }
)

_SYNTHETIC_CAPABILITY_YAML = (
    "capabilities:\n"
    "  - name: Synthetic service contract\n"
    "    status: supported\n"
    "    verification: tested\n"
    "    note: Tests exercise this synthetic manifest contract.\n"
)


def _assert_text_contract(text, *, contains=(), excludes=()):
    missing = tuple(fragment for fragment in contains if fragment not in text)
    unexpected = tuple(fragment for fragment in excludes if fragment in text)
    assert (missing, unexpected) == ((), ())


def _capability_named(manifest, name):
    matches = [
        capability for capability in manifest.capabilities if capability.name == name
    ]
    assert len(matches) == 1, (manifest.name, name, matches)
    return matches[0]


def _duplicate_capability_names(manifest):
    names = [capability.name for capability in manifest.capabilities]
    return sorted({name for name in names if names.count(name) > 1})


def _structurally_weak_capability_names(manifest):
    return [
        capability.name
        for capability in manifest.capabilities
        if len(capability.name.split()) < 2
        or len(capability.note.split()) < 5
        or capability.note.casefold() == capability.name.casefold()
    ]


def _assert_category_capability_quality(
    manifests, reviewed_names, expected_count, label
):
    discovered_names = {manifest.name for manifest in manifests}
    missing_contracts = sorted(
        manifest.name for manifest in manifests if not manifest.capabilities
    )
    duplicate_names = {
        manifest.name: duplicates
        for manifest in manifests
        if (duplicates := _duplicate_capability_names(manifest))
    }
    weak_names = {
        manifest.name: weak
        for manifest in manifests
        if (weak := _structurally_weak_capability_names(manifest))
    }
    assert (
        len(reviewed_names),
        sorted(reviewed_names - discovered_names),
        missing_contracts,
        duplicate_names,
        weak_names,
    ) == (expected_count, [], [], {}, {}), label


# ────────────────────────────────────────────────────────────────────────────
# Happy paths
# ────────────────────────────────────────────────────────────────────────────


def test_load_minimal_manifest(services_root, write_manifest, minimal_manifest_dict):
    write_manifest("redis", minimal_manifest_dict("redis"))
    manifests = load_manifests(services_root)
    assert len(manifests) == 1
    m = manifests[0]
    assert isinstance(m, Manifest)
    assert m.name == "redis"
    assert m.label == "Redis service"
    assert m.category == "data"
    assert m.containers == ["redis"]
    assert m.sources is None  # optional, omitted
    assert m.images == []     # optional → empty list
    assert m.depends_on.required == []
    assert m.depends_on.optional == []
    assert m.exports == []
    assert len(m.env) == 1
    assert m.env[0].name == "REDIS_PORT"
    assert m.env[0].default == 6379
    assert m.env[0].auto_managed is False
    assert [capability.name for capability in m.capabilities] == [
        "Synthetic service contract"
    ]


def test_load_capabilities_preserves_declaration_order(
    services_root, write_manifest, minimal_manifest_dict
):
    manifest = minimal_manifest_dict("redis")
    manifest["capabilities"] = [
        {
            "name": "Primary cache operations",
            "status": "supported",
            "verification": "tested",
            "note": "Atlas configures the in-stack Redis cache.",
        },
        {
            "name": "Cross-region replication",
            "status": "not-supported",
            "verification": "documented",
            "note": "Atlas does not configure Redis replication.",
        },
    ]
    write_manifest("redis", manifest)

    loaded = load_manifests(services_root)[0]

    assert [cap.name for cap in loaded.capabilities] == [
        "Primary cache operations",
        "Cross-region replication",
    ]
    assert loaded.capabilities[0].status == "supported"
    assert loaded.capabilities[0].verification == "tested"
    assert loaded.capabilities[0].note == "Atlas configures the in-stack Redis cache."


def test_capability_entries_are_immutable(
    services_root, write_manifest, minimal_manifest_dict
):
    manifest = minimal_manifest_dict("redis")
    manifest["capabilities"] = [
        {
            "name": "Primary cache operations",
            "status": "supported",
            "verification": "tested",
            "note": "Atlas configures the in-stack Redis cache.",
        }
    ]
    write_manifest("redis", manifest)

    capability = load_manifests(services_root)[0].capabilities[0]
    with pytest.raises(FrozenInstanceError):
        capability.status = "partial"


def test_load_full_manifest(services_root, write_manifest, full_manifest_dict):
    write_manifest("ollama", full_manifest_dict("ollama"))
    manifests = load_manifests(services_root)
    assert len(manifests) == 1
    m = manifests[0]
    assert m.name == "ollama"
    assert m.docs == "services/ollama/README.md"
    assert len(m.images) == 2
    assert m.images[0].var == "LLM_PROVIDER_IMAGE"
    assert m.sources is not None
    assert m.sources.var == "LLM_PROVIDER_SOURCE"
    assert m.sources.default == "ollama-container-cpu"
    assert len(m.sources.options) == 2
    assert m.sources.options[0].id == "ollama-container-cpu"
    assert m.sources.options[1].requires == ["OLLAMA_LOCALHOST_PORT"]
    # runtime_sc replaces the old sources.options[].effects (operational data)
    assert "llm_provider" in m.runtime_sc
    assert m.runtime_sc["llm_provider"]["ollama-container-cpu"]["environment"]["OLLAMA_ENDPOINT"] == "http://ollama:11434"
    assert m.depends_on.optional == []
    assert m.exports[0].name == "OLLAMA_ENDPOINT"
    assert m.exports[0].consumers == ["litellm", "weaviate"]


def test_load_multiple_manifests_in_deterministic_order(
    services_root, write_manifest, minimal_manifest_dict
):
    # Written out of order; load order should be alphabetical by folder name.
    write_manifest("ollama", minimal_manifest_dict("ollama") | {"category": "llm"})
    write_manifest("redis", minimal_manifest_dict("redis"))
    write_manifest("backend", minimal_manifest_dict("backend") | {"category": "apps"})
    manifests = load_manifests(services_root)
    assert [m.name for m in manifests] == ["backend", "ollama", "redis"]


def test_empty_services_dir_returns_empty_list(services_root):
    assert load_manifests(services_root) == []


def test_missing_services_dir_returns_empty_list(tmp_path):
    # Phase A: the services/ folder may not exist yet.
    assert load_manifests(tmp_path / "does-not-exist") == []


def test_underscore_prefixed_folders_are_ignored(
    services_root, write_manifest, minimal_manifest_dict
):
    # Downstream consumers can reserve services/_user/ as an overlay slot.
    # The loader should skip folders starting with `_` or `.`.
    write_manifest("redis", minimal_manifest_dict("redis"))
    (services_root / "_user").mkdir()
    (services_root / "_user" / "service.yml").write_text("name: should-be-ignored\n")
    (services_root / ".hidden").mkdir()
    manifests = load_manifests(services_root)
    assert [m.name for m in manifests] == ["redis"]


# ────────────────────────────────────────────────────────────────────────────
# Schema violations
# ────────────────────────────────────────────────────────────────────────────


def test_capabilities_is_a_top_level_required_schema_field():
    repo_root = Path(__file__).resolve().parent.parent.parent
    schema = json.loads(
        (repo_root / "bootstrapper/schemas/service.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert "capabilities" in schema["required"]


def test_manifest_without_capabilities_is_rejected(
    services_root, write_manifest, minimal_manifest_dict
):
    manifest = minimal_manifest_dict("redis")
    manifest.pop("capabilities", None)
    write_manifest("redis", manifest)

    with pytest.raises(ManifestLoadError, match="capabilities"):
        load_manifests(services_root)


def test_missing_required_field_rejected(services_root, write_manifest):
    write_manifest("redis", {"name": "redis", "label": "x", "category": "data"})
    # missing containers + env
    with pytest.raises(ManifestLoadError) as exc:
        load_manifests(services_root)
    msg = str(exc.value)
    assert "redis" in msg
    assert "containers" in msg or "required" in msg.lower()


def test_invalid_category_rejected(services_root, write_manifest, minimal_manifest_dict):
    bad = minimal_manifest_dict("redis")
    bad["category"] = "nonsense"
    write_manifest("redis", bad)
    with pytest.raises(ManifestLoadError):
        load_manifests(services_root)


def test_lowercase_env_var_name_rejected(services_root, write_manifest, minimal_manifest_dict):
    bad = minimal_manifest_dict("redis")
    bad["env"] = [{"name": "lower_case", "default": ""}]
    write_manifest("redis", bad)
    with pytest.raises(ManifestLoadError):
        load_manifests(services_root)


def test_unknown_field_rejected(services_root, write_manifest, minimal_manifest_dict):
    bad = minimal_manifest_dict("redis")
    bad["typo_field"] = "oops"
    write_manifest("redis", bad)
    with pytest.raises(ManifestLoadError):
        load_manifests(services_root)


@pytest.mark.parametrize("missing", ["name", "status", "verification", "note"])
def test_capability_missing_required_field_rejected(
    services_root, write_manifest, minimal_manifest_dict, missing
):
    capability = {
        "name": "Primary cache operations",
        "status": "supported",
        "verification": "tested",
        "note": "Atlas configures the in-stack Redis cache.",
    }
    capability.pop(missing)
    manifest = minimal_manifest_dict("redis") | {"capabilities": [capability]}
    write_manifest("redis", manifest)

    with pytest.raises(ManifestLoadError, match=missing):
        load_manifests(services_root)


def test_capability_extra_field_rejected(
    services_root, write_manifest, minimal_manifest_dict
):
    manifest = minimal_manifest_dict("redis") | {
        "capabilities": [
            {
                "name": "Primary cache operations",
                "status": "supported",
                "verification": "tested",
                "note": "Atlas configures the in-stack Redis cache.",
                "details": "not part of the contract",
            }
        ]
    }
    write_manifest("redis", manifest)

    with pytest.raises(ManifestLoadError, match="details"):
        load_manifests(services_root)


@pytest.mark.parametrize("field", ["name", "note"])
def test_capability_labels_and_notes_must_be_nonempty(
    services_root, write_manifest, minimal_manifest_dict, field
):
    capability = {
        "name": "Primary cache operations",
        "status": "supported",
        "verification": "tested",
        "note": "Atlas configures the in-stack Redis cache.",
    }
    capability[field] = ""
    manifest = minimal_manifest_dict("redis") | {"capabilities": [capability]}
    write_manifest("redis", manifest)

    with pytest.raises(ManifestLoadError, match=field):
        load_manifests(services_root)


@pytest.mark.parametrize("field", ["name", "note"])
@pytest.mark.parametrize(
    "separator",
    ["\n", "\r", "\v", "\f", "\u0085", "\u2028", "\u2029"],
    ids=["lf", "cr", "vertical-tab", "form-feed", "next-line", "line", "paragraph"],
)
def test_capability_labels_and_notes_reject_line_separators(
    services_root, write_manifest, minimal_manifest_dict, field, separator
):
    capability = {
        "name": "Primary cache operations",
        "status": "supported",
        "verification": "tested",
        "note": "Atlas configures the in-stack Redis cache.",
    }
    capability[field] = f"first line{separator}second line"
    manifest = minimal_manifest_dict("redis") | {"capabilities": [capability]}
    write_manifest("redis", manifest)

    with pytest.raises(ManifestLoadError, match=field):
        load_manifests(services_root)


@pytest.mark.parametrize("status", ["available", "unsupported", "unknown"])
def test_capability_invalid_runtime_status_rejected(
    services_root, write_manifest, minimal_manifest_dict, status
):
    manifest = minimal_manifest_dict("redis") | {
        "capabilities": [
            {
                "name": "Primary cache operations",
                "status": status,
                "verification": "tested",
                "note": "Atlas configures the in-stack Redis cache.",
            }
        ]
    }
    write_manifest("redis", manifest)

    with pytest.raises(ManifestLoadError, match="status"):
        load_manifests(services_root)


@pytest.mark.parametrize("verification", ["verified", "manual", "unknown"])
def test_capability_invalid_verification_rejected(
    services_root, write_manifest, minimal_manifest_dict, verification
):
    manifest = minimal_manifest_dict("redis") | {
        "capabilities": [
            {
                "name": "Primary cache operations",
                "status": "supported",
                "verification": verification,
                "note": "Atlas configures the in-stack Redis cache.",
            }
        ]
    }
    write_manifest("redis", manifest)

    with pytest.raises(ManifestLoadError, match="verification"):
        load_manifests(services_root)


def test_capabilities_block_cannot_be_empty(
    services_root, write_manifest, minimal_manifest_dict
):
    write_manifest(
        "redis", minimal_manifest_dict("redis") | {"capabilities": []}
    )

    with pytest.raises(ManifestLoadError, match="capabilities"):
        load_manifests(services_root)


def test_folder_name_must_match_manifest_name(
    services_root, write_manifest, minimal_manifest_dict
):
    # services/foo/service.yml declares name: bar → rejected.
    bad = minimal_manifest_dict("bar")
    write_manifest("bar", bad, folder_name="foo")
    with pytest.raises(ManifestLoadError) as exc:
        load_manifests(services_root)
    assert "folder" in str(exc.value).lower() or "name" in str(exc.value).lower()


def test_service_dir_missing_manifest_skipped(services_root):
    """A services/<X>/ folder without service.yml is silently skipped
    (it's a doc-only folder, e.g. services/multi2vec-clip/)."""
    (services_root / "redis").mkdir()
    # no service.yml inside
    manifests = load_manifests(services_root)
    assert manifests == []


def test_malformed_yaml_rejected(services_root):
    (services_root / "redis").mkdir()
    (services_root / "redis" / "service.yml").write_text("this is: : not valid: yaml\n  -bad")
    with pytest.raises(ManifestLoadError):
        load_manifests(services_root)


def test_source_default_must_be_one_of_options(
    services_root, write_manifest, full_manifest_dict
):
    bad = full_manifest_dict("ollama")
    bad["sources"]["default"] = "no-such-source"
    write_manifest("ollama", bad)
    with pytest.raises(ManifestLoadError) as exc:
        load_manifests(services_root)
    assert "default" in str(exc.value).lower()


def test_image_container_must_appear_in_containers(
    services_root, write_manifest, full_manifest_dict
):
    bad = full_manifest_dict("ollama")
    bad["images"][0]["container"] = "not-in-containers"
    write_manifest("ollama", bad)
    with pytest.raises(ManifestLoadError) as exc:
        load_manifests(services_root)
    assert "container" in str(exc.value).lower()


def test_rows_block_accepts_valid_entries(tmp_path):
    """The new rows: block accepts the canonical shape."""
    services_root = tmp_path / "services"
    (services_root / "demo").mkdir(parents=True)
    (services_root / "demo" / "service.yml").write_text(
        "name: demo\n"
        "label: Demo\n"
        "category: data\n"
        "env: []\n"
        + _SYNTHETIC_CAPABILITY_YAML
        + "rows:\n"
        "  - display_name: Demo Row\n"
        "    source_var: DEMO_SOURCE\n"
        "    port_var: DEMO_PORT\n"
        "    description: A demo row\n"
        "    alias: demo.localhost\n"
        "    localhost_endpoint_var: DEMO_URL\n"
    )

    from services.manifests import load_manifests
    manifests = load_manifests(services_root)
    assert len(manifests) == 1
    assert len(manifests[0].rows) == 1
    row = manifests[0].rows[0]
    assert row.display_name == "Demo Row"
    assert row.alias == "demo.localhost"


# ────────────────────────────────────────────────────────────────────────────
# Category enum
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cat", ["infra", "data", "llm", "media", "agents", "apps"])
def test_category_enum_accepts_new_values(tmp_path, cat):
    services_root = tmp_path / "services"
    (services_root / "demo").mkdir(parents=True)
    (services_root / "demo" / "service.yml").write_text(
        f"name: demo\nlabel: Demo\ncategory: {cat}\nenv: []\n"
        + _SYNTHETIC_CAPABILITY_YAML
    )
    from services.manifests import load_manifests
    manifests = load_manifests(services_root)
    assert manifests[0].category == cat


@pytest.mark.parametrize("cat", ["ai", "app"])
def test_category_enum_rejects_old_values(tmp_path, cat):
    services_root = tmp_path / "services"
    (services_root / "demo").mkdir(parents=True)
    (services_root / "demo" / "service.yml").write_text(
        f"name: demo\nlabel: Demo\ncategory: {cat}\nenv: []\n"
        + _SYNTHETIC_CAPABILITY_YAML
    )
    from services.manifests import load_manifests
    with pytest.raises(ManifestLoadError, match="category"):
        load_manifests(services_root)


def test_runtime_adaptive_failure_mode_round_trips(tmp_path):
    """A manifest declaring runtime_adaptive.<container>.failure_mode must
    parse without rejection and the value must be retrievable from the
    Manifest's runtime_adaptive dict."""
    from services.manifests import load_manifests

    services_dir = tmp_path / "services"
    svc = services_dir / "foo"
    svc.mkdir(parents=True)
    (svc / "service.yml").write_text(
        """
name: foo
label: Foo
category: data
env: []
capabilities:
  - name: Synthetic service contract
    status: supported
    verification: tested
    note: Tests exercise this synthetic manifest contract.
runtime_adaptive:
  foo:
    adapts_to: [other]
    failure_mode: "Foo skips its lookup; warning logged."
""".strip()
    )

    manifests = load_manifests(services_dir)
    assert len(manifests) == 1
    assert manifests[0].runtime_adaptive["foo"]["failure_mode"] == \
        "Foo skips its lookup; warning logged."


def test_doc_extras_extra_consumers_round_trips(tmp_path):
    """A manifest with doc_extras.diagram.extra_consumers must load and
    expose the list via Manifest.doc_extras."""
    from services.manifests import load_manifests

    services_dir = tmp_path / "services"
    svc = services_dir / "bar"
    svc.mkdir(parents=True)
    (svc / "service.yml").write_text(
        """
name: bar
label: Bar
category: infra
env: []
capabilities:
  - name: Synthetic service contract
    status: supported
    verification: tested
    note: Tests exercise this synthetic manifest contract.
doc_extras:
  diagram:
    extra_consumers: ["openclaw", "n8n"]
""".strip()
    )

    manifests = load_manifests(services_dir)
    assert manifests[0].doc_extras == {
        "diagram": {"extra_consumers": ["openclaw", "n8n"]}
    }


def test_data_flow_calls_round_trips(tmp_path):
    """A manifest declaring data_flow.calls must parse and the values must be retrievable."""
    from services.manifests import load_manifests

    services_dir = tmp_path / "services"
    svc = services_dir / "foo"
    svc.mkdir(parents=True)
    (svc / "service.yml").write_text(
        "name: foo\n"
        "label: Foo\n"
        "category: data\n"
        "env: []\n"
        + _SYNTHETIC_CAPABILITY_YAML
        + "data_flow:\n"
        "  calls:\n"
        "    - bar\n"
        "    - baz\n"
    )

    manifests = load_manifests(services_dir)
    assert len(manifests) == 1
    assert manifests[0].data_flow == {"calls": ["bar", "baz"]}


def test_data_flow_calls_optional(tmp_path):
    """A manifest without data_flow loads cleanly with empty dict."""
    from services.manifests import load_manifests

    services_dir = tmp_path / "services"
    svc = services_dir / "noflow"
    svc.mkdir(parents=True)
    (svc / "service.yml").write_text(
        "name: noflow\n"
        "label: NoFlow\n"
        "category: data\n"
        "env: []\n"
        + _SYNTHETIC_CAPABILITY_YAML
    )

    manifests = load_manifests(services_dir)
    assert manifests[0].data_flow == {}


def test_data_flow_calls_rejects_unknown_subkey(tmp_path):
    """Unknown subkeys under data_flow (e.g. data_flow.bogus) are rejected by schema."""
    from services.manifests import load_manifests

    services_dir = tmp_path / "services"
    svc = services_dir / "bad"
    svc.mkdir(parents=True)
    (svc / "service.yml").write_text(
        "name: bad\n"
        "label: Bad\n"
        "category: data\n"
        "env: []\n"
        + _SYNTHETIC_CAPABILITY_YAML
        + "data_flow:\n"
        "  bogus: [a, b]\n"
    )

    with pytest.raises(ManifestLoadError, match="bogus") as exc:
        load_manifests(services_dir)
    assert "capabilities" not in str(exc.value)


def test_row_carries_localhost_port_var_through_topology(tmp_path):
    """Newly-added field on manifest Row → topology Row, surfaced
    intact so state_builder.resolve_port can read it without going
    back through the YAML."""
    from services.topology import build_topology

    # Synthetic minimal manifest with the new field.
    services_root = tmp_path / "services"
    manifest_yml = services_root / "minimal" / "service.yml"
    manifest_yml.parent.mkdir(parents=True)
    manifest_yml.write_text(
        "name: minimal\n"
        "label: Minimal\n"
        "category: apps\n"
        "containers: [minimal]\n"
        + _SYNTHETIC_CAPABILITY_YAML
        + "sources:\n"
        "  var: MINIMAL_SOURCE\n"
        "  default: container\n"
        "  options:\n"
        "    - id: container\n"
        "      label: Container\n"
        "    - id: localhost\n"
        "      label: Localhost\n"
        "env:\n"
        "  - name: MINIMAL_PORT\n"
        "rows:\n"
        "  - display_name: Minimal\n"
        "    source_var: MINIMAL_SOURCE\n"
        "    port_var: MINIMAL_PORT\n"
        "    localhost_endpoint_var: MINIMAL_ENDPOINT\n"
        "    localhost_port_var: MINIMAL_LOCALHOST_PORT\n"
    )
    topology = build_topology(services_root)
    matching = [r for r in topology.rows if r.display_name == "Minimal"]
    assert len(matching) == 1
    row = matching[0]
    assert row.localhost_port_var == "MINIMAL_LOCALHOST_PORT", (
        f"localhost_port_var did not survive manifest -> Row round-trip; "
        f"got {row.localhost_port_var!r}"
    )


def test_spark_manifest_loads():
    from services.manifests import load_manifests
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests = load_manifests(repo_root / "services")
    spark = next((m for m in manifests if m.name == "spark"), None)
    assert spark is not None, "spark manifest not found"
    assert spark.category == "data"
    assert "spark-master" in {c for c in spark.containers}
    assert "spark-worker" in {c for c in spark.containers}
    assert "spark-history" in {c for c in spark.containers}
    assert "minio" in spark.depends_on.required
    assert spark.sources.default == "disabled"


def test_zeppelin_manifest_loads():
    from services.manifests import load_manifests
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests = load_manifests(repo_root / "services")
    z = next((m for m in manifests if m.name == "zeppelin"), None)
    assert z is not None
    assert z.category == "apps"
    assert "spark" in z.depends_on.required, "Zeppelin must hard-require Spark per D3"
    assert z.sources.default == "disabled"


def test_airflow_manifest_loads():
    from services.manifests import load_manifests
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests = load_manifests(repo_root / "services")
    a = next((m for m in manifests if m.name == "airflow"), None)
    assert a is not None
    assert a.category == "agents"
    # supabase / litellm / redis are unconditional consumers — airflow-init
    # seeds postgres_supabase / litellm_default / redis_default Connections
    # without gating. They belong in required: per ordering convention
    # mirrored by sibling consumers (n8n, jupyterhub).
    assert "supabase" in a.depends_on.required
    assert "litellm" in a.depends_on.required
    assert "redis" in a.depends_on.required
    # spark / weaviate / neo4j Connection seeding is gated on the sibling's
    # source being container — optional dependency.
    assert "spark" in a.depends_on.optional
    assert "weaviate" in a.depends_on.optional
    assert "neo4j" in a.depends_on.optional


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        (
            "blender-mcp",
            [
                (
                    "Host-only Blender MCP bridge",
                    "supported",
                    "documented",
                    "Atlas configures user-run GUI and managed headless host sources; it does not run Blender in a container.",
                ),
                (
                    "Managed headless scene control",
                    "partial",
                    "tested",
                    "Scene inspection and generated-code commands work through the managed bridge, but GUI-dependent operations are unavailable.",
                ),
                (
                    "Managed-bridge loopback guard for arbitrary Python execution",
                    "partial",
                    "tested",
                    "Atlas refuses non-loopback binds for managed-localhost without an explicit override; the user-run localhost GUI source remains operator-controlled.",
                ),
                (
                    "Viewport screenshots in managed headless mode",
                    "not-supported",
                    "documented",
                    "Headless Blender has no VIEW_3D context, so use the user-run GUI source when viewport screenshots are required.",
                ),
            ],
        ),
        (
            "speaches",
            [
                (
                    "OpenAI-compatible text-to-speech",
                    "partial",
                    "tested",
                    "Speaches serves /v1/audio/speech, but Atlas does not preload Kokoro; the model must be downloaded before requests succeed.",
                ),
                (
                    "OpenAI-compatible speech-to-text",
                    "partial",
                    "untested",
                    "Speaches exposes /v1/audio/transcriptions, but Atlas has not validated the current preload and Open WebUI model path against a live container.",
                ),
                (
                    "Configurable STT model selection",
                    "stubbed",
                    "documented",
                    "SPEACHES_STT_MODEL is declared but does not alter the hard-coded PRELOAD_MODELS value or Open WebUI's STT model.",
                ),
            ],
        ),
        (
            "comfyui",
            [
                (
                    "Container and managed-MPS image generation",
                    "supported",
                    "tested",
                    "Atlas configures CPU and NVIDIA containers plus an Apple-Silicon Metal host process behind the same endpoint contract.",
                ),
                (
                    "Workflow and model provisioning",
                    "partial",
                    "tested",
                    "Atlas stages selected catalog models and pinned custom nodes, but arbitrary workflow dependencies and readiness remain operator-managed.",
                ),
                    (
                        "Supabase output upload",
                        "stubbed",
                        "documented",
                        "The upload flag and bucket variables are placeholders with no stock image, provisioning, or backend consumer.",
                    ),
                    (
                        "Authenticated ComfyUI ingress",
                        "not-supported",
                        "documented",
                        "The published container UI/API and CORS-only comfyui.localhost route run without Atlas authentication; keep HOST_BIND_IP=127.0.0.1:, remove the publish, or add an authentication proxy before remote exposure.",
                    ),
                ],
            ),
        (
            "lightrag",
            [
                (
                    "Graph-augmented retrieval through LiteLLM",
                    "supported",
                    "tested",
                    "Atlas resolves LightRAG chat and embedding models through LiteLLM and exposes graph-aware query modes.",
                ),
                (
                    "External persistent storage",
                    "partial",
                    "tested",
                    "Atlas wires Supabase pgvector, Neo4j, and Redis when enabled; disabling them clears connection URIs without selecting file-backed storage implementations.",
                ),
                (
                    "LightRAG reranking",
                    "partial",
                    "tested",
                    "Reranking requires TEI plus the opt-in backend adapter because direct LightRAG-to-TEI request payloads are incompatible.",
                ),
            ],
        ),
    ],
)
def test_pilot_manifest_capability_contracts(service, expected):
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    manifest = next(
        m for m in load_manifests(repo_root / "services") if m.name == service
    )

    assert [
        (cap.name, cap.status, cap.verification, cap.note)
        for cap in manifest.capabilities
    ] == expected


def test_infra_and_data_manifests_meet_structural_capability_quality_floor():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests = [
        manifest
        for manifest in load_manifests(repo_root / "services")
        if manifest.category in {"infra", "data"}
    ]
    _assert_category_capability_quality(
        manifests,
        _TASK3_REVIEWED_INFRA_DATA_MANIFESTS,
        21,
        "infra/data capability quality floor",
    )


def test_llm_and_media_manifests_meet_structural_capability_quality_floor():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests = [
        manifest
        for manifest in load_manifests(repo_root / "services")
        if manifest.category in {"llm", "media"}
    ]
    _assert_category_capability_quality(
        manifests,
        _TASK4_REVIEWED_LLM_MEDIA_MANIFESTS,
        19,
        "LLM/media capability quality floor",
    )


def test_all_manifests_have_capabilities_and_agents_apps_meet_quality_floor():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests = load_manifests(repo_root / "services")
    agents_apps = [
        manifest
        for manifest in manifests
        if manifest.category in {"agents", "apps"}
    ]
    reviewed_manifests = (
        _TASK3_REVIEWED_INFRA_DATA_MANIFESTS
        | _TASK4_REVIEWED_LLM_MEDIA_MANIFESTS
        | _TASK5_REVIEWED_AGENTS_APPS_MANIFESTS
    )
    missing = sorted(
        manifest.name for manifest in manifests if not manifest.capabilities
    )
    assert (len(reviewed_manifests), sorted(reviewed_manifests - {m.name for m in manifests}), missing) == (
        57,
        [],
        [],
    )
    _assert_category_capability_quality(
        agents_apps,
        _TASK5_REVIEWED_AGENTS_APPS_MANIFESTS,
        17,
        "agents/apps capability quality floor",
    )


def test_repository_has_exactly_57_nonempty_unique_capability_contracts():
    repo_root = Path(__file__).resolve().parent.parent.parent
    manifests = load_manifests(repo_root / "services")

    duplicate_names = {
        manifest.name: duplicates
        for manifest in manifests
        if (duplicates := _duplicate_capability_names(manifest))
    }
    assert (
        len(manifests),
        len({manifest.name for manifest in manifests}),
        sorted(m.name for m in manifests if not m.capabilities),
        duplicate_names,
    ) == (57, 57, [], {})


def test_comfyui_ingress_contract_matches_compose_and_kong_boundaries():
    from pathlib import Path

    import yaml

    from utils.kong_config_generator import KongConfigGenerator

    repo_root = Path(__file__).resolve().parent.parent.parent
    compose = yaml.safe_load(
        (repo_root / "services/comfyui/compose.yml").read_text(encoding="utf-8")
    )
    comfyui = compose["services"]["comfyui"]
    generator = KongConfigGenerator(config_parser=None)
    generator.env_vars = {"COMFYUI_SOURCE": "container-cpu"}
    kong_service = generator.generate_comfyui_service()
    assert kong_service is not None

    manifest = next(
        manifest
        for manifest in load_manifests(repo_root / "services")
        if manifest.name == "comfyui"
    )
    capability = _capability_named(manifest, "Authenticated ComfyUI ingress")
    assert (
        comfyui["ports"],
        "WEB_ENABLE_AUTH=false" in comfyui["environment"],
        kong_service["routes"][0]["hosts"],
        kong_service["plugins"],
        (capability.status, capability.verification),
    ) == (
        ["${HOST_BIND_IP:-}${COMFYUI_PORT}:18188"],
        True,
        ["comfyui.localhost"],
        [{"name": "cors"}],
        ("not-supported", "documented"),
    )
    _assert_text_contract(capability.note, contains=(
        "published container UI/API",
        "CORS-only comfyui.localhost route",
        "without Atlas authentication",
        "HOST_BIND_IP=127.0.0.1:",
        "remove the publish",
        "authentication proxy",
    ))


@pytest.mark.parametrize(
    (
        "service",
        "capability_name",
        "expected_status",
        "expected_verification",
        "required_fragments",
        "forbidden_fragments",
    ),
    [
        (
            "chatterbox",
            "GPU voice-cloning text-to-speech",
            "supported",
            "documented",
            ("digest-pinned NVIDIA container", "synthesis and voice cloning"),
            ("no live model-inference certification",),
        ),
        (
            "cloud-providers",
            "Live cloud completion validation",
            "not-supported",
            "untested",
            (
                "statically tests selection, key isolation, and rendered routing",
                "performs no live credential, entitlement, model-availability, or provider certification",
            ),
            (),
        ),
        (
            "fal",
            "LiteLLM text-to-image passthrough",
            "partial",
            "tested",
            (
                "OpenAI-shaped b64/URL image result",
                "without Atlas storage or provenance normalization",
            ),
            ("provider-native outputs",),
        ),
        (
            "docling-lightrag-adapter",
            "Docling credential isolation",
            "supported",
            "tested",
            (
                "LightRAG receives only the internal adapter URL",
                "on that isolated boundary, the adapter alone receives the Docling bearer token",
                "no host-published adapter port",
            ),
            ("Only the adapter receives",),
        ),
        (
            "tei-reranker",
            "Arbitrary reranker model portability",
            "partial",
            "documented",
            (
                "TEI_RERANKER_REVISION defaults to mutable main",
                "pin a model commit for reproducible artifacts",
                "future main contents",
                "both backends",
                "memory limits",
            ),
            (),
        ),
    ],
)
def test_task4_reviewed_rows_keep_status_and_verification_orthogonal(
    service,
    capability_name,
    expected_status,
    expected_verification,
    required_fragments,
    forbidden_fragments,
):
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    manifest = next(
        manifest
        for manifest in load_manifests(repo_root / "services")
        if manifest.name == service
    )
    capability = next(
        capability
        for capability in manifest.capabilities
        if capability.name == capability_name
    )

    assert (capability.status, capability.verification) == (
        expected_status,
        expected_verification,
    )
    for fragment in required_fragments:
        assert fragment in capability.note
    for fragment in forbidden_fragments:
        assert fragment not in capability.note


@pytest.mark.parametrize(
    ("service", "capability_name"),
    [
        ("backup", "On-demand Postgres and volume backup export"),
        ("loki", "Automatic Atlas application log collection"),
        ("otel-collector", "Log export to Loki"),
    ],
)
def test_reviewed_capability_verification_matches_direct_coverage(
    service, capability_name
):
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    manifest = next(
        m for m in load_manifests(repo_root / "services") if m.name == service
    )
    capability = next(cap for cap in manifest.capabilities if cap.name == capability_name)

    assert capability.verification == "documented"


def test_supabase_app_role_capability_names_manifest_owned_variables():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    manifest = next(
        m for m in load_manifests(repo_root / "services") if m.name == "supabase"
    )
    capability = next(
        cap
        for cap in manifest.capabilities
        if cap.name == "Least-privilege application database role"
    )

    assert "SUPABASE_DB_APP_USER" in capability.note
    assert "SUPABASE_DB_APP_PASSWORD" in capability.note
    assert "SUPABASE_APP_USER" not in capability.note
    assert "SUPABASE_APP_PASSWORD" not in capability.note


def test_supabase_pg_meta_contract_matches_compose_and_kong_boundaries():
    from pathlib import Path

    import yaml

    from core.config_parser import ConfigParser
    from utils.kong_config_generator import KongConfigGenerator

    repo_root = Path(__file__).resolve().parent.parent.parent
    compose = yaml.safe_load(
        (repo_root / "services/supabase/compose.yml").read_text(encoding="utf-8")
    )
    meta = compose["services"]["supabase-meta"]
    kong_services = {
        service["name"]: service
        for service in KongConfigGenerator(ConfigParser(str(repo_root))).get_supabase_services()
    }
    kong_meta = kong_services["meta"]
    acl = next(plugin for plugin in kong_meta["plugins"] if plugin["name"] == "acl")

    manifest = next(
        m for m in load_manifests(repo_root / "services") if m.name == "supabase"
    )
    capability = _capability_named(manifest, "pg-meta administrative access control")
    assert {
        "ports": meta["ports"],
        "db_user": meta["environment"]["PG_META_DB_USER"],
        "dashboard_env": {
            name for name in meta["environment"] if name.startswith("DASHBOARD_")
        },
        "route_paths": kong_meta["routes"][0]["paths"],
        "required_plugins": {"basic-auth", "acl"}
        <= {plugin["name"] for plugin in kong_meta["plugins"]},
        "acl": acl["config"]["allow"],
        "capability": (capability.status, capability.verification),
    } == {
        "ports": ["${HOST_BIND_IP:-}${SUPABASE_META_PORT}:8080"],
        "db_user": "${SUPABASE_DB_USER}",
        "dashboard_env": set(),
        "route_paths": ["/pg/"],
        "required_plugins": True,
        "acl": ["dashboard_user"],
        "capability": ("partial", "documented"),
    }
    _assert_text_contract(capability.note, contains=(
        "Kong /pg/ route uses Basic authentication and the dashboard_user ACL",
        "direct host-published SUPABASE_META_PORT",
        "no application authentication",
        "supabase_admin",
    ))


def test_supabase_studio_contract_matches_compose_and_kong_boundaries():
    from pathlib import Path

    import yaml

    from core.config_parser import ConfigParser
    from utils.kong_config_generator import KongConfigGenerator

    repo_root = Path(__file__).resolve().parent.parent.parent
    compose = yaml.safe_load(
        (repo_root / "services/supabase/compose.yml").read_text(encoding="utf-8")
    )
    studio = compose["services"]["supabase-studio"]
    kong_services = {
        service["name"]: service
        for service in KongConfigGenerator(ConfigParser(str(repo_root))).get_supabase_services()
    }
    dashboard = kong_services["dashboard"]
    acl = next(plugin for plugin in dashboard["plugins"] if plugin["name"] == "acl")

    manifest = next(
        m for m in load_manifests(repo_root / "services") if m.name == "supabase"
    )
    capability = _capability_named(manifest, "Supabase Studio access control")
    assert {
        "ports": studio["ports"],
        "dashboard_env": {
            name for name in studio["environment"] if name.startswith("DASHBOARD_")
        },
        "route_hosts": dashboard["routes"][0]["hosts"],
        "required_plugins": {"basic-auth", "acl"}
        <= {plugin["name"] for plugin in dashboard["plugins"]},
        "acl": acl["config"]["allow"],
        "capability": (capability.status, capability.verification),
    } == {
        "ports": ["${HOST_BIND_IP:-}${SUPABASE_STUDIO_PORT}:3000"],
        "dashboard_env": set(),
        "route_hosts": ["supabase-studio.localhost"],
        "required_plugins": True,
        "acl": ["dashboard_user"],
        "capability": ("partial", "documented"),
    }
    _assert_text_contract(capability.note, contains=(
        "Kong route uses Basic authentication and the dashboard_user ACL",
        "host-published SUPABASE_STUDIO_PORT bypasses that gate",
        "Studio has no application authentication",
        "HOST_BIND_IP=127.0.0.1:",
        "firewall SUPABASE_STUDIO_PORT",
        "remove its ports: publish",
    ))


def test_supabase_postgres_host_auth_contract_matches_compose():
    from pathlib import Path

    import yaml

    repo_root = Path(__file__).resolve().parent.parent.parent
    compose = yaml.safe_load(
        (repo_root / "services/supabase/compose.yml").read_text(encoding="utf-8")
    )
    database = compose["services"]["supabase-db"]
    manifests = load_manifests(repo_root / "services")
    globals_manifest = next(m for m in manifests if m.name == "globals")
    host_bind_ip = next(env for env in globals_manifest.env if env.name == "HOST_BIND_IP")
    supabase = next(m for m in manifests if m.name == "supabase")
    capability = _capability_named(supabase, "Authenticated remote PostgreSQL access")
    assert (
        database["ports"],
        database["environment"]["POSTGRES_HOST_AUTH_METHOD"],
        host_bind_ip.default,
        (capability.status, capability.verification),
    ) == (
        ["${HOST_BIND_IP:-}${SUPABASE_DB_PORT}:5432"],
        "trust",
        "",
        ("not-supported", "documented"),
    )
    _assert_text_contract(capability.note, contains=(
        "host-published SUPABASE_DB_PORT",
        "POSTGRES_HOST_AUTH_METHOD=trust",
        "HOST_BIND_IP=127.0.0.1:",
        "firewall SUPABASE_DB_PORT",
        "remove the supabase-db ports: publish",
        "authenticated database policy before remote access",
    ))


def test_iceberg_rest_access_contract_matches_host_publish():
    from pathlib import Path

    import yaml

    repo_root = Path(__file__).resolve().parent.parent.parent
    compose = yaml.safe_load(
        (repo_root / "services/iceberg-rest/compose.yml").read_text(encoding="utf-8")
    )
    catalog = compose["services"]["iceberg-rest"]
    assert catalog["ports"] == ["${HOST_BIND_IP:-}${ICEBERG_REST_PORT}:8181"]
    assert not any(
        "AUTH" in name or "TOKEN" in name for name in catalog["environment"]
    )

    manifest = next(
        m for m in load_manifests(repo_root / "services") if m.name == "iceberg-rest"
    )
    capability = next(
        (
            cap
            for cap in manifest.capabilities
            if cap.name == "Authenticated Iceberg REST API access"
        ),
        None,
    )
    assert capability is not None
    assert (capability.status, capability.verification) == (
        "not-supported",
        "documented",
    )
    _assert_text_contract(capability.note, contains=(
        "Compose-network API and host-published ICEBERG_REST_PORT have no Atlas authentication",
        "HOST_BIND_IP=127.0.0.1:",
        "remove the iceberg-rest ports: publish",
    ))


def test_redpanda_access_contract_matches_compose_and_kong_boundaries():
    from pathlib import Path

    import yaml

    from core.config_parser import ConfigParser
    from utils.kong_config_generator import KongConfigGenerator

    repo_root = Path(__file__).resolve().parent.parent.parent
    compose = yaml.safe_load(
        (repo_root / "services/redpanda/compose.yml").read_text(encoding="utf-8")
    )
    broker = compose["services"]["redpanda"]
    console = compose["services"]["redpanda-console"]
    generator = KongConfigGenerator(ConfigParser(str(repo_root)))
    generator.env_vars = {"REDPANDA_SOURCE": "container"}
    kong_console = generator.generate_redpanda_service()
    assert kong_console is not None
    acl = next(plugin for plugin in kong_console["plugins"] if plugin["name"] == "acl")

    manifest = next(
        m for m in load_manifests(repo_root / "services") if m.name == "redpanda"
    )
    capability = _capability_named(manifest, "Broker and Console access control")
    assert {
        "broker_ports": broker["ports"],
        "console_ports": console["ports"],
        "external_listener": any(
            "external://0.0.0.0:19092" in argument for argument in broker["command"]
        ),
        "secure_listener_flags": tuple(
            argument
            for argument in broker["command"]
            if "sasl" in argument.casefold() or "tls" in argument.casefold()
        ),
        "console_auth_env": tuple(
            name for name in console["environment"] if "AUTH" in name
        ),
        "route_hosts": kong_console["routes"][0]["hosts"],
        "required_plugins": {"basic-auth", "acl"}
        <= {plugin["name"] for plugin in kong_console["plugins"]},
        "acl": acl["config"]["allow"],
        "capability": (capability.status, capability.verification),
    } == {
        "broker_ports": ["${HOST_BIND_IP:-}${REDPANDA_KAFKA_PORT}:19092"],
        "console_ports": ["${HOST_BIND_IP:-}${REDPANDA_CONSOLE_PORT}:8080"],
        "external_listener": True,
        "secure_listener_flags": (),
        "console_auth_env": (),
        "route_hosts": ["redpanda.localhost"],
        "required_plugins": True,
        "acl": ["dashboard_user"],
        "capability": ("partial", "documented"),
    }
    _assert_text_contract(capability.note, contains=(
        "Kong Console route uses Basic authentication and the dashboard_user ACL",
        "direct Console and Kafka listener are ungated",
        "HOST_BIND_IP=127.0.0.1:",
    ))


def test_backup_restore_contract_matches_non_atomic_orchestration():
    from pathlib import Path
    import shlex

    repo_root = Path(__file__).resolve().parent.parent.parent
    restore_script = (
        repo_root / "services/backup/init/scripts/restore-postgres.sh"
    ).read_text(encoding="utf-8")
    restore_tokens = next(
        tokens
        for line in restore_script.splitlines()
        if not line.lstrip().startswith("#")
        for tokens in (shlex.split(line),)
        if "pg_restore" in tokens
    )

    def uses_option(long_name: str, short_name: str) -> bool:
        return any(
            token == long_name
            or token == short_name
            or (
                token.startswith("-")
                and not token.startswith("--")
                and short_name.removeprefix("-") in token.removeprefix("-")
            )
            for token in restore_tokens
        )

    manifest = next(
        m for m in load_manifests(repo_root / "services") if m.name == "backup"
    )
    capability = next(
        cap for cap in manifest.capabilities if cap.name == "Postgres restore workflow"
    )
    assert (
        "pg_restore" in restore_tokens,
        uses_option("--list", "-l"),
        uses_option("--single-transaction", "-1"),
        (capability.status, capability.verification),
    ) == (True, False, False, ("partial", "tested"))
    _assert_text_contract(capability.note, contains=(
        "orchestrates S3 retrieval and pg_restore",
        "volume archives have no restore workflow",
        "no preflight validation or atomicity guarantee",
    ), excludes=("validates and restores",))


def test_ray_worker_count_capability_does_not_claim_a_global_upper_bound():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    manifest = next(
        m for m in load_manifests(repo_root / "services") if m.name == "ray"
    )
    capability = next(
        cap
        for cap in manifest.capabilities
        if cap.name == "Containerized CPU distributed compute"
    )

    assert "bounded" not in capability.note
    assert "operator-selected worker count" in capability.note


def test_prometheus_access_contract_matches_compose_and_kong_surfaces():
    from pathlib import Path

    import yaml

    from core.config_parser import ConfigParser
    from utils.kong_config_generator import KongConfigGenerator

    repo_root = Path(__file__).resolve().parent.parent.parent
    compose = yaml.safe_load(
        (repo_root / "services/prometheus/compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    manifests = load_manifests(repo_root / "services")
    globals_manifest = next(m for m in manifests if m.name == "globals")
    host_bind_ip = next(env for env in globals_manifest.env if env.name == "HOST_BIND_IP")
    generator = KongConfigGenerator(ConfigParser(str(repo_root)))
    generator.env_vars = {"PROMETHEUS_SOURCE": "container"}
    kong_prometheus = generator.generate_prometheus_service()
    assert kong_prometheus is not None

    prometheus = next(m for m in manifests if m.name == "prometheus")
    capability = _capability_named(
        prometheus, "Authenticated Prometheus and exporter access"
    )
    assert {
        "prometheus_ports": services["prometheus"]["ports"],
        "node_exporter_ports": services["node-exporter"]["ports"],
        "cadvisor_ports": services["cadvisor"]["ports"],
        "lifecycle_enabled": "--web.enable-lifecycle"
        in services["prometheus"]["command"],
        "host_bind_ip": host_bind_ip.default,
        "route_hosts": kong_prometheus["routes"][0]["hosts"],
        "plugins": {plugin["name"] for plugin in kong_prometheus["plugins"]},
        "capability": (capability.status, capability.verification),
    } == {
        "prometheus_ports": ["${HOST_BIND_IP:-}${PROMETHEUS_PORT}:9090"],
        "node_exporter_ports": ["${HOST_BIND_IP:-}${NODE_EXPORTER_PORT}:9100"],
        "cadvisor_ports": ["${HOST_BIND_IP:-}${CADVISOR_PORT}:8080"],
        "lifecycle_enabled": True,
        "host_bind_ip": "",
        "route_hosts": ["prometheus.localhost"],
        "plugins": {"cors"},
        "capability": ("not-supported", "tested"),
    }
    _assert_text_contract(capability.note, contains=(
        "direct PROMETHEUS_PORT, NODE_EXPORTER_PORT, and CADVISOR_PORT publishes have no authentication",
        "CORS-only Kong prometheus.localhost route has no authentication",
        "--web.enable-lifecycle is enabled",
        "default empty HOST_BIND_IP binds direct ports on all interfaces",
        "HOST_BIND_IP=127.0.0.1:",
        "firewall or remove the direct ports",
        "authentication proxy or remove the Prometheus Kong route",
    ))


def test_spark_web_access_contract_matches_compose_and_kong_surfaces():
    from pathlib import Path

    import yaml

    from core.config_parser import ConfigParser
    from utils.kong_config_generator import KongConfigGenerator

    repo_root = Path(__file__).resolve().parent.parent.parent
    compose = yaml.safe_load(
        (repo_root / "services/spark/compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    manifests = load_manifests(repo_root / "services")
    globals_manifest = next(m for m in manifests if m.name == "globals")
    host_bind_ip = next(env for env in globals_manifest.env if env.name == "HOST_BIND_IP")
    generator = KongConfigGenerator(ConfigParser(str(repo_root)))
    generator.env_vars = {"SPARK_SOURCE": "container"}
    kong_master = generator.generate_spark_master_service()
    kong_history = generator.generate_spark_history_service()
    assert kong_master is not None
    assert kong_history is not None

    spark = next(m for m in manifests if m.name == "spark")
    capability = _capability_named(spark, "Authenticated Spark web access")
    assert {
        "master_ports": services["spark-master"]["ports"],
        "history_ports": services["spark-history"]["ports"],
        "host_bind_ip": host_bind_ip.default,
        "master_hosts": kong_master["routes"][0]["hosts"],
        "history_hosts": kong_history["routes"][0]["hosts"],
        "master_plugins": {plugin["name"] for plugin in kong_master["plugins"]},
        "history_plugins": {plugin["name"] for plugin in kong_history["plugins"]},
        "capability": (capability.status, capability.verification),
    } == {
        "master_ports": ["${HOST_BIND_IP:-}${SPARK_MASTER_UI_PORT}:8080"],
        "history_ports": ["${HOST_BIND_IP:-}${SPARK_HISTORY_PORT}:18080"],
        "host_bind_ip": "",
        "master_hosts": ["spark.localhost"],
        "history_hosts": ["spark-history.localhost"],
        "master_plugins": {"cors"},
        "history_plugins": {"cors"},
        "capability": ("not-supported", "tested"),
    }
    _assert_text_contract(capability.note, contains=(
        "direct SPARK_MASTER_UI_PORT and SPARK_HISTORY_PORT publishes are unauthenticated",
        "CORS-only Kong Spark routes are unauthenticated",
        "default empty HOST_BIND_IP binds direct ports on all interfaces",
        "HOST_BIND_IP=127.0.0.1:",
        "firewall or remove the direct ports",
        "authentication proxy or remove both Spark Kong routes",
    ))


def test_lightrag_manifest_header_does_not_claim_automatic_file_fallbacks():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    manifest_path = repo_root / "services" / "lightrag" / "service.yml"
    header = "\n".join(manifest_path.read_text(encoding="utf-8").splitlines()[:6])

    assert "fall back to" not in header
    assert "does not switch" in header
    assert "storage selectors" in header


def test_doc_only_folders_are_skipped_by_real_manifest_load():
    """Three folders under services/ ship documentation only (README +
    architecture diagrams) and intentionally have no service.yml:
    stt-provider, doc-processor, multi2vec-clip. The loader's
    `_is_service_dir` predicate gates on `service.yml` existence so
    they're never loaded as manifests; they appear in other services'
    `data_flow.calls` as aggregator names and the regen / topology
    layers handle that aliasing separately. See project memory
    `project_doc_only_service_folders.md`.

    Synthetic-folder tests above (test_load_manifests*) cover the
    predicate in isolation. This test pins the contract against the
    actual repo layout so a future refactor of `_is_service_dir` can't
    accidentally load them without a real-repo failure.
    """
    from services.manifests import load_manifests
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent
    services_root = repo_root / "services"

    # Sanity: the folders exist (they ship README + architecture.svg).
    for name in ("stt-provider", "doc-processor", "multi2vec-clip"):
        assert (services_root / name).is_dir(), (
            f"services/{name}/ should exist as a doc-only folder; "
            f"if you renamed or removed it, update this test "
            f"(and project_doc_only_service_folders.md memory)."
        )

    manifests = load_manifests(services_root)
    loaded_names = {m.name for m in manifests}
    for name in ("stt-provider", "doc-processor", "multi2vec-clip"):
        assert name not in loaded_names, (
            f"services/{name}/ has no service.yml and must not appear "
            f"in load_manifests() output, but it did. Check that "
            f"_is_service_dir still gates on service.yml existence."
        )


# ── pass 17: the typo check must not be disabled by a typo ───────────


def _synthetic_family(tmp_path, runtime_sc_block: str):
    import textwrap

    root = tmp_path / "services"
    (root / "gamma").mkdir(parents=True)
    (root / "gamma" / "service.yml").write_text(
        textwrap.dedent(f"""
            name: gamma
            label: Gamma
            category: apps
            containers: [gamma, gamma-worker]
            capabilities:
              - name: Synthetic service contract
                status: supported
                verification: tested
                note: Tests exercise this synthetic manifest contract.
            sources:
              var: GAMMA_SOURCE
              default: container-cpu
              options:
                - id: container-cpu
                  label: CPU
                - id: container-gpu
                  label: GPU
                - id: disabled
                  label: Disabled
            env:
              - name: GAMMA_SOURCE
                default: container-cpu
            runtime_sc:
            {runtime_sc_block}
        """),
        encoding="utf-8",
    )
    (root / "gamma" / "compose.yml").write_text(
        "services:\n  gamma:\n    image: alpine\n", encoding="utf-8"
    )
    return root


_GOOD = """  gamma-worker:
                container-cpu: {scale: 1}
                container-gpu: {scale: 1}"""

_TYPO = """  gamma-worker:
                container-cpu: {scale: 1}
                containr-gpu: {scale: 1}"""


def test_a_typod_variant_key_does_not_disable_the_coverage_check(tmp_path):
    """`set(d) <= opts` selected slices by SUBSET.

    One typo'd variant key made that test false, so the whole slice dropped out
    of the target list and its genuinely missing variants went unreported — the
    check that exists to catch the typo was disabled BY the typo. Six families
    take this fallback path (airflow, celery, langfuse, llm-graph-builder, ray,
    spark) and the schema constrains variant key names not at all.
    """
    from services.manifest_validator import validate_manifests
    from services.manifests import load_manifests

    issues = validate_manifests(load_manifests(_synthetic_family(tmp_path, _TYPO)))
    kinds = [i.kind for i in issues if "runtime_sc" in i.kind]

    # the typo is named outright...
    assert "runtime_sc_unknown_variant" in kinds
    # ...and coverage is still evaluated rather than silently skipped
    missing = [
        i.message for i in issues if i.kind == "runtime_sc_missing_variant"
    ]
    assert any("'disabled'" in m for m in missing)
    assert any("'container-gpu'" in m for m in missing)


def test_a_clean_slice_reports_only_its_missing_variants(tmp_path):
    from services.manifest_validator import validate_manifests
    from services.manifests import load_manifests

    issues = validate_manifests(load_manifests(_synthetic_family(tmp_path, _GOOD)))
    relevant = [i for i in issues if "runtime_sc" in i.kind]
    assert [i.kind for i in relevant] == ["runtime_sc_missing_variant"]
    assert "'disabled'" in relevant[0].message
