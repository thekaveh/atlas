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


def _theme_css() -> str:
    return """:root {
  --atlas-bg: #020617;
  --atlas-bg-panel: #07111f;
  --atlas-bg-panel-2: #0b1728;
  --atlas-ink: #e5f4ff;
  --atlas-ink-strong: #f8fbff;
  --atlas-muted: #9fb7cc;
  --atlas-soft: #17324a;
  --atlas-line: #24445f;
  --atlas-blue: #60a5fa;
  --atlas-sky: #38bdf8;
  --atlas-cyan: #0ea5e9;
  --atlas-electric: #7dd3fc;
}

html {
  background: #020617;
}

html, body {
  color: var(--atlas-ink);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

body {
  min-height: 100vh;
  background:
    linear-gradient(115deg, rgba(14, 165, 233, 0.16), transparent 30rem),
    linear-gradient(180deg, #07111f 0, #020617 28rem, #020617 100%);
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  background:
    linear-gradient(rgba(56, 189, 248, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(56, 189, 248, 0.04) 1px, transparent 1px);
  background-size: 72px 72px;
  mask-image: linear-gradient(to bottom, black, transparent 72%);
}

.navbar {
  background: rgba(2, 6, 23, 0.88) !important;
  border-bottom: 1px solid rgba(96, 165, 250, 0.22);
  box-shadow: 0 14px 42px rgba(0, 0, 0, 0.32);
  backdrop-filter: saturate(160%) blur(18px);
}

.navbar .container {
  max-width: 1480px;
}

.navbar-brand {
  color: var(--atlas-ink-strong) !important;
  font-weight: 800;
  letter-spacing: 0;
  margin-right: 2rem;
  white-space: nowrap;
}

.navbar-brand::before {
  content: '';
  display: inline-block;
  width: 0.72rem;
  height: 0.72rem;
  margin-right: 0.58rem;
  border-radius: 3px;
  background: linear-gradient(135deg, var(--atlas-electric), var(--atlas-cyan));
  box-shadow: 0 0 22px rgba(56, 189, 248, 0.58);
}

.navbar-dark .navbar-nav .nav-link {
  color: #adc4d8;
  font-size: 0.9rem;
  font-weight: 650;
  padding-left: 0.55rem;
  padding-right: 0.55rem;
  white-space: nowrap;
}

.navbar-dark .navbar-nav .nav-link:hover,
.navbar-dark .navbar-nav .nav-link:focus,
.navbar-dark .navbar-nav .nav-link.active {
  color: #f8fbff;
}

.navbar-collapse {
  overflow-x: auto;
  scrollbar-width: none;
}

.navbar-collapse::-webkit-scrollbar {
  display: none;
}

.navbar-nav {
  flex-wrap: nowrap;
}

.navbar-toggler {
  border: 1px solid rgba(96, 165, 250, 0.28);
}

.dropdown-menu {
  background: rgba(7, 17, 31, 0.98);
  border: 1px solid rgba(96, 165, 250, 0.24);
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.42);
}

.dropdown-item {
  color: #c8d9e8;
}

.dropdown-item:hover,
.dropdown-item:focus {
  color: #f8fbff;
  background: rgba(14, 165, 233, 0.16);
}

body > .container {
  max-width: 1480px;
  margin-top: 2.5rem;
  margin-bottom: 5rem;
  padding: 3.2rem 3.6rem;
  background: linear-gradient(180deg, rgba(7, 17, 31, 0.76), rgba(2, 6, 23, 0.9));
  border: 1px solid rgba(96, 165, 250, 0.18);
  border-radius: 8px;
  box-shadow: 0 18px 70px rgba(0, 0, 0, 0.34);
}

.row {
  align-items: flex-start;
}

.col-md-9 {
  flex: 0 0 78%;
  max-width: 78%;
}

.col-md-3 {
  flex: 0 0 22%;
  max-width: 22%;
  border-left: 1px solid rgba(96, 165, 250, 0.16);
}

h1, h2, h3, h4 {
  color: var(--atlas-ink-strong);
  font-weight: 760;
  letter-spacing: 0;
}

h1 {
  max-width: 1050px;
  margin-bottom: 1.1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid rgba(96, 165, 250, 0.22);
  font-size: clamp(2.45rem, 3.6vw, 4.45rem);
  line-height: 1.02;
}

h2 {
  margin-top: 3rem;
  padding-top: 0.95rem;
  border-top: 1px solid rgba(96, 165, 250, 0.18);
}

a {
  color: var(--atlas-sky);
  text-decoration-thickness: 0.08em;
  text-underline-offset: 0.2em;
}

a:hover,
a:focus {
  color: var(--atlas-electric);
}

p, li, td {
  color: var(--atlas-muted);
  line-height: 1.72;
}

strong {
  color: #f8fbff;
}

.homepage img[alt='Atlas block-art platform view'] {
  display: block;
  width: min(100%, 1180px);
  margin: 1.8rem 0 2.2rem;
  border: 1px solid rgba(96, 165, 250, 0.22);
  border-radius: 8px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.38);
}

.col-md-3 .navbar-nav,
.bs-sidebar {
  font-size: 0.92rem;
}

.bs-sidebar .nav > li > a {
  color: #91a9bd;
}

.bs-sidebar .nav > li > a:hover,
.bs-sidebar .nav > li > a:focus {
  color: #f8fbff;
}

code, pre, kbd {
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

pre, code {
  background-color: #07111f;
  border: 1px solid rgba(96, 165, 250, 0.2);
  color: #d8edff;
  border-radius: 8px;
}

pre {
  padding: 1rem 1.15rem;
}

table {
  width: 100%;
  border: 1px solid rgba(96, 165, 250, 0.2);
  background: rgba(7, 17, 31, 0.76);
  border-radius: 8px;
  overflow: hidden;
}

thead th {
  background: #0b1728;
  color: #dff6ff;
  font-size: 0.82rem;
  letter-spacing: 0;
  text-transform: uppercase;
}

tbody tr:nth-child(odd) {
  background: rgba(7, 17, 31, 0.66);
}

tbody tr:nth-child(even) {
  background: rgba(2, 6, 23, 0.72);
}

blockquote {
  border-left: 4px solid var(--atlas-sky);
  color: #bdd2e4;
  background: rgba(14, 165, 233, 0.1);
  padding: 0.9rem 1rem;
}

.footer {
  color: #7890a5;
}

.modal-content {
  background: #07111f;
  border: 1px solid rgba(96, 165, 250, 0.22);
}

.form-control {
  color: #e5f4ff;
  background-color: #020617;
  border-color: rgba(96, 165, 250, 0.28);
}

@media (max-width: 992px) {
  body > .container {
    padding: 1.6rem;
  }

  .col-md-9,
  .col-md-3 {
    flex: 0 0 100%;
    max-width: 100%;
  }

  .navbar-collapse {
    max-height: 70vh;
    overflow-y: auto;
  }

  .navbar-nav {
    flex-wrap: wrap;
  }
}

@media (max-width: 767.98px) {
  body > .container {
    margin-top: 1rem;
    padding: 1.1rem;
    border-left: 0;
    border-right: 0;
    border-radius: 0;
  }

  .bs-sidebar,
  .col-md-3 {
    display: none;
  }
}

.wy-nav-content,
.md-content {
  background: transparent;
}
"""

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
