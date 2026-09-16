"""Product-facing completeness contract for critical documentation pages (#1052).

Deterministic generation and a strict build can pass while a page that a
reader needs is absent, unreachable, or reduced to a bare title. The contract
in ``docs/critical-pages.yaml`` names those pages by manifest id and the
sections each must render with a body on every surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .links import REPO_URL, SITE_URL, WIKI_URL
from .manifest import Manifest, Page


_SCHEME_RE = re.compile(r"^https?://")
# Repository paths that render documentation: file views and the repo root.
# Community destinations under the same host (issues, security advisories,
# discussions) are not documentation surfaces.
_REPO_DOC_PATH_RE = re.compile(rf"^{re.escape(REPO_URL)}(?:/?$|/(?:blob|tree|raw)(?:/|$))")
_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(?:\d+(?:\.\d+)*\.[ \t]+)?(?P<title>.+?)\s*$")


class ContractError(ValueError):
    """Raised when docs/critical-pages.yaml is malformed."""


@dataclass(frozen=True)
class CriticalPage:
    id: str
    role: str
    required_sections: tuple[str, ...]


@dataclass(frozen=True)
class Contract:
    pages: tuple[CriticalPage, ...]
    external_references: tuple[str, ...]


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    message: str
    surface: str = "repo"


@dataclass(frozen=True)
class SurfaceRoots:
    """Where the canonical tree and the rendered site/wiki projections live."""

    repo_root: Path
    generated_root: Path


CONTRACT_PATH = "docs/critical-pages.yaml"


def _string_list(value: Any, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ContractError(f"{context} must be a non-empty list of strings")
    return tuple(item.strip() for item in value)


def _parse_page(raw: Any, context: str) -> CriticalPage:
    if not isinstance(raw, dict):
        raise ContractError(f"{context} must be a mapping")
    page_id = raw.get("id")
    if not isinstance(page_id, str) or not page_id.strip():
        raise ContractError(f"{context}.id must be a manifest page id")
    if _SCHEME_RE.match(page_id) or "/" in page_id:
        raise ContractError(
            f"{context}.id must name a self-contained manifest page, not a URL or path"
        )
    role = raw.get("role")
    if not isinstance(role, str) or not role.strip():
        raise ContractError(f"{context}.role is required")
    return CriticalPage(
        id=page_id.strip(),
        role=role.strip(),
        required_sections=_string_list(raw.get("required_sections"), f"{context}.required_sections"),
    )


def _is_documentation_surface(url: str) -> bool:
    normalized = url.rstrip("/")
    for surface_url in (SITE_URL, WIKI_URL):
        if normalized == surface_url or normalized.startswith(f"{surface_url}/"):
            return True
    return bool(_REPO_DOC_PATH_RE.match(url))


def _parse_reference(raw: Any, context: str) -> str:
    if not isinstance(raw, str) or not _SCHEME_RE.match(raw):
        raise ContractError(f"{context} must be an absolute http(s) URL")
    if _is_documentation_surface(raw):
        raise ContractError(
            f"{context} points at an Atlas documentation surface; declare a page instead"
        )
    return raw


def _parse_pages(raw: Any) -> tuple[CriticalPage, ...]:
    if not isinstance(raw, list) or not raw:
        raise ContractError("Contract pages must be a non-empty list")
    pages = tuple(_parse_page(item, f"pages[{index}]") for index, item in enumerate(raw))
    ids = [page.id for page in pages]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ContractError(f"Duplicate critical page ids: {', '.join(duplicates)}")
    return pages


def _parse_references(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ContractError("Contract external_references must be a list")
    return tuple(
        _parse_reference(item, f"external_references[{index}]")
        for index, item in enumerate(raw)
    )


def _load_yaml_mapping(text: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractError(f"Contract YAML is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ContractError("Contract root must be a mapping")
    return raw


def parse_contract(text: str) -> Contract:
    raw = _load_yaml_mapping(text)
    return Contract(
        pages=_parse_pages(raw.get("pages")),
        external_references=_parse_references(raw.get("external_references", [])),
    )


def load_contract(path: Path) -> Contract:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"Cannot read contract {path}: {exc}") from exc
    return parse_contract(text)


def section_has_body(markdown: str, title: str) -> bool:
    """True when ``title`` is a heading followed by at least one content line."""
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match is None or match.group("title") != title:
            continue
        for following in lines[index + 1 :]:
            if _HEADING_RE.match(following):
                return False
            if following.strip():
                return True
        return False
    return False


def _surface_texts(page: Page, roots: SurfaceRoots) -> dict[str, Path]:
    return {
        "repo": roots.repo_root / page.source,
        "site": roots.generated_root / "site" / page.site_path,
        "wiki": roots.generated_root / "wiki" / page.wiki_path,
    }


def _relative_label(path: Path, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix() if path.is_relative_to(repo_root) else path.as_posix()


def _section_findings(critical: CriticalPage, page: Page, roots: SurfaceRoots) -> list[Finding]:
    findings: list[Finding] = []
    for surface, path in _surface_texts(page, roots).items():
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        relative = _relative_label(path, roots.repo_root)
        if not text:
            findings.append(Finding("error", relative, f"critical {critical.role} page is missing", surface))
            continue
        for title in critical.required_sections:
            if not section_has_body(text, title):
                findings.append(
                    Finding(
                        "error",
                        relative,
                        f"critical {critical.role} page lacks a substantive section {title!r}",
                        surface,
                    )
                )
    return findings


def check_critical_pages(
    contract: Contract,
    manifest: Manifest,
    reachable_sources: set[str],
    roots: SurfaceRoots,
) -> list[Finding]:
    """Require every critical page to be declared, reachable, and substantive."""
    pages_by_id = {page.id: page for page in manifest.pages}
    findings: list[Finding] = []
    for critical in contract.pages:
        page = pages_by_id.get(critical.id)
        if page is None:
            findings.append(
                Finding(
                    "error",
                    CONTRACT_PATH,
                    f"critical {critical.role} page {critical.id!r} is not declared in docs/manifest.yaml",
                )
            )
            continue
        if page.source not in reachable_sources:
            findings.append(
                Finding(
                    "error",
                    page.source,
                    f"critical {critical.role} page is not reachable from the documentation index",
                )
            )
        findings.extend(_section_findings(critical, page, roots))
    return findings
