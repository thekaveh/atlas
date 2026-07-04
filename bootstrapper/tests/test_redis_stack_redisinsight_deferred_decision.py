from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REDIS_STACK = ROOT / "docs" / "research" / "candidates" / "redis-stack.md"
REDISINSIGHT = ROOT / "docs" / "research" / "candidates" / "redisinsight.md"
REDIS_README = ROOT / "services" / "redis" / "README.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFESTS = [
    ROOT / "services" / "redis-stack" / "service.yml",
    ROOT / "services" / "redisinsight" / "service.yml",
]


def test_redis_candidates_record_july_deferred_decisions() -> None:
    stack = REDIS_STACK.read_text(encoding="utf-8")
    insight = REDISINSIGHT.read_text(encoding="utf-8")

    for phrase in [
        "## Deferred decision (2026-07-04)",
        "Redis 8",
        "tri-license",
        "RSALv2",
        "SSPLv1",
        "AGPLv3",
        "REDIS_VARIANT=oss|stack",
        "concrete module-backed workflow",
    ]:
        assert phrase in stack

    for phrase in [
        "## Deferred decision (2026-07-04)",
        "RedisInsight 3.6.0",
        "RI_ACCEPT_TERMS_AND_CONDITIONS",
        "RI_REDIS_HOST",
        "RI_ENCRYPTION_KEY",
        "REDISINSIGHT_SOURCE=disabled|container|localhost",
        "sensitive operator GUI",
    ]:
        assert phrase in insight


def test_future_contracts_cover_tracks_ports_routes_and_topology() -> None:
    combined = "\n".join(
        [
            REDIS_STACK.read_text(encoding="utf-8"),
            REDISINSIGHT.read_text(encoding="utf-8"),
        ]
    )

    expected_terms = [
        "`data-eng`",
        "`gen-ai-eng`",
        "`all`",
        "`data`",
        "`apps`",
        "`REDIS_PORT`",
        "`REDISINSIGHT_PORT`",
        "disabled by default",
        "Wizard placement",
        "no default Kong route",
        "custom `BASE_PORT`",
        "REDIS_PASSWORD",
        "redis-exporter",
        "Prometheus",
        "Grafana",
        "n8n",
        "Kong",
        "Open WebUI",
        "LightRAG",
        "JupyterHub",
        "Celery",
        "LiteLLM",
        "backend",
        "Weaviate",
        "Hermes",
        "data_flow.calls",
        "init companion",
        "AOF",
        "ACL",
        "license acceptance",
        "route auth",
        "module persistence",
    ]

    for term in expected_terms:
        assert term in combined


def test_redis_stack_and_redisinsight_remain_out_of_service_graph() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    redis_readme = REDIS_README.read_text(encoding="utf-8")

    for manifest in SERVICE_MANIFESTS:
        assert not manifest.exists()

    assert "| Redis Stack (redis-stack-server) | data | redis | [candidates/redis-stack.md]" in matrix
    assert "| RedisInsight | data | redis | [candidates/redisinsight.md]" in matrix
    assert "Redis is infrastructure; no Kong route." in redis_readme


def test_strategy_report_names_redis_deferral_gate() -> None:
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")

    assert (
        "July 4, 2026 decision keeps Redis Stack and RedisInsight deferred"
        in strategy
    )
