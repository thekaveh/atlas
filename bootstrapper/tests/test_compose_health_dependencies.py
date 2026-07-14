"""Every ``service_healthy`` dependency must target a healthchecked service."""

from __future__ import annotations

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]


def test_service_healthy_dependencies_have_healthchecks():
    services: dict[str, dict] = {}
    for compose_path in sorted((REPO / "services").glob("*/compose.yml")):
        document = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        services.update(document.get("services", {}))

    missing: list[str] = []
    for service_name, service in services.items():
        for dependency_name, config in (service.get("depends_on") or {}).items():
            if not isinstance(config, dict):
                continue
            if config.get("condition") != "service_healthy":
                continue
            if "healthcheck" not in services.get(dependency_name, {}):
                missing.append(f"{service_name} -> {dependency_name}")

    assert missing == [], "service_healthy targets without healthchecks: " + ", ".join(missing)


def test_neo4j_healthcheck_executes_a_real_cypher_probe():
    compose = yaml.safe_load(
        (REPO / "services" / "neo4j" / "compose.yml").read_text(encoding="utf-8")
    )
    healthcheck = compose["services"]["neo4j-graph-db"]["healthcheck"]
    command = " ".join(healthcheck["test"])

    assert healthcheck.get("disable") is not True
    assert "cypher-shell" in command
    assert "RETURN 1" in command
