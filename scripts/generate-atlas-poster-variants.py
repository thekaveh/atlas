#!/usr/bin/env python3
"""Generate Atlas documentation poster variants from the logo-less source art."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "atlas-source.png"
FONT_CANDIDATES = [
    Path("/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf"),
    Path("/System/Library/Fonts/SFNSMono.ttf"),
    Path("/System/Library/Fonts/Menlo.ttc"),
]
VARIANTS = {
    "blue": {
        "border": (96, 165, 250),
        "text": (118, 180, 255),
        "glow": (45, 135, 255),
    },
    "gold": {
        "border": (213, 162, 42),
        "text": (250, 205, 99),
        "glow": (213, 162, 42),
    },
}


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


def _fit_font(draw: ImageDraw.ImageDraw, text: str, width: int) -> ImageFont.FreeTypeFont:
    size = 88
    while size >= 52:
        font = _font(size)
        left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font)
        if right - left <= width * 0.46:
            return font
        size -= 2
    return _font(size)


def _rounded_border(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    inset = 10
    draw.rounded_rectangle(
        (inset, inset, width - inset - 1, height - inset - 1),
        radius=18,
        outline=(*color, 190),
        width=3,
    )
    return overlay


def _wordmark_layer(
    size: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    text_color: tuple[int, int, int],
    glow_color: tuple[int, int, int],
) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_width = right - left
    text_height = bottom - top
    x = (width - text_width) // 2
    y = height - text_height - 34

    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for offset, alpha in ((0, 150), (2, 110), (4, 70)):
        glow_draw.text((x - left, y - top + offset), text, font=font, fill=(*glow_color, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(5))
    layer.alpha_composite(glow)

    for dx, dy, alpha in ((2, 2, 130), (-1, 1, 90), (0, 0, 255)):
        draw.text((x - left + dx, y - top + dy), text, font=font, fill=(*text_color, alpha))
    return layer


def generate() -> None:
    source = Image.open(SOURCE).convert("RGB")
    text = "ATLAS-PLATFORM"
    scratch = ImageDraw.Draw(Image.new("RGB", source.size))
    font = _fit_font(scratch, text, source.size[0])

    for name, colors in VARIANTS.items():
        image = source.convert("RGBA")
        image.alpha_composite(
            _wordmark_layer(source.size, text, font, colors["text"], colors["glow"])
        )
        image.alpha_composite(_rounded_border(source.size, colors["border"]))
        image.convert("RGB").save(ROOT / "assets" / f"atlas-poster-{name}.png", optimize=True)


if __name__ == "__main__":
    generate()
