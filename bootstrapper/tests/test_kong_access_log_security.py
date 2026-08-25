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
    assert log_format == (
        "atlas '$$request_method $$uri $$server_protocol' $$status "
        "$$body_bytes_sent '\"$$http_user_agent\"' $$kong_request_id"
    )
    assert re.search(r"(?<!\$)\$(?!\$)", log_format) is None

    variables = re.findall(r"\$\$[A-Za-z0-9_]+", log_format)
    assert variables == [
        "$$request_method",
        "$$uri",
        "$$server_protocol",
        "$$status",
        "$$body_bytes_sent",
        "$$http_user_agent",
        "$$kong_request_id",
    ]
    assert set(variables).isdisjoint(
        {
            "$$args",
            "$$query_string",
            "$$arg_apikey",
            "$$request",
            "$$request_uri",
        }
    )
