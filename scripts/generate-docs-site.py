#!/usr/bin/env python3
"""Generate the Atlas MkDocs site, diagram catalog, and wiki export.

The generated docs site is a navigation/publishing layer over Atlas' existing
source-of-truth docs. Per-service READMEs and per-service architecture diagrams
remain owned by their service folders and by ``bootstrapper.docs.regen``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bootstrapper"))

from docs.sitegen.mkdocs_config import build_mkdocs_config  # noqa: E402
from docs.sitegen.model import load_docs_model  # noqa: E402
from docs.sitegen.pages import architecture_pages, reference_pages, static_pages  # noqa: E402
from docs.sitegen.services import service_pages  # noqa: E402
from docs.sitegen.theme import copy_artifacts, theme_artifacts  # noqa: E402
from docs.sitegen.wiki import wiki_pages  # noqa: E402


DOCS = ROOT / "docs"
SERVICES = ROOT / "services"
PUBLIC_URL = "https://thekaveh.github.io/atlas/"
GITHUB_BLOB_URL = "https://github.com/thekaveh/atlas/blob/main"
HOME = DOCS / "index.md"
SITE = DOCS / "site"
WIKI = DOCS / "wiki"


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _write_or_check(path: Path, content: str, check: bool) -> int:
    content = content.rstrip() + "\n"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing == content:
        return 0
    if check:
        print(f"DRIFT: {_rel(path)}")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return 0


def _copy_or_check_binary(source: Path, target: Path, check: bool) -> int:
    existing = target.read_bytes() if target.exists() else b""
    expected = source.read_bytes()
    if existing == expected:
        return 0
    if check:
        print(f"DRIFT: {_rel(target)}")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return 0

def build_artifacts() -> dict[Path, str]:
    model = load_docs_model(ROOT)
    artifacts: dict[Path, str] = {}
    artifacts[ROOT / "mkdocs.yml"] = yaml.safe_dump(build_mkdocs_config(model), sort_keys=False)
    artifacts.update(theme_artifacts(ROOT))
    artifacts.update(static_pages(model))
    artifacts.update(service_pages(model))
    artifacts.update(reference_pages(model))
    artifacts.update(architecture_pages(model))
    artifacts.update(wiki_pages(model))
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail if generated docs are stale.")
    args = parser.parse_args()

    drift = 0
    for path, content in sorted(build_artifacts().items(), key=lambda item: str(item[0])):
        drift += _write_or_check(path, content, args.check)
    for source, target in copy_artifacts(ROOT):
        drift += _copy_or_check_binary(source, target, args.check)
    return 2 if drift and args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
