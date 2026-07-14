from __future__ import annotations

import argparse
import html as html_module
import os
import re
import sys
import tempfile
import textwrap
from contextlib import nullcontext
from pathlib import Path

from .manifest import Manifest, load_manifest


_SVG_RE = re.compile(r"<svg\b[\s\S]*?</svg>", re.IGNORECASE)
_NON_XML_ENTITY_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)([A-Za-z][A-Za-z0-9]+);")


def extract_svg(html: str) -> str:
    match = _SVG_RE.search(html)
    if not match:
        raise ValueError("Diagram master does not contain an inline SVG")
    svg = match.group(0)
    lines = svg.splitlines()
    if len(lines) > 1:
        svg = lines[0] + "\n" + textwrap.dedent("\n".join(lines[1:]))
    return _NON_XML_ENTITY_RE.sub(
        lambda item: html_module.unescape(item.group(0)),
        svg,
    )


def svg_to_png(svg: str, output: Path, *, width: int = 1800) -> None:
    if sys.platform == "darwin":
        candidates = (
            Path("/opt/homebrew/opt/cairo/lib"),
            Path("/usr/local/opt/cairo/lib"),
        )
        cairo_lib = next((path for path in candidates if path.is_dir()), None)
        if cairo_lib:
            existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
            paths = [str(cairo_lib), *[item for item in existing.split(":") if item]]
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(dict.fromkeys(paths))
    import cairosvg

    output.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(output), output_width=width)


def render_all(
    manifest: Manifest,
    repo_root: Path,
    site_img_dir: Path,
    png_dir: Path,
    wiki_img_dir: Path | None = None,
    *,
    check_png: bool = False,
) -> None:
    site_img_dir.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)
    if wiki_img_dir:
        wiki_img_dir.mkdir(parents=True, exist_ok=True)
    temp_context = (
        tempfile.TemporaryDirectory(prefix="atlas-diagram-check-")
        if check_png
        else nullcontext(None)
    )
    with temp_context as temp:
        for diagram in manifest.diagrams:
            master = repo_root / diagram.master
            svg = extract_svg(master.read_text(encoding="utf-8"))
            (site_img_dir / f"{diagram.id}.svg").write_text(svg + "\n", encoding="utf-8")
            committed_png = png_dir / f"{diagram.id}.png"
            rendered_png = Path(temp) / committed_png.name if temp else committed_png
            svg_to_png(svg, rendered_png)
            if check_png:
                if not committed_png.is_file() or committed_png.read_bytes() != rendered_png.read_bytes():
                    relative = committed_png.relative_to(repo_root)
                    raise RuntimeError(f"Committed diagram PNG is stale: {relative}")
            if wiki_img_dir:
                (wiki_img_dir / committed_png.name).write_bytes(rendered_png.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Atlas documentation diagrams")
    parser.add_argument("--manifest", default="docs/manifest.yaml")
    args = parser.parse_args()
    root = Path.cwd()
    manifest = load_manifest(root / args.manifest, root)
    render_all(
        manifest,
        root,
        root / "generated" / "site" / "assets" / "img",
        root / "docs" / "diagrams" / "img",
        root / "generated" / "wiki" / "img",
    )


if __name__ == "__main__":
    main()
