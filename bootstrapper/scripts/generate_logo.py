#!/usr/bin/env python3
"""Maintainer-only generator: render assets/atlas-source.png to per-width
terminal cell-grids committed under bootstrapper/ui/textual/assets/.

Requires Pillow and chafa (NOT runtime deps):
    pip install pillow   &&   brew install chafa   # or apt-get install chafa

Run from the repo root:
    python bootstrapper/scripts/generate_logo.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "assets" / "atlas-source.png"
POSTER = REPO / "assets" / "atlas-poster.png"
PROFILE = REPO / "assets" / "atlas-profile.png"
SOCIAL = REPO / "assets" / "atlas-social-preview.png"
OUT_DIR = REPO / "bootstrapper" / "ui" / "textual" / "assets"
BREAKPOINTS = (160, 120, 100, 80, 60)

# Reuse the canonical ATLAS-PLATFORM block-art rows + blue gradient so the
# poster wordmark always matches the in-app title lockup (single dash, etc.).
sys.path.insert(0, str(REPO / "bootstrapper"))
from core.process_runner import CommandOutputTooLarge, run_with_deadline  # noqa: E402
from ui.textual.widgets.block_logo import _LOGO_ROWS_FULL, _GRADIENT  # noqa: E402

# Monospace fonts to try for the block-art wordmark (maintainer machine).
_FONTS = (
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/Library/Fonts/Menlo.ttc",
)

# Locked params.
CROP = (0.101, 0.0, 0.941, 1.0)
GAMMA, BRIGHT, SAT, CONTRAST = 1.12, 1.04, 1.22, 1.16

_SGR = re.compile(r"\x1b\[([0-9;?]*)m")
_OTHER = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_DEFAULT_FG = "#c0caf5"
_DEFAULT_BG = "#0e0f18"
_ARTIFACT_TOOL_TIMEOUT_SECONDS = 120.0


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _enhanced_source() -> Image.Image:
    im = Image.open(SRC).convert("RGB")
    w, h = im.size
    box = (int(CROP[0] * w), int(CROP[1] * h), int(CROP[2] * w), int(CROP[3] * h))
    im = im.crop(box)
    lut = [min(255, int(((i / 255) ** GAMMA) * 255 * BRIGHT)) for i in range(256)]
    im = im.point(lut * 3)
    im = ImageEnhance.Color(im).enhance(SAT)
    im = ImageEnhance.Contrast(im).enhance(CONTRAST)
    return im


def _chafa(tmp_png: Path, cols: int, rows: int) -> str:
    cmd = [
        "chafa", "-f", "symbols", "-c", "full", "--symbols", "block+space",
        "--fill", "block", "--dither", "none", "-s", f"{cols}x{rows}", str(tmp_png),
    ]
    try:
        result = run_with_deadline(
            cmd, timeout_seconds=_ARTIFACT_TOOL_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("chafa timed out while generating logo data") from exc
    except CommandOutputTooLarge as exc:
        raise RuntimeError("chafa exceeded its output limit") from exc
    result.check_returncode()
    return result.stdout


def _parse(ansi: str) -> list[list[list[str]]]:
    fg, bg = _DEFAULT_FG, _DEFAULT_BG
    ansi = _OTHER.sub(lambda m: m.group(0) if m.group(0).endswith("m") else "", ansi)
    rows: list[list[list[str]]] = []
    cur: list[list[str]] = []
    i = 0
    while i < len(ansi):
        m = _SGR.match(ansi, i)
        if m:
            parts = [p for p in m.group(1).split(";") if p != ""]
            j = 0
            while j < len(parts):
                c = parts[j]
                if c == "0":
                    fg, bg = _DEFAULT_FG, _DEFAULT_BG
                elif c == "38" and parts[j + 1 : j + 2] == ["2"]:
                    fg = _hex((int(parts[j + 2]), int(parts[j + 3]), int(parts[j + 4]))); j += 4
                elif c == "48" and parts[j + 1 : j + 2] == ["2"]:
                    bg = _hex((int(parts[j + 2]), int(parts[j + 3]), int(parts[j + 4]))); j += 4
                j += 1
            i = m.end(); continue
        ch = ansi[i]
        if ch == "\n":
            rows.append(cur); cur = []; i += 1; continue
        if ch in "\r\x1b":
            i += 1; continue
        cur.append([ch, fg, bg]); i += 1
    if cur:
        rows.append(cur)
    rows = [r for r in rows if r]
    width = max(len(r) for r in rows)
    for r in rows:
        while len(r) < width:
            r.append([" ", _DEFAULT_FG, _DEFAULT_BG])
    return rows


def _font(size: int):
    for p in _FONTS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hexrgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _wordmark(target_w: int) -> Image.Image:
    """ATLAS-PLATFORM block art (single-dash, blue gradient) as RGBA ~target_w
    wide; rows tiled by ink height so the dash never seams into a '='."""
    n = len(_LOGO_ROWS_FULL[0])
    size = 2
    while _font(size).getlength("█") * n < target_w and size < 80:
        size += 1
    f = _font(size)
    cw = f.getlength("█")
    bb = f.getbbox("█")
    ink = bb[3] - bb[1]
    im = Image.new("RGBA", (int(cw * n), int(ink * len(_LOGO_ROWS_FULL)) + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, row in enumerate(_LOGO_ROWS_FULL):
        color = _hexrgb(_GRADIENT[i] if i < len(_GRADIENT) else _GRADIENT[-1])
        d.text((0, int(i * ink - bb[1])), row, font=f, fill=color, anchor="la")
    return im


def _scrim(base: Image.Image, frac: float = 0.42, strength: int = 190) -> Image.Image:
    """Darken the bottom ``frac`` so the wordmark reads (poster lower third)."""
    w, h = base.size
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    y0 = int(h * (1 - frac))
    for y in range(y0, h):
        a = int(strength * (y - y0) / max(1, h - y0))
        d.line([(0, y), (w, y)], fill=(6, 8, 18, a))
    return Image.alpha_composite(base.convert("RGBA"), ov).convert("RGB")


def _finish(base: Image.Image, wm_frac: float, bottom_frac: float) -> Image.Image:
    """Scrim the lower third and overlay the centered ATLAS-PLATFORM wordmark."""
    base = _scrim(base)
    w, h = base.size
    wm = _wordmark(int(w * wm_frac))
    base.paste(wm, ((w - wm.width) // 2, h - wm.height - int(h * bottom_frac)), wm)
    return base


def _compose_poster(square: bool, wm_frac: float, bottom_frac: float = 0.07) -> Image.Image:
    base = Image.open(SRC).convert("RGB")
    if square:
        w, h = base.size
        s = min(w, h)
        base = base.crop(((w - s) // 2, 0, (w - s) // 2 + s, s))
    return _finish(base, wm_frac, bottom_frac)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    im = _enhanced_source()
    aspect = im.height / im.width
    tmp = OUT_DIR / "_tmp_source.png"
    im.save(tmp)
    try:
        for cols in BREAKPOINTS:
            rows = max(1, int(cols * aspect * 0.5) + 1)
            grid = _parse(_chafa(tmp, cols, rows))
            data = {"cols": len(grid[0]), "rows": len(grid), "cells": grid}
            out = OUT_DIR / f"atlas_hero_{cols}.json"
            out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            print(f"wrote {out.relative_to(REPO)}  {data['cols']}x{data['rows']}")
    finally:
        tmp.unlink(missing_ok=True)

    # Movie-poster (landscape) for the app splash + README hero, and the
    # square wordmarked profile picture for the GitHub avatar.
    _compose_poster(square=False, wm_frac=0.72, bottom_frac=0.023).save(POSTER)
    print(f"wrote {POSTER.relative_to(REPO)}")
    _compose_poster(square=True, wm_frac=0.86, bottom_frac=0.023).save(PROFILE)
    _optimize_png(PROFILE)  # GitHub avatar/social-preview must be < 1MB
    print(f"wrote {PROFILE.relative_to(REPO)} ({PROFILE.stat().st_size // 1024} KB)")

    # GitHub social-preview card: exactly 1280x640 (2:1). The full poster
    # (globe + figure + wordmark) is fit inside without cropping; the sides are
    # filled with a blurred, darkened copy of the artwork so there are no flat
    # letterbox bars. Must be < 1MB.
    _compose_social().save(SOCIAL)
    _optimize_png(SOCIAL)
    print(f"wrote {SOCIAL.relative_to(REPO)} ({SOCIAL.stat().st_size // 1024} KB)")

    # Block-art cell-grids of the landscape POSTER (with wordmark) for the
    # splash fallback on terminals that can't paint inline images in a TUI
    # (Warp). Pure-text at runtime, so it never queries the terminal / crashes.
    poster_img = Image.open(POSTER).convert("RGB")
    paspect = poster_img.height / poster_img.width
    ptmp = OUT_DIR / "_tmp_poster_grid.png"
    poster_img.save(ptmp)
    try:
        # 60 included so narrow terminals (~80 cols, where the splash budgets
        # ~0.85*width) still get a centered poster with a navy margin.
        for cols in (160, 120, 100, 80, 60):
            rows = max(1, int(cols * paspect * 0.5) + 1)
            grid = _parse(_chafa(ptmp, cols, rows))
            data = {"cols": len(grid[0]), "rows": len(grid), "cells": grid}
            out = OUT_DIR / f"atlas_poster_{cols}.json"
            out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            print(f"wrote {out.relative_to(REPO)}  {data['cols']}x{data['rows']}")
    finally:
        ptmp.unlink(missing_ok=True)


def _compose_social(w: int = 1280, h: int = 640) -> Image.Image:
    """Native 2:1 social card: crop the source scene to the card's aspect
    (full-bleed, no letterbox/blur fill), weighting the vertical trim toward
    the top (empty sky/space) so the globe, figure, and wordmark stay intact,
    then scrim + place the wordmark fresh and resize to exactly w x h."""
    base = Image.open(SRC).convert("RGB")
    W, H = base.size
    new_h = int(W * h / w)           # height for the card's aspect (2:1)
    cut = max(0, H - new_h)
    top = int(cut * 0.85)            # take most of the trim from the top sky
    base = base.crop((0, top, W, top + new_h))
    base = _finish(base, wm_frac=0.68, bottom_frac=0.035)
    return base.resize((w, h), Image.LANCZOS)


def _optimize_png(path: Path, quality: str = "80-95") -> None:
    """Shrink a PNG below GitHub's 1MB avatar/social-preview limit via
    pngquant (palette quantization — preserves dimensions/aspect, near-lossless
    for this artwork). No-op with a note if pngquant is unavailable."""
    if shutil.which("pngquant") is None:
        print(f"  (pngquant not found; {path.name} left unoptimized — may exceed 1MB)")
        return
    try:
        run_with_deadline(
            [
                "pngquant",
                "--force",
                "--quality",
                quality,
                "--output",
                str(path),
                str(path),
            ],
            timeout_seconds=_ARTIFACT_TOOL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("pngquant timed out while optimizing artwork") from exc
    except CommandOutputTooLarge as exc:
        raise RuntimeError("pngquant exceeded its output limit") from exc


if __name__ == "__main__":
    main()
