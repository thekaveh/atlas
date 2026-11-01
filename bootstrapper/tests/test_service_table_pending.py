"""Visual snapshot tests for ServiceTable's pending vs answered rows."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _render(table) -> str:
    """Render a ServiceTable with a mocked terminal width of 200."""
    size_mock = MagicMock()
    size_mock.width = 200
    with patch.object(type(table), "size", new_callable=lambda: property(lambda self: size_mock)):
        return table.render().plain


def test_pending_row_renders_hollow_dot_and_dashes():
    """A pending row shows ◌ and em-dashes; not the source color."""
    from ui.textual.widgets.service_table import ServiceRow, ServiceTable
    row = ServiceRow(
        name="ComfyUI", source="", port="", alias="", category="media",
        pending=True,
    )
    table = ServiceTable([row], columns=1)
    text = _render(table)
    assert "◌" in text  # hollow dot
    # The pending-source slot emits the literal ``"pending…"`` placeholder.
    # The previous ``"pending" in text or "—" in text`` assertion always
    # passed via the em-dash arm (other columns emit em-dashes too), so the
    # pending-source check was a no-op.
    #
    # The source column gets auto-sized to MIN_SOURCE (6) when no row has a
    # longer source, so ``_fit("pending…", 7)`` truncates to ``"pendin…"`` —
    # but the ``"pendin"`` prefix is unique to the pending branch in this
    # table, so it pins the pending-source slot exactly. The literal
    # ``pending…`` substring is asserted on a wider rendering below.
    assert "pendin" in text


def test_pending_row_full_pending_token_when_column_wide_enough():
    """When the source column is wide enough to fit ``pending…`` un-truncated
    (e.g. another row in the same table has a long source value that drives
    src_max up), the full literal ``pending…`` placeholder appears.

    This guards against the prior tautological assertion that accepted
    em-dashes from any column.
    """
    from ui.textual.widgets.service_table import ServiceRow, ServiceTable
    pending_row = ServiceRow(
        name="ComfyUI", source="", port="", alias="", category="media",
        pending=True,
    )
    sibling = ServiceRow(
        name="Other", source="container-cpu-gpu-hybrid", port="63040",
        alias="other.localhost", category="media", pending=False,
        default_source="container-cpu-gpu-hybrid",
    )
    table = ServiceTable([pending_row, sibling], columns=1)
    text = _render(table)
    assert "pending…" in text


def test_answered_row_renders_full_dot_and_real_port():
    """An answered row has the running dot and the actual port number."""
    from ui.textual.widgets.service_table import ServiceRow, ServiceTable
    row = ServiceRow(
        name="ComfyUI", source="container-cpu", port="63040", alias="comfyui.localhost",
        category="media", pending=False, default_source="container-cpu",
    )
    table = ServiceTable([row], columns=1)
    text = _render(table)
    assert "63040" in text
    assert "container-cpu" in text


def test_off_track_label_is_never_truncated_by_shorter_stored_sources():
    """The source column was sized from the stored ``.env`` sources, but an
    off-track row renders the longer ``disabled (off-track)`` label. With
    every stored source at 18 cells or fewer it rendered ``disabled (off-tra…``.
    """
    from ui.textual.widgets.service_table import ServiceRow, ServiceTable
    rows = [
        ServiceRow(name="Spark", source="container", category="data"),
        ServiceRow(name="Ray", source="ray-container-cpu", category="data"),
        ServiceRow(name="LLM Engine", source="ollama-localhost", category="llm"),
        ServiceRow(name="STT", source="disabled", category="media", off_track=True),
    ]
    table = ServiceTable(rows, columns=1)
    off_row = rows[-1]
    text = table._slot_text(off_row, is_cursor=False, widths=table._raw_widths(rows))
    assert "disabled (off-track)" in text.plain
