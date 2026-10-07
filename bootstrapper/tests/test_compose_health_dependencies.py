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


def test_backend_weaviate_init_dependency_is_optional():
    compose = yaml.safe_load(
        (REPO / "services" / "backend" / "compose.yml").read_text(encoding="utf-8")
    )
    dependency = compose["services"]["backend"]["depends_on"]["weaviate-init"]
    assert dependency["condition"] == "service_completed_successfully"
    assert dependency["required"] is False


def test_long_lived_commands_and_probes_carry_no_password_flags():
    """Container processes are visible to every local user through ps on
    Linux; Flower's --basic-auth and redis-cli -a put the shared dashboard
    and Redis passwords there for the container's whole life."""
    flower = yaml.safe_load(
        (REPO / "services/celery/compose.yml").read_text(encoding="utf-8")
    )["services"]["flower"]
    redis = yaml.safe_load(
        (REPO / "services/redis/compose.yml").read_text(encoding="utf-8")
    )["services"]["redis"]
    assert not any("--basic-auth" in part for part in flower["command"])
    assert flower["environment"]["FLOWER_BASIC_AUTH"].startswith("${DASHBOARD_USERNAME:-kong_admin}")
    # Exact exec form: a CMD-SHELL string would make a "-a" membership check
    # pass vacuously while still carrying the password.
    # PONG required: redis-cli exits 0 on -LOADING/NOAUTH error replies.
    assert redis["healthcheck"]["test"] == ["CMD-SHELL", "redis-cli ping | grep -q PONG"]
    assert " -a " not in redis["healthcheck"]["test"][1]
    assert redis["environment"]["REDISCLI_AUTH"] == "${REDIS_PASSWORD}"


def test_no_compose_probe_or_init_script_puts_a_bearer_secret_on_curl_argv():
    """`curl -H "Authorization: Bearer $KEY"` shows the key in `ps` and
    `docker top` on every run (#1381); the header goes on stdin (-K-)."""
    import re
    from pathlib import Path

    services = Path(__file__).resolve().parents[2] / "services"
    pattern = re.compile(r"-H\s*\\?[\"']Authorization: Bearer \$")
    offenders = [
        str(path.relative_to(services))
        for path in [*services.glob("*/compose.yml"), *services.glob("*/**/*.sh")]
        if pattern.search(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert offenders == []
