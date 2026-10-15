from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_kong_proxy_access_log_format_omits_query_bearing_variables() -> None:
    compose = yaml.safe_load((REPO_ROOT / "services/kong/compose.yml").read_text())
    environment = compose["services"]["kong-api-gateway"]["environment"]

    log_format = environment["KONG_NGINX_HTTP_LOG_FORMAT"]
    assert environment["KONG_PROXY_ACCESS_LOG"] == "/dev/stdout atlas"
    # One quoted string: nginx joins split format strings with no separator.
    assert log_format == (
        "atlas '$$request_method $$uri $$server_protocol $$status "
        "$$body_bytes_sent \"$$http_user_agent\" $$kong_request_id'"
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


@pytest.mark.parametrize("surface", ["repository", "site", "wiki"])
def test_searxng_guide_discloses_gateway_logging(surface: str) -> None:
    """Protect the privacy warning, allowing different prose and page structure."""
    import markdown
    from markdown_it import MarkdownIt
    from tests.three_surface_test_utils import markdown_block_shape, surface_text

    source = "services/searxng/README.md"
    text = (REPO_ROOT / source).read_text() if surface == "repository" else surface_text(source, surface)
    prose = " ".join(
        token.content for token in MarkdownIt("commonmark").parse(text) if token.type == "inline"
    ).lower()
    prose = re.sub(r"\s+", " ", prose)
    # These concepts distinguish query-scrubbed access logs from error-log exposure.
    disclosures = {
        "gateway access logging": r"kong.*access (?:metadata|logs?|logging)",
        "query omission in access logs": r"access.*(?:omit|exclud|redact)[^.]*query|query[^.]*(?:omit|exclud|redact)[^.]*access",
        "query exposure in error logs": r"error log[^.]*query",
        "failure condition": r"upstream[^.]*fail|fail[^.]*upstream",
    }
    for concept, pattern in disclosures.items():
        assert re.search(pattern, prose), (surface, concept)
    for false_guarantee in ("it keeps no logs", "there are no access logs", "forgets a query once"):
        assert false_guarantee not in prose, (surface, false_guarantee)
    # Both renderers must preserve the source's list and code-block containers.
    rendered = markdown.markdown(text, extensions=["fenced_code", "tables", "attr_list"])
    assert markdown_block_shape(rendered) == markdown_block_shape(MarkdownIt().render(text))
