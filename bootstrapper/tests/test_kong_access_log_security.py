from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_kong_proxy_access_log_format_omits_query_bearing_variables() -> None:
    compose = yaml.safe_load((REPO_ROOT / "services/kong/compose.yml").read_text())
    environment = compose["services"]["kong-api-gateway"]["environment"]

    log_format = environment["KONG_NGINX_HTTP_LOG_FORMAT"]
    assert environment["KONG_PROXY_ACCESS_LOG"] == "/dev/stdout atlas"
    assert log_format.startswith("atlas ")

    variables = set(re.findall(r"\$\$[A-Za-z0-9_]+", log_format))
    assert {
        "$$request_method",
        "$$uri",
        "$$server_protocol",
        "$$status",
        "$$body_bytes_sent",
        "$$http_user_agent",
        "$$request_id",
    } <= variables
    assert variables.isdisjoint({"$$request", "$$request_uri", "$$http_referer"})
