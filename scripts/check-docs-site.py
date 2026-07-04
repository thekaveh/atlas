#!/usr/bin/env python3
"""Validate Atlas' generated MkDocs documentation site."""

from __future__ import annotations

import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True)


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.links.append(value)


def _target_exists(source: Path, link: str) -> bool:
    parsed = urlparse(link)
    if parsed.scheme or parsed.netloc or link.startswith(("#", "mailto:", "tel:")):
        return True
    path = parsed.path
    if not path:
        return True
    if path.startswith("/atlas/"):
        candidate = SITE / path.removeprefix("/atlas/")
    elif path.startswith("/"):
        candidate = SITE / path.lstrip("/")
    else:
        candidate = source.parent / path
    candidate = candidate.resolve()
    try:
        candidate.relative_to(SITE.resolve())
    except ValueError:
        return False
    if candidate.is_dir():
        candidate = candidate / "index.html"
    elif candidate.suffix == "":
        candidate = candidate / "index.html"
    return candidate.exists()


def validate_built_site_links() -> None:
    missing: list[str] = []
    for html_file in sorted(SITE.rglob("*.html")):
        parser = LinkParser()
        parser.feed(html_file.read_text(encoding="utf-8"))
        for link in parser.links:
            if not _target_exists(html_file, link):
                missing.append(f"{html_file.relative_to(SITE)} -> {link}")
    if missing:
        details = "\n".join(missing[:50])
        extra = "" if len(missing) <= 50 else f"\n... and {len(missing) - 50} more"
        raise SystemExit(f"Built MkDocs site has broken internal links:\n{details}{extra}")


def main() -> int:
    run([sys.executable, "scripts/generate-docs-site.py", "--check"])
    # CI contract: mkdocs build --strict
    run(["mkdocs", "build", "--strict"])
    validate_built_site_links()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
