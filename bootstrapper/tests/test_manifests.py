"""Tests for bootstrapper.services.manifests (loader)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from services.manifests import (
    Manifest,
    ManifestLoadError,
    load_manifests,
)


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
    assert m.capabilities == []  # optional during the pilot rollout


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
@pytest.mark.parametrize("value", ["", "first line\nsecond line", "first line\rsecond line"])
def test_capability_labels_and_notes_must_be_nonempty_single_lines(
    services_root, write_manifest, minimal_manifest_dict, field, value
):
    capability = {
        "name": "Primary cache operations",
        "status": "supported",
        "verification": "tested",
        "note": "Atlas configures the in-stack Redis cache.",
    }
    capability[field] = value
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
        "rows:\n"
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
        "data_flow:\n"
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
        "data_flow:\n"
        "  bogus: [a, b]\n"
    )

    with pytest.raises(ManifestLoadError):
        load_manifests(services_dir)


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
        "sources:\n"
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
                ("Host-only Blender MCP bridge", "supported", "documented"),
                ("Managed headless scene control", "partial", "tested"),
                ("Loopback-guarded arbitrary Python execution", "partial", "tested"),
                ("Viewport screenshots in managed headless mode", "not-supported", "documented"),
            ],
        ),
        (
            "speaches",
            [
                ("OpenAI-compatible text-to-speech", "partial", "tested"),
                ("OpenAI-compatible speech-to-text", "partial", "untested"),
                ("Configurable STT model selection", "stubbed", "documented"),
            ],
        ),
        (
            "comfyui",
            [
                ("Container and managed-MPS image generation", "supported", "tested"),
                ("Workflow and model provisioning", "partial", "tested"),
                ("Supabase output upload", "stubbed", "documented"),
            ],
        ),
        (
            "lightrag",
            [
                ("Graph-augmented retrieval through LiteLLM", "supported", "tested"),
                ("External persistent storage", "partial", "tested"),
                ("LightRAG reranking", "partial", "tested"),
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
        (cap.name, cap.status, cap.verification) for cap in manifest.capabilities
    ] == expected
    assert all(cap.note for cap in manifest.capabilities)


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
