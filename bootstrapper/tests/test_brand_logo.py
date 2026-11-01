"""Unit tests for utils.brand_logo — the BRAND_LOGO_FILE override + ATLAS fallback.

The block-art lockup is brand-overridable via BRAND_LOGO_FILE (same BRAND_*
rebranding contract as the name/tagline/credits). resolve() is a pure function
of the env (and the file it points at), so these tests are .env-independent.
"""
from __future__ import annotations

from utils import brand_logo


def test_default_is_atlas():
    full, compact, threshold = brand_logo.resolve({})
    assert full == brand_logo.ATLAS_FULL
    assert compact == brand_logo.ATLAS_COMPACT
    assert threshold == len(brand_logo.ATLAS_FULL[0]) + 1  # 118 + 1 = 119


def test_empty_path_falls_back_to_atlas():
    assert brand_logo.resolve({"BRAND_LOGO_FILE": "   "})[0] == brand_logo.ATLAS_FULL


def test_missing_file_falls_back_to_atlas(tmp_path):
    env = {"BRAND_LOGO_FILE": str(tmp_path / "nope.txt")}
    assert brand_logo.resolve(env)[0] == brand_logo.ATLAS_FULL


def test_all_blank_file_falls_back_to_atlas(tmp_path):
    f = tmp_path / "blank.txt"
    f.write_text("\n  \n\n", encoding="utf-8")
    assert brand_logo.resolve({"BRAND_LOGO_FILE": str(f)})[0] == brand_logo.ATLAS_FULL


def test_undecodable_file_falls_back_to_atlas(tmp_path):
    """A non-UTF-8 file (CP437 art, a PNG) must not crash the banner or TUI."""
    expected = (
        brand_logo.ATLAS_FULL,
        brand_logo.ATLAS_COMPACT,
        brand_logo.width_threshold(brand_logo.ATLAS_FULL),
    )
    for name, body in (("cp437.txt", b"\xdb\xdb\xdb ART\n"),
                       ("logo.png", b"\x89PNG\r\n\x1a\n")):
        f = tmp_path / name
        f.write_bytes(body)
        assert brand_logo.resolve({"BRAND_LOGO_FILE": str(f)}) == expected


def test_custom_file_with_separator_splits_full_and_compact(tmp_path):
    f = tmp_path / "logo.txt"
    f.write_text("FULL-ROW-1\nFULL-ROW-22\n---\nCMP1\nCMP2\n", encoding="utf-8")
    full, compact, threshold = brand_logo.resolve({"BRAND_LOGO_FILE": str(f)})
    assert full == ["FULL-ROW-1", "FULL-ROW-22"]
    assert compact == ["CMP1", "CMP2"]
    assert threshold == len("FULL-ROW-22") + 1  # widest full row + 1


def test_custom_file_without_separator_reuses_full_for_compact(tmp_path):
    f = tmp_path / "logo.txt"
    f.write_text("ONLY-FULL-1\nONLY-FULL-2\n", encoding="utf-8")
    full, compact, _ = brand_logo.resolve({"BRAND_LOGO_FILE": str(f)})
    assert full == ["ONLY-FULL-1", "ONLY-FULL-2"]
    assert compact == full


def test_custom_file_trims_blank_edges_but_keeps_interior(tmp_path):
    f = tmp_path / "logo.txt"
    # leading/trailing blank lines trimmed; interior spacing preserved verbatim
    f.write_text("\n\nROW  A\nROW  B\n\n", encoding="utf-8")
    full, _, _ = brand_logo.resolve({"BRAND_LOGO_FILE": str(f)})
    assert full == ["ROW  A", "ROW  B"]


def test_width_threshold_helper():
    assert brand_logo.width_threshold(["12345"]) == 6
    assert brand_logo.width_threshold([]) == 1
