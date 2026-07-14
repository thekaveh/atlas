from __future__ import annotations

import argparse
import hashlib
import html as html_module
import os
import re
import struct
import sys
import textwrap
from pathlib import Path
import zlib

from .manifest import Manifest, load_manifest


_SVG_RE = re.compile(r"<svg\b[\s\S]*?</svg>", re.IGNORECASE)
_NON_XML_ENTITY_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)([A-Za-z][A-Za-z0-9]+);")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SOURCE_HASH_KEY = b"AtlasSourceSHA256"


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


def diagram_source_fingerprint(svg: str, *, width: int = 1800) -> str:
    payload = f"atlas-diagram-v1\nwidth={width}\n{svg}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum)


def _stamp_png(path: Path, source_fingerprint: str) -> None:
    png = path.read_bytes()
    if not png.startswith(_PNG_SIGNATURE):
        raise ValueError(f"Renderer did not produce a PNG: {path}")
    iend = png.rfind(b"IEND")
    if iend < 4:
        raise ValueError(f"Renderer produced an invalid PNG: {path}")
    chunk_start = iend - 4
    payload = _SOURCE_HASH_KEY + b"\0" + source_fingerprint.encode("ascii")
    path.write_bytes(png[:chunk_start] + _png_chunk(b"tEXt", payload) + png[chunk_start:])


def png_source_fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    png = path.read_bytes()
    if not png.startswith(_PNG_SIGNATURE):
        return None
    offset = len(_PNG_SIGNATURE)
    while offset + 12 <= len(png):
        length = struct.unpack(">I", png[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(png):
            return None
        chunk_type = png[offset + 4 : offset + 8]
        payload = png[offset + 8 : offset + 8 + length]
        if chunk_type == b"tEXt":
            key, separator, value = payload.partition(b"\0")
            if separator and key == _SOURCE_HASH_KEY:
                try:
                    return value.decode("ascii")
                except UnicodeDecodeError:
                    return None
        if chunk_type == b"IEND":
            break
        offset = chunk_end
    return None


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
    _stamp_png(output, diagram_source_fingerprint(svg, width=width))


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
    for diagram in manifest.diagrams:
        master = repo_root / diagram.master
        svg = extract_svg(master.read_text(encoding="utf-8"))
        (site_img_dir / f"{diagram.id}.svg").write_text(svg + "\n", encoding="utf-8")
        committed_png = png_dir / f"{diagram.id}.png"
        if check_png:
            expected = diagram_source_fingerprint(svg)
            if png_source_fingerprint(committed_png) != expected:
                relative = committed_png.relative_to(repo_root)
                raise RuntimeError(f"Committed diagram PNG is stale: {relative}")
        else:
            svg_to_png(svg, committed_png)
        if wiki_img_dir:
            (wiki_img_dir / committed_png.name).write_bytes(committed_png.read_bytes())


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
