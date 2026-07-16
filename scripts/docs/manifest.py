from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


class ManifestError(ValueError):
    """Raised when the public documentation manifest is invalid."""


@dataclass(frozen=True)
class DiagramEntry:
    id: str
    master: str


@dataclass(frozen=True)
class Section:
    id: str
    number: str
    title: str
    source: str | None = None
    children: tuple[Section, ...] = ()
    diagrams: tuple[str, ...] = ()


@dataclass(frozen=True)
class Page:
    id: str
    number: str
    title: str
    source: str
    diagrams: tuple[str, ...]

    @property
    def site_path(self) -> PurePosixPath:
        source = PurePosixPath(self.source)
        if self.id == "overview":
            return PurePosixPath("index.md")
        if self.id == "services-index":
            return PurePosixPath("services", "index.md")
        if self.source == "docs/README.md":
            return PurePosixPath("documentation-map.md")
        if self.source == "docs/architecture/README.md":
            return PurePosixPath("architecture", "diagram-authoring.md")
        if self.source == "docs/diagrams/README.md":
            return PurePosixPath("diagrams", "catalog.md")
        if source.parts[:1] == ("docs",):
            return PurePosixPath(*source.parts[1:])
        if len(source.parts) == 3 and source.parts[0] == "services" and source.name == "README.md":
            return PurePosixPath("services", f"{source.parts[1]}.md")
        return PurePosixPath("reference", f"{self.id}.md")

    @property
    def wiki_path(self) -> PurePosixPath:
        if self.id == "overview":
            return PurePosixPath("Home.md")
        slug = re.sub(r"[^A-Za-z0-9]+", "-", self.title).strip("-")
        return PurePosixPath(f"{self.number}-{slug}.md")


@dataclass(frozen=True)
class Manifest:
    surfaces: tuple[str, ...]
    numbering: str
    sections: tuple[Section, ...]
    diagrams: tuple[DiagramEntry, ...]

    @property
    def pages(self) -> tuple[Page, ...]:
        pages: list[Page] = []

        def visit(sections: tuple[Section, ...]) -> None:
            for section in sections:
                if section.source:
                    pages.append(
                        Page(
                            id=section.id,
                            number=section.number,
                            title=section.title,
                            source=section.source,
                            diagrams=section.diagrams,
                        )
                    )
                visit(section.children)

        visit(self.sections)
        return tuple(pages)


def _required(mapping: dict[str, Any], key: str, context: str) -> Any:
    try:
        return mapping[key]
    except KeyError as exc:
        raise ManifestError(f"{context} is missing required key {key!r}") from exc


def _parse_section(raw: Any, context: str) -> Section:
    if not isinstance(raw, dict):
        raise ManifestError(f"{context} must be a mapping")
    source = raw.get("source")
    children_raw = raw.get("children", [])
    if not isinstance(children_raw, list):
        raise ManifestError(f"{context}.children must be a list")
    if bool(source) == bool(children_raw):
        raise ManifestError(f"{context} must define exactly one of source or children")
    children = tuple(
        _parse_section(child, f"{context}.children[{index}]")
        for index, child in enumerate(children_raw)
    )
    diagrams = raw.get("diagrams", [])
    if not isinstance(diagrams, list) or not all(isinstance(item, str) for item in diagrams):
        raise ManifestError(f"{context}.diagrams must be a list of IDs")
    return Section(
        id=str(_required(raw, "id", context)),
        number=str(_required(raw, "number", context)),
        title=str(_required(raw, "title", context)),
        source=str(source) if source else None,
        children=children,
        diagrams=tuple(diagrams),
    )


def parse_manifest(text: str) -> Manifest:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"Manifest YAML is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError("Manifest root must be a mapping")
    try:
        surfaces = tuple(str(item) for item in _required(raw, "surfaces", "manifest"))
        numbering = str(_required(raw, "numbering", "manifest"))
        sections_raw = _required(raw, "sections", "manifest")
    except TypeError as exc:
        raise ManifestError(f"Manifest has an invalid value: {exc}") from exc
    if surfaces != ("repo", "site", "wiki"):
        raise ManifestError("Manifest surfaces must be [repo, site, wiki]")
    if numbering != "baked":
        raise ManifestError("Manifest numbering must be 'baked'")
    if not isinstance(sections_raw, list) or not sections_raw:
        raise ManifestError("Manifest sections must be a non-empty list")
    sections = tuple(
        _parse_section(section, f"sections[{index}]")
        for index, section in enumerate(sections_raw)
    )
    diagrams_raw = raw.get("diagrams", [])
    if not isinstance(diagrams_raw, list):
        raise ManifestError("Manifest diagrams must be a list")
    diagrams: list[DiagramEntry] = []
    for index, diagram in enumerate(diagrams_raw):
        if not isinstance(diagram, dict):
            raise ManifestError(f"diagrams[{index}] must be a mapping")
        diagrams.append(
            DiagramEntry(
                id=str(_required(diagram, "id", f"diagrams[{index}]")),
                master=str(_required(diagram, "master", f"diagrams[{index}]")),
            )
        )
    manifest = Manifest(surfaces, numbering, sections, tuple(diagrams))
    _validate_uniqueness(manifest)
    _validate_numbering(manifest.sections)
    return manifest


def _validate_uniqueness(manifest: Manifest) -> None:
    for label, values in (
        ("page ID", [page.id for page in manifest.pages]),
        ("page number", [page.number for page in manifest.pages]),
        ("page source", [page.source for page in manifest.pages]),
        ("site path", [page.site_path.as_posix() for page in manifest.pages]),
        ("wiki path", [page.wiki_path.as_posix() for page in manifest.pages]),
        ("diagram ID", [diagram.id for diagram in manifest.diagrams]),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ManifestError(f"Duplicate {label}: {', '.join(duplicates)}")
    known_diagrams = {diagram.id for diagram in manifest.diagrams}
    missing = sorted({item for page in manifest.pages for item in page.diagrams} - known_diagrams)
    if missing:
        raise ManifestError(f"Unknown diagram IDs: {', '.join(missing)}")


def _validate_numbering(sections: tuple[Section, ...], parent: str = "") -> None:
    for index, section in enumerate(sections, start=1):
        expected = f"{parent}.{index}" if parent else str(index)
        if section.number != expected:
            raise ManifestError(
                f"Section {section.id!r} has number {section.number!r}; expected {expected}"
            )
        _validate_numbering(section.children, section.number)


def _validated_repo_path(repo_root: Path, relative: str, label: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ManifestError(f"{label} must stay within the repository: {relative}")
    path = repo_root.joinpath(*pure.parts)
    if not path.is_file():
        raise ManifestError(f"{label} does not exist: {relative}")
    return path


def load_manifest(path: Path, repo_root: Path) -> Manifest:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"Cannot read manifest {path}: {exc}") from exc
    manifest = parse_manifest(text)
    for page in manifest.pages:
        _validated_repo_path(repo_root, page.source, f"Page source {page.id}")
    for diagram in manifest.diagrams:
        _validated_repo_path(repo_root, diagram.master, f"Diagram master {diagram.id}")
    return manifest
