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
