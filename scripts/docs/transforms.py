from __future__ import annotations

import os
import posixpath
import re
from pathlib import PurePosixPath

from .links import is_forbidden
from .manifest import Manifest


_MARKDOWN_LINK_RE = re.compile(
    r"(?P<image>!)?\[(?P<label>[^\]]*)\]\((?P<target><[^>]+>|[^)\s]+)(?P<title>\s+[^)]*)?\)"
)
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def build_source_map(manifest: Manifest, surface: str) -> dict[str, str]:
    if surface not in {"site", "wiki"}:
        raise ValueError(f"Unsupported generated surface: {surface}")
    return {
        page.source: (
            page.site_path.as_posix() if surface == "site" else page.wiki_path.as_posix()
        )
        for page in manifest.pages
    }


def _canonical_target(source_path: str, target: str) -> str:
    source_parent = PurePosixPath(source_path).parent
    return posixpath.normpath((source_parent / target).as_posix())


def _relative_output(output_path: str, target_path: str) -> str:
    parent = PurePosixPath(output_path).parent.as_posix()
    return posixpath.relpath(target_path, parent if parent != "." else ".")


def rewrite_for_surface(
    markdown: str,
    *,
    surface: str,
    source_path: str,
    output_path: str,
    source_map: dict[str, str],
    asset_map: dict[str, str] | None = None,
) -> str:
    assets = asset_map or {}

    def rewrite_match(match: re.Match[str]) -> str:
        image = bool(match.group("image"))
        label = match.group("label")
        raw_target = match.group("target").strip("<>")
        title = match.group("title") or ""
        target, separator, anchor = raw_target.partition("#")
        anchor_suffix = f"#{anchor}" if separator else ""

        if is_forbidden(raw_target, surface):
            return label
        if not target or target.startswith("#") or _SCHEME_RE.match(target) or target.startswith("//"):
            return match.group(0)

        canonical = _canonical_target(source_path, target)
        if canonical in assets:
            rewritten = _relative_output(output_path, assets[canonical]) + anchor_suffix
            prefix = "!" if image else ""
            return f"{prefix}[{label}]({rewritten}{title})"
        if target.lower().endswith(".ipynb"):
            return label
        if target.lower().endswith(".md"):
            mapped = source_map.get(canonical)
            if mapped is None:
                return label
            rewritten = _relative_output(output_path, mapped) + anchor_suffix
            return f"[{label}]({rewritten}{title})"
        if image:
            return match.group(0)
        return label

    output: list[str] = []
    in_fence = False
    for line in markdown.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            output.append(line)
        elif in_fence:
            output.append(line)
        else:
            output.append(_MARKDOWN_LINK_RE.sub(rewrite_match, line))
    return "".join(output)
