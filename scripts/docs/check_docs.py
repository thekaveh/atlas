from __future__ import annotations

import argparse
import posixpath
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import yaml

from scripts.bounded_subprocess import (
    CommandLaunchError,
    CommandOutputTooLarge,
    CommandTimedOut,
    run_bounded,
)

from .build_docs import build
from .canonical_references import sync_canonical_references
from .links import find_links, is_forbidden, navigable_link_targets
from .manifest import Manifest, load_manifest


_INTERNAL_DIRS = {"research", "strategy", "maintenance", "superpowers"}
_PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD|FIXME|XXX)\b")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_MARKDOWN_ATTR_LIST_RE = re.compile(r"\{:\s+[^}\n]+\}")


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
            path for path in _tracked_docs(repo_root)
            if not _is_internal_doc(path, docs_root)
        )
    services_root = repo_root / "services"
    if services_root.is_dir():
        repo_inputs.extend(_tracked_service_readmes(repo_root))
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


def check_wiki_links(repo_root: Path, wiki_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    resolved_root = wiki_root.resolve()
    for path in sorted(wiki_root.rglob("*.md")):
        markdown = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(markdown.splitlines(), 1):
            if _MARKDOWN_ATTR_LIST_RE.search(line):
                findings.append(
                    Finding(
                        severity="error",
                        path=path.relative_to(repo_root).as_posix(),
                        message=f"residual MkDocs attribute list at line {line_number}",
                        surface="wiki",
                    )
                )
        for link in find_links(markdown):
            raw_target = link.target.strip("<>")
            target = unquote(raw_target.partition("#")[0])
            if not target or _SCHEME_RE.match(target) or target.startswith("//"):
                continue
            if not link.is_image and target.lower().endswith(".md"):
                findings.append(
                    Finding(
                        severity="error",
                        path=path.relative_to(repo_root).as_posix(),
                        message=f"wiki page links must be extensionless: {raw_target}",
                        surface="wiki",
                    )
                )
                continue
            candidate = (path.parent / target).resolve()
            try:
                candidate.relative_to(resolved_root)
            except ValueError:
                exists = False
            else:
                alternatives = [candidate, Path(f"{candidate}.md")]
                exists = any(item.is_file() for item in alternatives)
            if not exists:
                findings.append(
                    Finding(
                        severity="error",
                        path=path.relative_to(repo_root).as_posix(),
                        message=f"missing local wiki target: {raw_target}",
                        surface="wiki",
                    )
                )
    return findings


def _canonical_page_target(
    source: str,
    raw_target: str,
    known_sources: set[str],
) -> str | None:
    """Resolve one local Markdown link to a manifest-owned canonical source."""
    target = unquote(raw_target.strip("<>").partition("#")[0]).partition("?")[0]
    if (
        not target
        or _SCHEME_RE.match(target)
        or target.startswith(("/", "//"))
    ):
        return None
    candidate = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    if candidate == ".." or candidate.startswith("../"):
        return None
    candidates = (candidate, f"{candidate}.md", f"{candidate}/index.md")
    return next((item for item in candidates if item in known_sources), None)


def _nav_targets(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        children = value
    elif isinstance(value, dict):
        children = value.values()
    else:
        children = ()
    targets: set[str] = set()
    for child in children:
        targets.update(_nav_targets(child))
    return targets


def _is_safe_file(root: Path, relative: Path) -> bool:
    if relative.is_absolute() or ".." in relative.parts or root.is_symlink():
        return False
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return False
    try:
        current.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return current.is_file()


def check_manifest_reachability(
    manifest: Manifest,
    repo_root: Path,
    generated_root: Path,
) -> list[Finding]:
    """Require every manifest page to be discoverable on all three surfaces."""
    pages_by_id = {page.id: page for page in manifest.pages}
    pages_by_source = {page.source: page for page in manifest.pages}
    known_sources = set(pages_by_source)
    root_page = pages_by_id[manifest.index_id]
    reachable = {root_page.source}
    pending = deque([root_page.source])
    while pending:
        source = pending.popleft()
        markdown = (repo_root / source).read_text(encoding="utf-8")
        for raw_target in navigable_link_targets(markdown):
            target = _canonical_page_target(
                source, raw_target, known_sources
            )
            if target is not None and target not in reachable:
                reachable.add(target)
                pending.append(target)

    findings = [
        Finding(
            "error",
            page.source,
            f"manifest page is not reachable from {root_page.source}",
            "repo",
        )
        for page in manifest.pages
        if page.source not in reachable
    ]

    mkdocs_path = repo_root / "mkdocs.yml"
    site_nav: set[str] = set()
    if _is_safe_file(repo_root, Path("mkdocs.yml")):
        config = yaml.safe_load(mkdocs_path.read_text(encoding="utf-8"))
        if isinstance(config, dict):
            site_nav = _nav_targets(config.get("nav", []))
    site_root = generated_root / "site"
    for page in manifest.pages:
        output = page.site_path.as_posix()
        if output not in site_nav or not _is_safe_file(site_root, Path(page.site_path)):
            findings.append(
                Finding(
                    "error",
                    (Path("generated/site") / page.site_path).as_posix(),
                    "manifest page is missing from the generated site navigation",
                    "site",
                )
            )

    wiki_root = generated_root / "wiki"
    sidebar_path = wiki_root / "_Sidebar.md"
    wiki_routes: set[str] = set()
    if _is_safe_file(wiki_root, Path("_Sidebar.md")):
        wiki_routes = {
            link.target.strip("<>").partition("#")[0].removesuffix(".md")
            for link in find_links(sidebar_path.read_text(encoding="utf-8"))
            if not link.is_image
        }
    for page in manifest.pages:
        route = page.wiki_path.stem
        if route not in wiki_routes or not _is_safe_file(wiki_root, Path(page.wiki_path)):
            findings.append(
                Finding(
                    "error",
                    (Path("generated/wiki") / page.wiki_path).as_posix(),
                    "manifest page is missing from the generated wiki sidebar",
                    "wiki",
                )
            )
    return findings


def _is_internal_doc(path: Path, docs_root: Path) -> bool:
    relative = path.relative_to(docs_root)
    return bool(relative.parts and relative.parts[0] in _INTERNAL_DIRS)


def _tracked_service_readmes(repo_root: Path) -> list[Path]:
    try:
        result = run_bounded(
            ["git", "ls-files", "--", "services/**"], cwd=repo_root
        )
    except CommandLaunchError:
        result = None
    except CommandTimedOut as exc:
        raise RuntimeError("Tracked service documentation inventory timed out") from exc
    except CommandOutputTooLarge as exc:
        raise RuntimeError("Tracked service documentation inventory was too large") from exc
    if result is None or result.returncode != 0:
        services_root = repo_root / "services"
        return sorted(services_root.rglob("README.md")) if services_root.is_dir() else []
    relative_paths = result.stdout.splitlines()
    return [
        repo_root / relative
        for relative in relative_paths
        if Path(relative).name == "README.md" and (repo_root / relative).is_file()
    ]


def _tracked_docs(repo_root: Path) -> list[Path]:
    """Return git-tracked Markdown under docs/, matching what a fresh CI
    checkout sees. Untracked/gitignored docs (e.g. local planning notes under
    docs/plans/) must not trip the completeness/self-containment/placeholder
    gates the way a raw filesystem walk would. Falls back to a filesystem walk
    when git is unavailable, mirroring ``_tracked_service_readmes``.
    """
    docs_root = repo_root / "docs"
    try:
        result = run_bounded(["git", "ls-files", "--", "docs"], cwd=repo_root)
    except CommandLaunchError:
        result = None
    except CommandTimedOut as exc:
        raise RuntimeError("Tracked documentation inventory timed out") from exc
    except CommandOutputTooLarge as exc:
        raise RuntimeError("Tracked documentation inventory was too large") from exc
    if result is None or result.returncode != 0:
        return sorted(docs_root.rglob("*.md")) if docs_root.is_dir() else []
    return sorted(
        repo_root / relative
        for relative in result.stdout.splitlines()
        if relative.endswith(".md") and (repo_root / relative).is_file()
    )


def check_completeness(manifest: Manifest, repo_root: Path) -> list[Finding]:
    docs_root = repo_root / "docs"
    declared = {page.source for page in manifest.pages}
    findings: list[Finding] = []
    for path in _tracked_docs(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        if _is_internal_doc(path, docs_root) or relative in declared:
            continue
        findings.append(Finding("error", relative, "public Markdown is not declared in docs/manifest.yaml"))
    services_root = repo_root / "services"
    if services_root.is_dir():
        for path in _tracked_service_readmes(repo_root):
            relative = path.relative_to(repo_root).as_posix()
            if relative not in declared:
                findings.append(
                    Finding("error", relative, "service README is not declared in docs/manifest.yaml")
                )
    return findings


def check_placeholders(repo_root: Path) -> list[Finding]:
    docs_root = repo_root / "docs"
    findings: list[Finding] = []
    for path in _tracked_docs(repo_root):
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
    # PRODUCE the tree we are about to validate. `build(..., check=True)`
    # above renders into its own TemporaryDirectory (that call is the
    # determinism check) and returns without writing `generated/`, so pointing
    # the surface checks at `generated/` validated whatever happened to be
    # lying there — nothing at all on a clean checkout, which made both globs
    # match zero files and the gate print PASS having checked neither surface.
    # CI only escaped it because the Makefile happens to run a full
    # `build_docs` first, an ordering dependency expressed nowhere here and
    # not honoured by `check_site.py`.
    #
    # `build` (not bare render_site/render_wiki) because the surfaces also
    # need their diagram assets — without those, the wiki-link check reports
    # every embedded image as a missing target.
    build(manifest_path, repo_root, site=True, wiki=True, check=False)
    rendered = repo_root / "generated"
    surface_findings = [
        *check_self_containment(repo_root, rendered),
        *check_wiki_links(repo_root, rendered / "wiki"),
        *check_manifest_reachability(manifest, repo_root, rendered),
    ]

    return [
        *canonical_drift,
        *surface_findings,
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
