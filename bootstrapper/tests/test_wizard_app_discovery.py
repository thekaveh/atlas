"""
Regression tests for ServiceDiscovery — the wizard's source of truth for
which services need a user answer.

Locks down the post-merge audit finding C3: Open WebUI, JupyterHub, and
Local Deep Researcher were rendered in the box as CFG (configurable) but
were skipped by the wizard because:

  * ``open_web_ui_source`` and ``local_deep_researcher_source`` were
    missing from ``SourceOverrideManager.source_mapping`` (so they had
    no CLI flag and the wizard filtered them out).
  * ``jupyterhub`` only had ``runtime_adaptive`` (not ``runtime_sc``),
    so ``_has_source_options`` returned False and the service never
    reached the CLI-flag check.

The tests below pin the discovery contract so any future regression
(removed manifest slice, dropped source_mapping entry) fails the build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config_parser import ConfigParser
from wizard.service_discovery import ServiceDiscovery


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# Services the wizard MUST present to the user (configurable, unlocked,
# have CLI flags). Keyed by ServiceInfo.display_name so the test fails
# loudly if a Topology rename drops one.
EXPECTED_DISCOVERED = frozenset({
    "LLM Engine",
    "ComfyUI",
    "Document Processor",
    "MinIO Console",
    "n8n",
    "Neo4j Graph DB",
    "OpenClaw",
    "STT Provider",
    "SearxNG",
    "TTS Provider",
    "Weaviate",
    "Multi2Vec CLIP",
    "Hermes Agent",
    # The three that audit C3 unblocked:
    "Open WebUI",
    "JupyterHub",
    "Local Deep Researcher",
    # Ray (added 2026-05-24) — distributed-compute substrate. Wizard
    # discovery requires `ray_source` in SourceOverrideManager.source_mapping.
    "Ray",
    # Observability bundle (added 2026-05-31).
    "Prometheus",
    "Grafana",
    # Spark (added 2026-06-04) — standalone cluster, distinct from Ray.
    # Wizard discovery requires `spark_master_source` shim in
    # SourceOverrideManager.source_mapping (multi-container family).
    "Apache Spark",
    # Zeppelin (added 2026-06-04) — Spark-first notebook UI, hard-gated
    # on Spark. Single-container family so no shim needed; wired via
    # 'zeppelin_source' in source_mapping.
    "Apache Zeppelin",
    # Airflow (added 2026-06-04) — code-defined DAG orchestrator.
    # Multi-container family (webserver + scheduler + init), so wizard
    # discovery uses the `airflow_webserver_source` shim in
    # SourceOverrideManager.source_mapping (same pattern as Ray's
    # ray_head_source / Spark's spark_master_source).
    "Apache Airflow",
    # Iceberg REST Catalog (added 2026-07-03) — durable lakehouse catalog
    # for the data-eng track. Single-container family; wired via
    # 'iceberg_rest_source' in source_mapping.
    "Apache Iceberg REST Catalog",
    # Jenkins (added 2026-07-03) — Maven Spark app builder for the data-eng
    # track. Single-container family; wired via 'jenkins_source' in
    # source_mapping.
    "Jenkins",
    # Curated MCP Servers (added 2026-07-03) — disabled-by-default MCP
    # tool package for Postgres, Neo4j, and SearXNG. Single-container
    # family; wired via 'mcp_servers_source' in source_mapping.
    "Curated MCP Servers",
    # Blender MCP (added 2026-07-04) — disabled-by-default host-only
    # creative 3D bridge. Virtual family wired via 'blender_mcp_source'.
    "Blender MCP",
    # Crawl4AI (added 2026-07-03) — disabled-by-default browser-backed
    # extraction API for Local Deep Researcher and n8n HTTP workflows.
    "Crawl4AI",
    # Langfuse (added 2026-07-03) — disabled-by-default LLM trace and
    # eval observability for LiteLLM-routed calls. Multi-container family
    # (web + worker + ClickHouse + init), so wizard discovery uses the
    # `langfuse_web_source` shim while the real flag remains
    # `--langfuse-source`.
    "Langfuse",
    # OpenTelemetry / Tempo / Loki (added 2026-07-03) — disabled-by-default
    # internal observability services. They are surfaced as optional wizard
    # prompts but remain internal-only and Grafana-centered by default.
    "OpenTelemetry Collector",
    "Tempo",
    "Loki",
    # MLflow (added 2026-07-03) — disabled-by-default experiment tracking
    # and MinIO-backed artifacts for the ML Engineering track.
    "MLflow",
    # Label Studio (added 2026-07-03) — disabled-by-default annotation
    # review loop for ML/RAG datasets. Single-container app family wired via
    # `label_studio_source` in source_mapping.
    "Label Studio",
    # Celery + Flower (added 2026-07-03) — disabled-by-default async
    # backend worker tier. Multi-container family (worker + Flower), so
    # discovery uses the `celery_worker_source` shim while the real flag
    # remains `--celery-source`.
    "Celery Worker",
    # Supavisor (added 2026-07-03) — disabled-by-default internal
    # Supabase Postgres transaction pooler for selected app clients.
    "Supavisor",
    # Apache Tika (added 2026-07-03) — disabled-by-default fallback
    # extractor for long-tail document formats.
    "Apache Tika",
    # Neo4j LLM Graph Builder (added 2026-07-03) — disabled-by-default
    # document-to-knowledge-graph UI/API for the RAG track.
    "Neo4j LLM Graph Builder",
    # TEI Reranker (added 2026-06-05) — GPU-accelerated reranker inference.
    # Single-container family; wired via 'tei_reranker_source' in
    # source_mapping with container-cpu / container-gpu / localhost / disabled.
    "TEI Reranker",
    # LightRAG (added 2026-06-05) — knowledge-graph-augmented RAG engine.
    # Single-container family; wired via 'lightrag_source' in source_mapping.
    "LightRAG",
    # Verba (added 2026-07-03) — archived, disabled-by-default Weaviate RAG
    # demo UI for the RAG track.
    "Verba",
    # Trino (added 2026-07-03) — disabled-by-default SQL query engine over
    # the Iceberg REST + MinIO lakehouse path for the data-eng track.
    "Trino",
    # Redpanda (added 2026-07-03) — disabled-by-default Kafka API streaming
    # broker + console for the data-eng track. Single-row family wired via
    # 'redpanda_source' in source_mapping.
    "Redpanda Console",
})


# Services that MUST NOT be in the discovered list: locked (single
# source variant, no user choice), cloud-provider toggles (collected
# via bespoke secret-input steps), or pure virtual / infra.
EXPECTED_NOT_DISCOVERED = frozenset({
    "LiteLLM",
    "Kong API Gateway",
    "Supabase DB",
    "Supabase Meta",
    "Supabase Storage",
    "Supabase Auth",
    "Supabase API",
    "Supabase Realtime",
    "Supabase Studio",
    "Redis",
    "Backend API",
})


@pytest.fixture
def discovery() -> ServiceDiscovery:
    """ServiceDiscovery wired to the real repo manifests."""
    cp = ConfigParser(str(REPO_ROOT))
    return ServiceDiscovery(cp)


def test_discovery_includes_all_configurable_app_services(discovery: ServiceDiscovery) -> None:
    """All 16 configurable services must surface in discover().

    Was a 13-service list pre-C3: Open WebUI, JupyterHub, and Local
    Deep Researcher were missing because of CLI-flag / runtime_sc gaps.
    """
    discovered_names = {svc.display_name for svc in discovery.discover()}

    missing = EXPECTED_DISCOVERED - discovered_names
    assert not missing, (
        f"Wizard discovery is missing services that should be configurable: {sorted(missing)}. "
        f"Got: {sorted(discovered_names)}"
    )


def test_discovery_excludes_locked_and_virtual_services(discovery: ServiceDiscovery) -> None:
    """Locked services (LiteLLM, Kong, Supabase, Redis, Backend) and
    cloud-provider toggles must not appear in the wizard's configurable
    list.
    """
    discovered_names = {svc.display_name for svc in discovery.discover()}

    leaked = EXPECTED_NOT_DISCOVERED & discovered_names
    assert not leaked, (
        f"Wizard discovery is leaking locked/virtual services that should be skipped: "
        f"{sorted(leaked)}"
    )


def test_discovery_count_matches_expected(discovery: ServiceDiscovery) -> None:
    """Pin the exact discovered count so silent additions/removals trip
    the test. Bump this number deliberately when adding a new
    configurable service.
    """
    discovered = discovery.discover()
    assert len(discovered) == len(EXPECTED_DISCOVERED), (
        f"Expected {len(EXPECTED_DISCOVERED)} discovered services, got {len(discovered)}: "
        f"{sorted(s.display_name for s in discovered)}"
    )


def test_open_web_ui_has_container_and_disabled_options(discovery: ServiceDiscovery) -> None:
    """Lock the option list for Open WebUI — regression guard for the
    runtime_sc slice in services/open-webui/service.yml.
    """
    discovered = {svc.key: svc for svc in discovery.discover()}
    assert "open-web-ui" in discovered, "Open WebUI missing from wizard discovery"
    assert set(discovered["open-web-ui"].options) == {"container", "disabled"}


def test_jupyterhub_has_container_and_disabled_options(discovery: ServiceDiscovery) -> None:
    """Lock the option list for JupyterHub — regression guard for the
    runtime_sc slice in services/jupyterhub/service.yml. Without it
    _has_source_options() returns False and the service vanishes.
    """
    discovered = {svc.key: svc for svc in discovery.discover()}
    assert "jupyterhub" in discovered, "JupyterHub missing from wizard discovery"
    assert set(discovered["jupyterhub"].options) == {"container", "disabled"}


def test_local_deep_researcher_has_container_and_disabled_options(
    discovery: ServiceDiscovery,
) -> None:
    """Lock the option list for Local Deep Researcher."""
    discovered = {svc.key: svc for svc in discovery.discover()}
    assert "local-deep-researcher" in discovered, (
        "Local Deep Researcher missing from wizard discovery"
    )
    assert set(discovered["local-deep-researcher"].options) == {"container", "disabled"}


def test_source_mapping_includes_app_service_flags() -> None:
    """SourceOverrideManager.source_mapping must carry the CLI keys for
    the three app services; otherwise the wizard's discover() filter
    drops them.
    """
    from utils.source_override_manager import SourceOverrideManager

    cp = ConfigParser(str(REPO_ROOT))
    mgr = SourceOverrideManager(cp)
    for cli_key in (
        "open_web_ui_source",
        "jupyterhub_source",
        "local_deep_researcher_source",
        "ray_source",
        "prometheus_source",
        "grafana_source",
        "spark_source",
        "zeppelin_source",
        "airflow_source",
        "iceberg_rest_source",
        "langfuse_source",
        "langfuse_web_source",
        "mlflow_source",
        "supavisor_source",
        "tika_source",
    ):
        assert cli_key in mgr.source_mapping, (
            f"{cli_key} missing from SourceOverrideManager.source_mapping — "
            f"wizard discover() will silently filter the service out."
        )


def test_source_mapping_includes_tei_reranker():
    from utils.source_override_manager import SourceOverrideManager
    cp = ConfigParser(str(REPO_ROOT))
    mgr = SourceOverrideManager(cp)
    assert 'tei_reranker_source' in mgr.source_mapping
    assert mgr.source_mapping['tei_reranker_source'] == 'TEI_RERANKER_SOURCE'


def test_source_mapping_includes_lightrag():
    from utils.source_override_manager import SourceOverrideManager
    cp = ConfigParser(str(REPO_ROOT))
    mgr = SourceOverrideManager(cp)
    assert 'lightrag_source' in mgr.source_mapping
    assert mgr.source_mapping['lightrag_source'] == 'LIGHTRAG_SOURCE'


def test_start_py_source_args_dict_includes_every_cli_source_flag() -> None:
    """Every ``@click.option('--<svc>-source', ...)`` in start.py must appear
    as a key in the ``source_args`` dict that's passed to
    ``SourceOverrideManager.collect_overrides``. Otherwise the flag value
    is silently dropped at runtime (the wizard still works because it
    builds keys dynamically; only the CLI-flag path is broken).

    Memory: ``project_cli_source_flag_three_seams.md`` documents the 4
    coordinated edits required when adding a new source flag. PR #17 added
    this guard for ray/prometheus/grafana; PR #35 omitted spark/zeppelin/
    airflow and this regression test would have caught it.
    """
    import ast
    import re

    start_py = (REPO_ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")

    # Collect every --*-source flag declared via @click.option(...).
    cli_flags = set(re.findall(r"@click\.option\(\s*['\"](--[a-z0-9-]+-source)['\"]", start_py))
    assert cli_flags, "regex matched no --*-source @click.option declarations — stale pattern?"
    # Skip the ROOT flag (--spark-source's parent doesn't exist; spark-workers
    # is a non-source plumbing flag handled separately).
    expected_keys = {flag[2:].replace("-", "_") for flag in cli_flags}

    # AST-parse start.py to find the source_args dict literal.
    tree = ast.parse(start_py)
    source_args_keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "source_args"
            and isinstance(node.value, ast.Dict)
        ):
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    source_args_keys.add(k.value)
            break

    missing = expected_keys - source_args_keys
    assert not missing, (
        f"start.py declares --*-source CLI flags but their corresponding keys "
        f"are missing from the source_args dict literal: {sorted(missing)}. "
        f"This silently drops the flag value at runtime — add the keys to the "
        f"source_args dict so SourceOverrideManager.collect_overrides receives them."
    )


def test_every_source_args_key_resolves_in_source_mapping() -> None:
    """collect_overrides() iterates source_mapping, so a key present in
    start.py's source_args but missing from
    SourceOverrideManager.source_mapping is SILENTLY dropped — the flag
    parses, lands in the dict, and never reaches .env. The flag→dict
    seam is guarded above; this closes the dict→mapping seam (the
    audit-C3 bug class)."""
    import ast

    from utils.source_override_manager import SourceOverrideManager

    start_py = (REPO_ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")
    tree = ast.parse(start_py)
    source_args_keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "source_args"
            and isinstance(node.value, ast.Dict)
        ):
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    source_args_keys.add(k.value)
            break
    assert source_args_keys, "source_args dict literal not found in start.py"

    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))
    unmapped = source_args_keys - set(mgr.source_mapping)
    assert not unmapped, (
        f"source_args keys with no source_mapping entry (silently dropped "
        f"by collect_overrides): {sorted(unmapped)}"
    )


def test_command_summary_flags_are_real_click_options(discovery):
    """Every discovered service's command-summary flag must be a real flag.

    The launch-screen command summary builds a copy-pasteable ``./start.sh``
    invocation. It derives each service flag from ``PromptStep.service_key``
    (== ``ServiceInfo.key``). Multi-container families (ray-head, spark-master,
    airflow-webserver) carry their discovery-anchor container key, NOT the
    family flag stem, so they must be remapped via ``_FAMILY_FLAG_STEM`` or the
    summary prints ``--ray-head-source`` etc. — flags Click rejects.

    This guards the H1 fix and fails loudly if a new multi-container family is
    added without a remap entry.
    """
    import start
    from ui.textual.screens.wizard_screen import _FAMILY_FLAG_STEM

    real_source_flags = {
        opt
        for param in start.main.params
        for opt in getattr(param, "opts", [])
        if opt.endswith("-source")
    }
    assert real_source_flags, "no --*-source Click flags found on start.main"

    offenders = []
    for svc in discovery.discover():
        key = _FAMILY_FLAG_STEM.get(svc.key, svc.key)
        flag = "--" + key.replace("_", "-") + "-source"
        if flag not in real_source_flags:
            offenders.append((svc.key, flag))

    assert not offenders, (
        "command-summary would emit flags Click rejects (add a "
        "_FAMILY_FLAG_STEM remap for multi-container families): "
        + ", ".join(f"{k} -> {f}" for k, f in offenders)
    )
