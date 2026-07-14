from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from .build_docs import build
from .canonical_references import sync_canonical_references
from .links import find_links, is_forbidden
from .manifest import Manifest, load_manifest


_INTERNAL_DIRS = {"research", "strategy", "maintenance", "superpowers"}
_PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD|FIXME|XXX)\b")


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    message: str
    surface: str = "repo"


def check_self_containment(repo_root: Path, generated_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    repo_inputs = [repo_root / "README.md"]
    docs_root = repo_root / "docs"
    if docs_root.is_dir():
        repo_inputs.extend(
            path for path in sorted(docs_root.rglob("*.md"))
            if not _is_internal_doc(path, docs_root)
        )
    services_root = repo_root / "services"
    if services_root.is_dir():
        repo_inputs.extend(sorted(services_root.glob("*/README.md")))
    inputs = [("repo", path) for path in repo_inputs]
    for surface in ("site", "wiki"):
        inputs.extend((surface, path) for path in sorted((generated_root / surface).rglob("*.md")))
    for surface, path in inputs:
        if not path.is_file():
            continue
        for link in find_links(path.read_text(encoding="utf-8")):
            if is_forbidden(link.target, surface):
                findings.append(
                    Finding(
                        severity="error",
                        path=path.relative_to(repo_root).as_posix(),
                        message=f"forbidden cross-surface link: {link.target}",
                        surface=surface,
                    )
                )
    return findings


def _is_internal_doc(path: Path, docs_root: Path) -> bool:
    relative = path.relative_to(docs_root)
    return bool(relative.parts and relative.parts[0] in _INTERNAL_DIRS)


def check_completeness(manifest: Manifest, repo_root: Path) -> list[Finding]:
    docs_root = repo_root / "docs"
    declared = {page.source for page in manifest.pages}
    findings: list[Finding] = []
    for path in sorted(docs_root.rglob("*.md")):
        relative = path.relative_to(repo_root).as_posix()
        if _is_internal_doc(path, docs_root) or relative in declared:
            continue
        findings.append(Finding("error", relative, "public Markdown is not declared in docs/manifest.yaml"))
    services_root = repo_root / "services"
    if services_root.is_dir():
        for path in sorted(services_root.glob("*/README.md")):
            relative = path.relative_to(repo_root).as_posix()
            if relative not in declared:
                findings.append(
                    Finding("error", relative, "service README is not declared in docs/manifest.yaml")
                )
    return findings


def check_placeholders(repo_root: Path) -> list[Finding]:
    docs_root = repo_root / "docs"
    findings: list[Finding] = []
    for path in sorted(docs_root.rglob("*.md")):
        if _is_internal_doc(path, docs_root):
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PLACEHOLDER_RE.search(line):
                findings.append(
                    Finding(
                        "error",
                        path.relative_to(repo_root).as_posix(),
                        f"placeholder at line {line_number}",
                    )
                )
    return findings


def check(repo_root: Path, manifest_path: Path) -> list[Finding]:
    manifest = load_manifest(manifest_path, repo_root)
    build(manifest_path, repo_root, site=True, wiki=True, check=True)
    canonical_drift = [
        Finding(
            "error",
            path.relative_to(repo_root).as_posix(),
            "manifest-derived canonical page is stale; run make docs-build",
        )
        for path in sync_canonical_references(repo_root, check=True)
    ]
    return [
        *canonical_drift,
        *check_self_containment(repo_root, repo_root / "generated"),
        *check_completeness(manifest, repo_root),
        *check_placeholders(repo_root),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Atlas documentation contracts")
    parser.add_argument("--manifest", default="docs/manifest.yaml")
    args = parser.parse_args()
    root = Path.cwd()
    findings = check(root, root / args.manifest)
    if findings:
        for finding in findings:
            print(f"{finding.severity.upper()} {finding.path}: {finding.message}")
        raise SystemExit(1)
    print("PASS three-surface documentation contracts")


if __name__ == "__main__":
    main()
