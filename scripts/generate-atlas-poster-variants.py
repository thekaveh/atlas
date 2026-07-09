#!/usr/bin/env python3
"""Generate Atlas documentation poster variants from existing brand art."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "atlas-source.png"
WORDMARK_SOURCE = ROOT / "assets" / "atlas-poster.png"
WORDMARK_SCALE = 0.56
WORDMARK_BOTTOM_MARGIN = 4
VARIANTS = {
    "blue": (96, 165, 250),
    "gold": (213, 162, 42),
}


def _rounded_border(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=18,
        outline=(*color, 190),
        width=3,
    )
    return overlay


def _expanded_box(
    box: tuple[int, int, int, int],
    size: tuple[int, int],
    padding: int,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    width, height = size
    return (
        max(0, left - padding),
        max(0, top - padding),
        min(width, right + padding),
        min(height, bottom + padding),
    )


def _extract_wordmark(source: Image.Image, poster: Image.Image) -> Image.Image:
    width, height = source.size
    lower_box = (0, int(height * 0.56), width, height)
    source_lower = source.crop(lower_box)
    poster_lower = poster.crop(lower_box)

    diff = ImageChops.difference(poster_lower, source_lower).convert("L")
    mask = diff.point(lambda value: 0 if value < 8 else min(255, (value - 8) * 5))
    mask = mask.filter(ImageFilter.GaussianBlur(0.7))
    bbox = mask.getbbox()
    if bbox is None:
        raise RuntimeError("Could not locate the Atlas poster wordmark.")

    bbox = _expanded_box(bbox, poster_lower.size, 14)
    wordmark = poster_lower.crop(bbox).convert("RGBA")
    wordmark.putalpha(mask.crop(bbox))
    return wordmark


def _place_wordmark(base: Image.Image, wordmark: Image.Image) -> None:
    width, height = base.size
    target_size = (
        max(1, round(wordmark.width * WORDMARK_SCALE)),
        max(1, round(wordmark.height * WORDMARK_SCALE)),
    )
    scaled = wordmark.resize(target_size, Image.Resampling.LANCZOS)
    x = (width - scaled.width) // 2
    y = height - scaled.height - WORDMARK_BOTTOM_MARGIN
    base.alpha_composite(scaled, (x, y))


def generate() -> None:
    source = Image.open(SOURCE).convert("RGB")
    poster = Image.open(WORDMARK_SOURCE).convert("RGB")
    if source.size != poster.size:
        raise RuntimeError("Atlas source and poster assets must have the same dimensions.")

    wordmark = _extract_wordmark(source, poster)
    for name, border_color in VARIANTS.items():
        image = source.convert("RGBA")
        _place_wordmark(image, wordmark)
        image.alpha_composite(_rounded_border(source.size, border_color))
        image.convert("RGB").save(ROOT / "assets" / f"atlas-poster-{name}.png", optimize=True)


if __name__ == "__main__":
    generate()
