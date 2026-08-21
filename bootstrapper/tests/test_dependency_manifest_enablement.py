"""#503: dependency enablement is derived from manifest source metadata.

The old hand-maintained _SCALE_VAR_MAPPING/_SOURCE_VAR_MAPPING dicts omitted
newer manifest services (trino / redpanda / iceberg-rest), so a disabled
service fell through to "assume enabled" and produced false violations like
"trino requires minio but it's disabled" on a fresh gen-ai-rag track start.
These tests pin the manifest-derived behavior for every acceptance criterion.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_dm(tmp_path: Path, env_body: str):
    """DependencyManager against the REAL repo manifests (so the synthesized
    service_dependencies table loads) with a synthetic .env (the established
    consumer-test pattern of overriding env_file_path)."""
    from core.config_parser import ConfigParser
    from services.dependency_manager import DependencyManager

    (tmp_path / ".env").write_text(env_body)
    cp = ConfigParser(str(REPO_ROOT))
    cp.env_file_path = tmp_path / ".env"
    return DependencyManager(cp)


# ── AC: SOURCE=disabled ⇒ scale 0 (fresh env, blank scale vars) ──────────
def test_disabled_trino_scales_to_zero(tmp_path):
    dm = _make_dm(tmp_path, "TRINO_SOURCE=disabled\nTRINO_SCALE=\n")
    assert dm.get_service_scale("trino") == 0


def test_disabled_redpanda_and_iceberg_rest_scale_to_zero(tmp_path):
    dm = _make_dm(
        tmp_path,
        "REDPANDA_SOURCE=disabled\nICEBERG_REST_SOURCE=disabled\n",
    )
    assert dm.get_service_scale("redpanda") == 0
    assert dm.get_service_scale("iceberg-rest") == 0


def test_every_source_configurable_manifest_service_honors_disabled(tmp_path):
    """AC: '…and every source-configurable manifest service' — walk the real
    manifests so a newly added service can never silently regress (#503)."""
    from services.manifests import load_manifests

    services_dir = Path(__file__).resolve().parents[2] / "services"
    manifests = load_manifests(services_dir)

    checked = 0
    for m in manifests:
        if m.sources is None or "disabled" not in [o.id for o in m.sources.options]:
            continue
        env_body = f"{m.sources.var}=disabled\n"
        dm = _make_dm(tmp_path, env_body)
        assert dm.get_service_scale(m.name) == 0, (
            f"{m.name}: {m.sources.var}=disabled must yield scale 0"
        )
        checked += 1
    # Sanity: the walk actually exercised a meaningful set of services.
    assert checked >= 20, f"only {checked} source-configurable manifests checked"


# ── AC: disabled Trino does NOT validate its deps; enabled Trino fails early ──
def test_disabled_trino_with_disabled_minio_passes(tmp_path):
    dm = _make_dm(
        tmp_path,
        "TRINO_SOURCE=disabled\nREDPANDA_SOURCE=disabled\n"
        "ICEBERG_REST_SOURCE=disabled\nMINIO_SOURCE=disabled\nMINIO_SCALE=0\n",
    )
    assert dm.check_service_dependencies() is True
    assert dm.get_dependency_violations() == []


def test_enabled_trino_with_disabled_minio_fails_early(tmp_path):
    dm = _make_dm(
        tmp_path,
        "TRINO_SOURCE=container\nICEBERG_REST_SOURCE=container\n"
        "MINIO_SOURCE=disabled\nMINIO_SCALE=0\nREDPANDA_SOURCE=disabled\n",
    )
    assert dm.check_service_dependencies() is False
    violations = dm.get_dependency_violations()
    assert any(
        v["service"] == "trino" and v["required_service"] == "minio"
        for v in violations
    )


# ── explicit scale still wins (auto-resolve writes scales, not sources) ──
def test_explicit_zero_scale_wins_over_enabled_source(tmp_path):
    dm = _make_dm(tmp_path, "N8N_SOURCE=container\nN8N_SCALE=0\n")
    assert dm.get_service_scale("n8n") == 0


def test_container_alias_names_resolve_via_manifest(tmp_path):
    """Dependency keys that are container names (not manifest names) resolve:
    openclaw-gateway → OPENCLAW_*, n8n-worker → N8N_WORKER_SCALE,
    neo4j-graph-db → NEO4J_GRAPH_DB_SOURCE."""
    dm = _make_dm(
        tmp_path,
        "OPENCLAW_SOURCE=disabled\nNEO4J_GRAPH_DB_SOURCE=disabled\n"
        "N8N_WORKER_SCALE=3\n",
    )
    assert dm.get_service_scale("openclaw-gateway") == 0
    assert dm.get_service_scale("neo4j-graph-db") == 0
    assert dm.get_service_scale("n8n-worker") == 3


# ── AC: auto-resolution consistent for every manifest-backed service ──────
def test_auto_resolve_zeroes_all_family_scale_vars(tmp_path):
    """Disabling n8n zeroes main + worker + init scales (the old hand-written
    special case, now manifest-generalized)."""
    dm = _make_dm(
        tmp_path,
        "N8N_SOURCE=container\nN8N_SCALE=1\nN8N_WORKER_SCALE=1\n"
        "N8N_INIT_SCALE=1\nWEAVIATE_SOURCE=disabled\nWEAVIATE_SCALE=0\n",
    )
    assert dm.check_service_dependencies() is False
    disabled = dm.auto_resolve_dependency_violations()
    assert "n8n" in disabled
    env_text = (tmp_path / ".env").read_text()
    assert "N8N_SCALE=0" in env_text
    assert "N8N_WORKER_SCALE=0" in env_text
    assert "N8N_INIT_SCALE=0" in env_text


def test_auto_resolve_handles_trino(tmp_path):
    """A newly-manifest-backed service (trino) auto-resolves instead of being
    silently skipped by an incomplete hand map."""
    dm = _make_dm(
        tmp_path,
        "TRINO_SOURCE=container\nTRINO_SCALE=1\n"
        "ICEBERG_REST_SOURCE=disabled\nMINIO_SOURCE=disabled\nMINIO_SCALE=0\n"
        "REDPANDA_SOURCE=disabled\n",
    )
    assert dm.check_service_dependencies() is False
    disabled = dm.auto_resolve_dependency_violations()
    assert "trino" in disabled
    assert "TRINO_SCALE=0" in (tmp_path / ".env").read_text()


# ── pass 15: the compose fragment is the container→scale-var authority ───


def test_a_container_resolves_to_its_own_declared_scale_var(tmp_path):
    """`docling-lightrag-adapter` is scaled by DOCLING_ADAPTER_SCALE.

    The name-derived guess DOCLING_LIGHTRAG_ADAPTER_SCALE does not exist, so
    the lookup silently fell through to the family's primary DOCLING_GPU_SCALE
    and answered for the WRONG container: two of the four combinations below
    were inverted, and `auto_resolve` would have zeroed the whole family.
    """
    dm = _make_dm(tmp_path, "DOCLING_GPU_SCALE=1\nDOCLING_ADAPTER_SCALE=0\n")
    assert dm.get_service_scale("docling-lightrag-adapter") == 0
    assert dm.get_service_scale("docling-gpu") == 1

    dm = _make_dm(tmp_path, "DOCLING_GPU_SCALE=0\nDOCLING_ADAPTER_SCALE=1\n")
    assert dm.get_service_scale("docling-lightrag-adapter") == 1
    assert dm.get_service_scale("docling-gpu") == 0


def test_the_compose_fragment_states_the_mapping():
    """Pin the source of truth itself, not just its consequence."""
    from services.dependency_manager import _compose_replica_vars
    from services.manifests import load_manifests

    manifest = next(
        m for m in load_manifests(REPO_ROOT / "services") if m.name == "docling"
    )
    assert _compose_replica_vars(manifest) == {
        "docling-gpu": "DOCLING_GPU_SCALE",
        "docling-lightrag-adapter": "DOCLING_ADAPTER_SCALE",
    }


# ── pass 15: a malformed scale must not read as ENABLED ──────────────────


import pytest  # noqa: E402 — module-level test helpers precede it by convention


@pytest.mark.parametrize("raw,expected", [
    ("0", 0), ("1", 1), ("3", 3),
    # unambiguously "off" — without these, each fell through to "assume
    # enabled", so a disabled service masqueraded as enabled (the #503 rule)
    ("0.0", 0), ("false", 0), ("off", 0), ("no", 0), ("none", 0), ("disabled", 0),
    # `int()` accepts these; `docker compose --scale` does not, so accepting
    # them made the manager's belief and the launched topology disagree
    ("1_0", None), ("+2", None), ("-1", None),
    ("0x0", None), ("O", None), ("garbage", None), ("", None),
])
def test_only_a_plain_non_negative_integer_is_a_replica_count(raw, expected):
    from services.dependency_manager import _parse_scale

    assert _parse_scale(raw) == expected


def test_a_malformed_scale_falls_through_to_the_source_signal(tmp_path, capsys):
    dm = _make_dm(tmp_path, "TRINO_SOURCE=disabled\nTRINO_SCALE=0.0\n")
    assert dm.get_service_scale("trino") == 0

    dm = _make_dm(tmp_path, "TRINO_SOURCE=disabled\nTRINO_SCALE=notanumber\n")
    assert dm.get_service_scale("trino") == 0
    # ...and it says so, rather than silently guessing
    assert "not a replica count" in capsys.readouterr().out


# ── pass 15: an empty lookup is not an answer ────────────────────────


def test_an_empty_services_dir_does_not_short_circuit_the_fallback(tmp_path):
    """Verbatim the #503 regression this module exists to prevent.

    A `services/` directory that EXISTS but yields zero manifests cached `{}`
    and returned it, so the packaged-tree fallback never ran and every service
    fell through to "assume enabled" — a disabled n8n reported scale 1.
    """
    import services.dependency_manager as dependency_manager
    from core.config_parser import ConfigParser
    from services.dependency_manager import DependencyManager

    (tmp_path / "services").mkdir()
    (tmp_path / ".env").write_text("N8N_SOURCE=disabled\nN8N_SCALE=\n")
    parser = ConfigParser(str(tmp_path))
    parser.env_file_path = tmp_path / ".env"

    dependency_manager._ENABLEMENT_CACHE.clear()
    try:
        dm = DependencyManager(parser)
        assert dm.get_service_scale("n8n") == 0
        assert dm._enablement_lookup(), "fell back to an empty lookup"
    finally:
        dependency_manager._ENABLEMENT_CACHE.clear()


def test_auto_resolve_disables_each_service_once(tmp_path):
    """Violations are per (service, requirement), not per service.

    A service missing two dependencies was resolved twice: `.env` was
    atomically rewritten a second time with identical content, and the caller
    printed "Auto-disabled trino..." twice.
    """
    dm = _make_dm(
        tmp_path,
        "TRINO_SCALE=1\nTRINO_SOURCE=container\n"
        "MINIO_SOURCE=disabled\nICEBERG_REST_SOURCE=disabled\n",
    )
    dm.check_service_dependencies()
    offenders = [v["service"] for v in dm.dependency_violations]
    assert offenders.count("trino") >= 2, "precondition: two violations for one service"

    disabled = dm.auto_resolve_dependency_violations()
    assert disabled.count("trino") == 1, f"resolved more than once: {disabled}"
