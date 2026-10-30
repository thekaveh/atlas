"""Hover-tooltip card for the wizard ServiceTable.

Covers the pieces that don't need a live Textual app:
  - ``_tooltip_pairs`` / ``_build_tooltip`` — the aligned key/value card.
  - ``_row_at`` — mapping a widget-relative (x, y) back to a rendered row.

The visual hover behavior itself is validated live in the TUI, not here.
"""

from __future__ import annotations

import pytest

from ui.textual.widgets.service_table import ServiceRow, ServiceTable


def _row(name, **kw):
    return ServiceRow(name=name, **kw)


def test_tooltip_pairs_basic():
    row = _row("n8n", source="container", alias="n8n.localhost",
               alias_port="63000", port=":63064")
    assert ServiceTable._tooltip_pairs(row) == [
        ("Source", "container"),
        ("URL", "http://n8n.localhost:63000"),
        ("Local", "localhost:63064"),   # port's leading colon stripped (no '::')
    ]


def test_tooltip_pairs_strips_leading_colon_port():
    # resolve_port returns ':<port>'; the card must not render 'localhost::'.
    row = _row("MinIO Console", source="container", port=":64094")
    pairs = ServiceTable._tooltip_pairs(row)
    assert ("Local", "localhost:64094") in pairs
    assert all("::" not in v for _, v in pairs)


def test_tooltip_pairs_omits_empty_url_and_dash_port():
    row = _row("Supabase DB", source="container", alias="", alias_port="",
               port="—")
    assert ServiceTable._tooltip_pairs(row) == [("Source", "container")]


def test_launch_flow_row_rebuild_preserves_hover_metadata():
    """Regression: run_launch_flow (CLI-flag launch mode) rebuilt each
    ServiceRow without tooltip_extra / source_options / depends_on, dropping
    the hover-card metadata that wizard mode shows. recompute_ports_for_base
    already preserves all three; the launch-flow rebuild must too. Scoped
    source check so it can't silently regress."""
    import re
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "ui" / "textual" / "integration.py"
    ).read_text(encoding="utf-8")
    # Isolate the run_launch_flow function body (up to the next top-level def,
    # or end-of-file — run_launch_flow is currently the last function).
    m = re.search(r"\ndef run_launch_flow\(.*?(?=\ndef |\Z)", src, re.S)
    assert m, "could not locate run_launch_flow in integration.py"
    body = m.group(0)
    for field in ("tooltip_extra=", "source_options=", "depends_on="):
        assert field in body, (
            f"run_launch_flow no longer preserves {field} on its ServiceRow "
            f"rebuild — CLI-flag launch mode would drop hover-card metadata."
        )


@pytest.mark.parametrize("provider_key", ["openai", "anthropic", "openrouter"])
@pytest.mark.parametrize("case", [
    (False, "", "enabled", "replacement", (True, True)),
    (True, "saved", "disabled", None, (False, True)),
    (True, "saved", "enabled", "", (True, False)),
    (True, "saved", None, None, (True, True)),
])
def test_cli_launch_cloud_overview_uses_effective_flags(
    tmp_path, monkeypatch, provider_key, case,
):
    from types import SimpleNamespace
    from core.config_parser import ConfigParser
    from core.port_manager import PortManager
    from ui.textual import integration
    from ui.textual.screens import wizard_screen
    from ui.textual.widgets.info_box import CloudApiSummary
    from utils.cloud_providers import CLOUD_PROVIDERS

    old_enabled, old_key, source_override, key_override, expected = case
    provider = next(p for p in CLOUD_PROVIDERS if p.key == provider_key)
    env = {provider.source_var: "enabled" if old_enabled else "disabled", provider.api_key_var: old_key}
    parser = ConfigParser(str(tmp_path))
    monkeypatch.setattr(parser, "parse_env_file", lambda: dict(env))
    summaries = [CloudApiSummary(name=provider.name, enabled=old_enabled, key_set=bool(old_key))]
    monkeypatch.setattr(integration, "_build_steps_and_rows", lambda *a, **kw: (
        [], [], [], 63000, SimpleNamespace(consumers=[]), summaries,
    ))
    monkeypatch.setattr(PortManager, "port_offsets", lambda self: {})
    monkeypatch.setattr(wizard_screen, "WizardScreen", lambda **kwargs: kwargs)
    captured = []

    def mount(app):
        app.push_screen = captured.append
        app.on_mount()

    monkeypatch.setattr(integration, "_run_app_with_process_cleanup", mount)
    sources = {} if source_override is None else {provider.source_var.lower(): source_override}
    keys = {} if key_override is None else {provider.api_key_var: key_override}
    starter = SimpleNamespace(run_port_migration=lambda *a: None)
    integration.run_launch_flow(parser, None, starter=starter, source_args=sources,
                                stack_options={"cloud_api_keys": keys})

    actual = captured[0]["cloud_apis"][0]
    assert (actual.enabled, actual.key_set) == expected
    assert "replacement" not in repr(actual)


def test_tooltip_pairs_appends_extra():
    row = _row("MinIO Console", source="container", alias="minio.localhost",
               alias_port="63000", port=":63019",
               tooltip_extra=[("S3 API", "http://localhost:63020"),
                              ("S3 (Kong)", "http://s3.minio.localhost:63000")])
    pairs = ServiceTable._tooltip_pairs(row)
    assert pairs[0] == ("Source", "container")
    assert ("S3 API", "http://localhost:63020") in pairs
    assert ("S3 (Kong)", "http://s3.minio.localhost:63000") in pairs


def test_build_tooltip_renders_aligned_card():
    row = _row("MinIO Console", source="container", alias="minio.localhost",
               alias_port="63000", port=":63019",
               tooltip_extra=[("S3 API", "http://localhost:63020")])
    card = ServiceTable._build_tooltip(row)
    lines = card.plain.splitlines()
    assert lines[0] == "MinIO Console"               # title
    # labels left-aligned to a common width (Source/URL/Local/S3 API -> 6)
    assert lines[1] == "Source  container"
    assert lines[2] == "URL     http://minio.localhost:63000"
    assert lines[3] == "Local   localhost:63019"
    assert lines[4] == "S3 API  http://localhost:63020"
    # URL value is styled with the accent color
    assert any(span.style and "#7dcfff" in str(span.style) for span in card.spans)


def test_row_at_single_column():
    t = ServiceTable(rows=[])
    rows = [_row("a"), _row("b"), _row("c")]
    t._hit_flat = rows
    t._hit_offsets = [0]
    t._hit_lens = [3]
    t._hit_block_w = 40
    assert t._row_at(5, 0) is rows[0]
    assert t._row_at(5, 2) is rows[2]
    assert t._row_at(5, 3) is None
    assert t._row_at(5, -1) is None


def test_row_at_two_columns():
    t = ServiceTable(rows=[])
    rows = [_row("a"), _row("b"), _row("c"), _row("d")]
    t._hit_flat = rows
    t._hit_offsets = [0, 2]
    t._hit_lens = [2, 2]
    t._hit_block_w = 40
    assert t._row_at(5, 0) is rows[0]
    assert t._row_at(45, 1) is rows[3]
    assert t._row_at(85, 0) is None


def test_row_at_no_layout_returns_none():
    assert ServiceTable(rows=[])._row_at(0, 0) is None


def test_tooltip_pairs_includes_source_options_and_depends():
    row = _row("MinIO Console", source="container",
               source_options=["container", "disabled"],
               depends_on=["supabase", "redis"])
    pairs = ServiceTable._tooltip_pairs(row)
    assert ("Source options", "container, disabled") in pairs
    assert ("Depends on", "supabase, redis") in pairs
    # config rows come before access rows
    labels = [k for k, _ in pairs]
    assert labels.index("Source options") < labels.index("Source") + 3


def test_recompute_ports_preserves_card_metadata():
    """A base-port change must not strip a row's hover-card metadata
    (the bug where MinIO lost its S3 endpoints after the base-port step)."""
    from ui.textual.integration import recompute_ports_for_base

    class _CP:
        def parse_env_file(self):
            return {}

    row = ServiceRow(
        name="MinIO Console", source="container", alias="minio.localhost",
        tooltip_extra=[("S3 API", "http://localhost:63020")],
        source_options=["container", "disabled"],
        depends_on=["supabase"],
    )
    out = recompute_ports_for_base(64000, [row], _CP(), {})
    assert out[0].tooltip_extra == [("S3 API", "http://localhost:63020")]
    assert out[0].source_options == ["container", "disabled"]
    assert out[0].depends_on == ["supabase"]


def test_source_options_highlights_active_source():
    row = _row("LLM Engine", source="ollama-localhost",
               source_options=["ollama-container-cpu", "ollama-localhost", "none"])
    card = ServiceTable._build_tooltip(row)
    active_accented = False
    for span in card.spans:
        seg = card.plain[span.start:span.end]
        style = str(span.style)
        if seg == "ollama-localhost":
            active_accented = "#7dcfff" in style and "bold" in style
        if seg == "none":   # an inactive option must NOT be accented
            assert "#7dcfff" not in style
    assert active_accented, "active source option should be accent + bold"
