"""One definition per question, in two places that had drifted apart."""

from __future__ import annotations

import pytest

from services.topology import get_topology
from wizard.model.state_builder import (
    _port_from_endpoint,
    resolve_localhost_port,
    resolve_port,
    service_extras,
)


def _row(display_name: str):
    for row in get_topology().rows:
        if row.display_name == display_name:
            return row
    pytest.skip(f"{display_name} row not present")


_ENV = {
    "COMFYUI_ENDPOINT": "http://host.docker.internal:${COMFYUI_MPS_LOCALHOST_PORT:-8188}",
    "COMFYUI_LOCALHOST_PORT": "8000",
    "COMFYUI_MPS_LOCALHOST_PORT": "8188",
    "VLLM_METAL_ENDPOINT": "http://host.docker.internal:${VLLM_METAL_LOCALHOST_PORT:-8000}",
    "VLLM_METAL_LOCALHOST_PORT": "8000",
}


@pytest.mark.parametrize("endpoint,expected", [
    ("http://h:8188", "8188"),
    ("http://h:8188/api", "8188"),
    ("http://h:${V}", "7777"),
    ("http://h:${V:-8188}", "7777"),          # the var wins over the default
    ("http://h:${MISSING:-9000}", "9000"),    # ...and the default is the fallback
    ("http://h", ""),
])
def test_a_port_is_read_through_shell_interpolation(endpoint, expected):
    """A bare `:(\\d+)` cannot match `:${VAR:-8188}` — the `:` is followed by `$`."""
    assert _port_from_endpoint(endpoint, {"V": "7777"}) == expected


def test_comfyui_mps_reports_the_port_it_actually_binds():
    """It reported 8000 — its PLAIN-localhost port — while MPS binds 8188.

    The endpoint parse failed on the `${VAR:-default}` literal and fell
    through to `COMFYUI_LOCALHOST_PORT`. The wrong value then collided with
    vLLM Metal's real 8000 and produced a FALSE port-collision warning.
    """
    assert resolve_localhost_port(_row("ComfyUI"), _ENV) == "8188"


def test_vllm_metal_is_not_blank_in_the_tui():
    """It declares `localhost_endpoint_var` and no `localhost_port_var`.

    The TUI consulted only the latter, so the PORT cell was empty while
    `--no-tui` printed `:8000`.
    """
    assert resolve_localhost_port(_row("vLLM (Metal)"), _ENV) == "8000"


@pytest.mark.parametrize("name,source", [
    ("ComfyUI", "managed-localhost-mps"),
    ("vLLM (Metal)", "managed-localhost"),
])
def test_both_surfaces_agree(name, source):
    row = _row(name)
    tui = resolve_port(name, source, getattr(row, "port_var", None), _ENV)
    shared = resolve_localhost_port(row, _ENV)
    assert tui == f":{shared}"


def test_a_secondary_row_advertises_its_own_source_options():
    """`weaviate` carries two rows with different SOURCE vars.

    Stamping the manifest-level options onto every row advertised Weaviate's
    `container / localhost / disabled` on the Multi2Vec CLIP hover card. All
    three were wrong: `localhost` is not offered for CLIP at all, and both of
    its real container variants were missing.
    """
    clip = service_extras("Multi2Vec CLIP")["options"]
    assert set(clip) == {"container-cpu", "container-gpu", "disabled"}
    assert "localhost" not in clip
    # ...and the primary row is unaffected
    assert set(service_extras("Weaviate")["options"]) == {"container", "localhost", "disabled"}


# ── regen: the START search must be fence-aware like every END search ──


_FENCED = """# Svc

## 1. Overview
body

## 5. Docs contract
Example:

```markdown
## 6. Dependencies & Integrations
### 6.1 Current — Upstream
```

## 6. Dependencies & Integrations
stale

## 7. Troubleshooting
IMPORTANT BODY

## 8. References
refs
"""


def test_a_fenced_example_header_is_not_mistaken_for_the_real_one():
    """The generated tables were spliced INSIDE the fence.

    That left a duplicate header, and the next pass treated the fenced copy as
    the real section — deleting the genuine Dependencies section,
    Troubleshooting and References with it. Regen stopped being idempotent.
    """
    from docs.regen import _detect_position, _slice_deps_section

    start, end = _slice_deps_section(_FENCED)
    assert _FENCED[start:].startswith("## 6. Dependencies & Integrations")
    assert "```markdown" in _FENCED[:start], "the fenced example is above the slice"
    assert "IMPORTANT BODY" not in _FENCED[start:end], "the slice swallowed Troubleshooting"
    assert _detect_position(_FENCED) == 6


def test_an_unterminated_fence_does_not_truncate_the_file():
    """`_fenced_spans` extends an open fence to EOF.

    The old start search matched the header anyway, `end` became `len(text)`,
    and everything from the header down — Troubleshooting, References — was
    replaced.
    """
    from docs.regen import _slice_deps_section

    text = (
        "# S\n\n## 1. Overview\n```\nunterminated\n\n"
        "## 6. Dependencies & Integrations\nstale\n\n## 7. Troubleshooting\nKEEP\n"
    )
    assert _slice_deps_section(text) is None, "a header inside an open fence was spliced"
