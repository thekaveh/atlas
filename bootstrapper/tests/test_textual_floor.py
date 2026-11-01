"""Textual version floor + the textual-image compatibility seam.

textual-image declares `textual>=0.68.0` with no upper bound, so pip
metadata cannot catch a break across Textual majors. These tests are
the only guard on that seam.
"""

from __future__ import annotations

import importlib.metadata as md

from packaging.version import Version

import pytest


def test_textual_is_at_least_8_2_8():
    """Selection auto-scroll, cross-container selection and the
    TextSelected event all require >= 8.x (TextSelected: 6.11.0)."""
    installed = Version(md.version("textual"))
    assert installed >= Version("8.2.8"), (
        f"expected textual >= 8.2.8, got {installed}"
    )


def test_text_selected_event_is_importable():
    """Added in Textual 6.11.0. Pass 3 binds log-pane selection to a
    ViewModel through this event, so its absence is a hard failure."""
    from textual.events import TextSelected  # noqa: F401


def test_richlog_still_allows_selection():
    """LogPane subclasses RichLog. If a Textual upgrade ever flips this
    default, mouse selection in the log pane dies silently."""
    from textual.widgets import RichLog

    assert RichLog.ALLOW_SELECT is True


def test_textual_image_seam_still_imports():
    """atlas_splash.py imports these two names lazily at render time, so
    a break would surface as a broken splash at runtime rather than an
    ImportError at startup. Import them eagerly here instead."""
    from textual_image.widget import Image, get_cell_size  # noqa: F401


def test_textual_image_stays_below_0_13():
    """textual-image 0.13+ requires Python >=3.12 and would raise the
    bootstrapper's 3.11 floor."""
    installed = Version(md.version("textual-image"))
    assert installed < Version("0.13"), (
        f"textual-image {installed} would break the Python 3.11 floor"
    )


def test_textual_image_consumes_real_pixels_and_preserves_image_operations():
    from PIL import Image
    from textual_image._pixeldata import PixelData

    # Pillow 13 is supported; textual-image 0.12 still calls getdata().
    # Assert that diagnostic locally so unrelated warnings remain errors.
    getdata_warning = (
        r"^Image\.Image\.getdata is deprecated and will be removed in "
        r"Pillow 14 \(2027-10-15\)\. Use get_flattened_data instead\.$"
    )
    with Image.new("RGB", (2, 2), (12, 34, 56)) as source:
        pixels = PixelData(source)
        with pytest.warns(DeprecationWarning, match=getdata_warning):
            assert [list(row) for row in pixels] == [[(12, 34, 56)] * 2] * 2
        grayscale = PixelData(source, mode="grayscale")
        with pytest.warns(DeprecationWarning, match=getdata_warning):
            assert [list(row) for row in grayscale] == [[30, 30], [30, 30]]
        scaled = pixels.scaled(4, 6)
        assert (scaled.width, scaled.height) == (4, 6)
        cropped = scaled.cropped(1, 2, 3, 5)
        assert (cropped.width, cropped.height) == (2, 3)
        assert cropped.to_base64().startswith("iVBOR")
