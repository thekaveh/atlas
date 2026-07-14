from __future__ import annotations

import argparse
import os
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.links.append(value)


def _target_exists(site_dir: Path, source: Path, link: str) -> bool:
    parsed = urlparse(link)
    if parsed.scheme or parsed.netloc or link.startswith(("#", "mailto:", "tel:")):
        return True
    path = parsed.path
    if not path:
        return True
    if path.startswith("/atlas/"):
        candidate = site_dir / path.removeprefix("/atlas/")
    elif path.startswith("/"):
        candidate = site_dir / path.lstrip("/")
    else:
        candidate = source.parent / path
    candidate = candidate.resolve()
    try:
        candidate.relative_to(site_dir.resolve())
    except ValueError:
        return False
    if candidate.is_dir() or candidate.suffix == "":
        candidate = candidate / "index.html"
    return candidate.exists()


def validate_built_site_links(site_dir: Path) -> list[str]:
    missing: list[str] = []
    for html_file in sorted(site_dir.rglob("*.html")):
        parser = LinkParser()
        parser.feed(html_file.read_text(encoding="utf-8"))
        for link in parser.links:
            if not _target_exists(site_dir, html_file, link):
                missing.append(f"{html_file.relative_to(site_dir)} -> {link}")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and validate the generated MkDocs site")
    parser.add_argument(
        "--built-only",
        action="store_true",
        help="Validate an existing site/ tree without rebuilding it",
    )
    args = parser.parse_args()
    root = Path.cwd()
    if not args.built_only:
        subprocess.run([sys.executable, "-m", "scripts.docs.check_docs"], cwd=root, check=True)
        env = os.environ.copy()
        env["NO_MKDOCS_2_WARNING"] = "1"
        subprocess.run(
            [sys.executable, "-m", "mkdocs", "build", "--strict"],
            cwd=root,
            env=env,
            check=True,
        )
    missing = validate_built_site_links(root / "site")
    if missing:
        details = "\n".join(missing[:50])
        extra = "" if len(missing) <= 50 else f"\n... and {len(missing) - 50} more"
        raise SystemExit(f"Built MkDocs site has broken internal links:\n{details}{extra}")
    print("PASS built-site local links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
