"""Sanity tests for ``tools.generate_readme_topology.generate_block``.

The README block is generated from the live manifests + topology, so this
test pins the structural contract — markers, category labels (in display
order), and at least one known data point survive the regeneration.
"""

from __future__ import annotations

from pathlib import Path

from services.topology import CATEGORY_LABELS, CATEGORY_ORDER
from tools.generate_readme_topology import generate_block


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_generate_block_has_topology_markers():
    """Output is delimited by ``<!-- TOPOLOGY:BEGIN -->`` and ``<!-- TOPOLOGY:END -->``."""
    block = generate_block(_REPO_ROOT / "services")
    assert block.startswith("<!-- TOPOLOGY:BEGIN -->"), block[:80]
    # Trailing newline is appended after the END marker.
    assert block.rstrip().endswith("<!-- TOPOLOGY:END -->"), block[-80:]
    assert "Auto-generated" not in block


def test_generate_block_contains_all_category_labels_in_order():
    """Every category label appears, and they appear in CATEGORY_ORDER."""
    block = generate_block(_REPO_ROOT / "services")
    positions = {}
    for cat in CATEGORY_ORDER:
        label = CATEGORY_LABELS[cat]
        idx = block.find(label)
        assert idx >= 0, f"category label {label!r} missing from generated block"
        positions[cat] = idx
    # Labels must appear in display order.
    ordered_positions = [positions[c] for c in CATEGORY_ORDER]
    assert ordered_positions == sorted(ordered_positions), (
        f"category labels out of order: {positions}"
    )


def test_generate_block_contains_known_row():
    """Redpanda and Supabase DB are stable anchors in the expanded data band."""
    block = generate_block(_REPO_ROOT / "services")
    assert "Redpanda Console" in block
    assert "63011" in block
    assert "Supabase DB" in block
    assert "63012" in block


def test_check_mode_reports_drift_without_writing_and_bad_markers_are_refused(tmp_path):
    """`--check` rewrote the README, and swapped markers duplicated the text
    between them (2026-10-08 run, cycle 66)."""
    import pytest

    from tools.generate_readme_topology import update_readme

    services = Path(__file__).resolve().parents[2] / "services"
    readme = tmp_path / "README.md"
    stale = "intro\n<!-- TOPOLOGY:BEGIN -->\nold\n<!-- TOPOLOGY:END -->\noutro\n"
    readme.write_text(stale)
    assert update_readme(readme, services, check=True) is False
    assert readme.read_text() == stale
    update_readme(readme, services)
    assert update_readme(readme, services, check=True) is True
    swapped = "a\n<!-- TOPOLOGY:END -->\nb\n<!-- TOPOLOGY:BEGIN -->\nc\n"
    readme.write_text(swapped)
    with pytest.raises(RuntimeError):
        update_readme(readme, services)
    assert readme.read_text() == swapped
