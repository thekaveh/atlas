"""When a user-typed localhost port collides with another row's host
port (e.g. typing 64000 for ollama-localhost when Kong is also at
64000), the pre-launch summary surfaces a warning line. Doesn't
block launch — just informs.

The pre-launch summary builder lives at
``bootstrapper/start.py::AtlasStarter.build_pre_launch_summary_table``
(a method that reads env_vars from self.config_parser and returns a
Rich ``Group(table, cloud_panel, *warnings)``). The collision logic
itself is factored into a module-level free function
``_detect_port_collisions(rows)`` so it can be unit-tested in
isolation without spinning up the full Starter — that's what most of
the tests below exercise. A final integration test renders the actual
builder against a real Starter instance and checks the warning shows
up in the rendered output.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from start import _detect_port_collisions, AtlasStarter


def _make_service(name, source, port):
    """The detector consumes ``(name, port_val)`` tuples — the same
    shape ``build_pre_launch_summary_table`` collects as it iterates
    services. ``source`` is recorded for documentation only; the
    detector treats ``port == "-" / "" / None`` as "disabled" and
    skips them, mirroring the builder's port_val convention."""
    if source == "disabled" or port is None:
        return (name, "-")
    return (name, port)


def _render(rendered) -> str:
    """Render a Rich renderable (Group / Text / str) to a plain
    string, mirroring what the user sees in the pre-launch summary."""
    if isinstance(rendered, str):
        return rendered
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, width=200).print(rendered)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pure detector tests — no Starter, no env, deterministic.
# ---------------------------------------------------------------------------


def test_port_collision_flagged_in_summary():
    """Two rows resolving to the same host port produce a warning."""
    services = [
        _make_service("Kong", "container", ":64000"),
        _make_service("LLM Engine", "ollama-localhost", ":64000"),
    ]
    warnings = _detect_port_collisions(services)
    assert len(warnings) == 1, f"Expected one warning, got: {warnings}"
    text = warnings[0]
    assert "⚠" in text or "collision" in text.lower(), (
        f"Expected a collision warning marker; got:\n{text}"
    )
    assert "64000" in text
    assert "LLM Engine" in text
    assert "Kong" in text


def test_no_collision_no_warning():
    """When all host ports are unique, no warning line appears."""
    services = [
        _make_service("Kong", "container", ":64000"),
        _make_service("LLM Engine", "ollama-localhost", ":11434"),
    ]
    warnings = _detect_port_collisions(services)
    assert warnings == [], f"Expected no warnings, got: {warnings}"


def test_disabled_rows_dont_participate_in_collision_detection():
    """Disabled rows have no port — they shouldn't trigger collision
    even if multiple of them share the same ``-`` placeholder."""
    services = [
        _make_service("Kong", "container", ":64000"),
        _make_service("Ray", "disabled", None),
        _make_service("Hermes", "disabled", None),
    ]
    warnings = _detect_port_collisions(services)
    assert warnings == [], f"Expected no warnings, got: {warnings}"


def test_three_way_collision_named_in_warning():
    """When 3+ rows collide on the same port, all are named in the
    same warning line."""
    services = [
        _make_service("Kong", "container", ":64000"),
        _make_service("LLM Engine", "ollama-localhost", ":64000"),
        _make_service("ComfyUI", "localhost", ":64000"),
    ]
    warnings = _detect_port_collisions(services)
    assert len(warnings) == 1, f"Expected one combined warning, got: {warnings}"
    text = warnings[0]
    assert "Kong" in text
    assert "LLM Engine" in text
    assert "ComfyUI" in text
    assert "64000" in text


def test_empty_port_string_skipped():
    """A row with port_val of empty string is treated as disabled."""
    services = [
        _make_service("Kong", "container", ":64000"),
        ("Disabled Thing", ""),
        ("Another Disabled", ""),
    ]
    warnings = _detect_port_collisions(services)
    assert warnings == []


def test_non_numeric_port_skipped():
    """A row whose 'port' is non-numeric (e.g. an external URL
    fragment leaked through) doesn't participate in collision
    detection — only digit-only ports do."""
    services = [
        ("Kong", ":64000"),
        ("External Foo", ":abc"),
        ("External Bar", ":abc"),
    ]
    warnings = _detect_port_collisions(services)
    assert warnings == []


def test_bare_number_port_handled():
    """The detector accepts ``64000`` and ``:64000`` interchangeably,
    since the builder's port_val convention has evolved over time."""
    services = [
        ("Kong", "64000"),
        ("LLM Engine", ":64000"),
    ]
    warnings = _detect_port_collisions(services)
    assert len(warnings) == 1
    assert "64000" in warnings[0]


def test_multiple_independent_collisions_each_warned():
    """Two independent colliding port groups produce two warnings."""
    services = [
        ("Kong", ":64000"),
        ("LLM Engine", ":64000"),
        ("Postgres A", ":5432"),
        ("Postgres B", ":5432"),
        ("Unique", ":7777"),
    ]
    warnings = _detect_port_collisions(services)
    assert len(warnings) == 2, f"Expected two warnings, got: {warnings}"
    joined = "\n".join(warnings)
    assert "64000" in joined
    assert "5432" in joined
    assert "7777" not in joined


# ---------------------------------------------------------------------------
# Integration test — render the real builder against a forced collision
# and confirm the warning appears in the rendered output.
# ---------------------------------------------------------------------------


def test_builder_includes_warning_when_collision_present(monkeypatch):
    """End-to-end: instantiate Starter, force two rows (Kong +
    LLM Engine in ollama-localhost mode) onto the same host port via a
    monkeypatched env, render the builder, confirm the yellow
    collision warning appears in the rendered output."""
    starter = AtlasStarter()

    # Build a synthetic env that pins KONG_HTTP_PORT and the LLM
    # Engine's localhost endpoint var (LITELLM_OLLAMA_UPSTREAM — the
    # one ``_get_localhost_port`` actually reads) to the same port.
    forced_port = "64000"
    forced_env = {
        "KONG_HTTP_PORT": forced_port,
        "LITELLM_OLLAMA_UPSTREAM": f"http://localhost:{forced_port}",
        "OLLAMA_LOCALHOST_PORT": forced_port,
    }

    def _forced_env(*args, **kwargs):
        return dict(forced_env)

    monkeypatch.setattr(starter.config_parser, "parse_env_file", _forced_env)

    # Force the source map so the LLM Engine row renders in
    # ollama-localhost mode (which causes _get_localhost_port to fire).
    def _forced_sources(*args, **kwargs):
        return {"LLM_PROVIDER_SOURCE": "ollama-localhost"}

    monkeypatch.setattr(starter.config_parser, "parse_service_sources", _forced_sources)

    rendered = starter.build_pre_launch_summary_table()
    text = _render(rendered)
    assert "⚠" in text, (
        f"Expected the collision warning marker in the rendered "
        f"summary; got:\n{text}"
    )
    assert forced_port in text, (
        f"Expected the colliding port {forced_port} in the warning; got:\n{text}"
    )


# ---------------------------------------------------------------------------
# _get_localhost_port resolution — endpoint-var first, localhost_port_var
# fallback so port-only rows (no endpoint URL) don't render '-'. Regression
# for the TUI/--no-tui cross-surface divergence: services that declare only a
# localhost port number (OpenClaw, Neo4j Graph DB) previously showed '-' in
# the linear summary while the TUI table showed the real port.
# ---------------------------------------------------------------------------


def test_localhost_port_falls_back_to_port_var_for_endpointless_rows():
    """Neo4j Graph DB / OpenClaw declare a localhost_port_var but no
    localhost_endpoint_var; the summary must surface the configured port."""
    assert (
        AtlasStarter._get_localhost_port(
            "Neo4j Graph DB", {"NEO4J_LOCALHOST_BOLT_PORT": "63023"}
        )
        == ":63023"
    )
    assert (
        AtlasStarter._get_localhost_port(
            "OpenClaw", {"OPENCLAW_LOCALHOST_PORT": "63065"}
        )
        == ":63065"
    )


def test_localhost_port_prefers_endpoint_var_over_port_var():
    """When a row has both, the endpoint var wins — it encodes the active
    source variant's port (e.g. ComfyUI's MPS port 8188, not the plain 8000)."""
    port = AtlasStarter._get_localhost_port(
        "ComfyUI",
        {
            "COMFYUI_ENDPOINT": "http://host.docker.internal:8188",
            "COMFYUI_LOCALHOST_PORT": "8000",
        },
    )
    assert port == ":8188"


def test_localhost_port_dash_when_unset_or_unknown():
    """No port var / unknown service → '-', never a bogus port."""
    assert AtlasStarter._get_localhost_port("OpenClaw", {}) == "-"
    assert AtlasStarter._get_localhost_port("OpenClaw", {"OPENCLAW_LOCALHOST_PORT": ""}) == "-"
    assert AtlasStarter._get_localhost_port("No Such Service", {}) == "-"
