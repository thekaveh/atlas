from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from services.manifests import Manifest, load_manifests
from services.topology import get_topology


PUBLIC_URL = "https://thekaveh.github.io/atlas/"


@dataclass(frozen=True)
class TrackPage:
    key: str
    label: str
    description: str
    services: list[str]
    all_services: bool = False

    @property
    def services_display(self) -> str:
        if self.all_services:
            return "all services (no filtering)"
        return ", ".join(self.services) if self.services else "-"


@dataclass(frozen=True)
class SourceSurface:
    var: str
    default: str
    values: list[str]


@dataclass(frozen=True)
class EnvVarSurface:
    name: str
    default: str
    description: str


@dataclass(frozen=True)
class ServicePage:
    name: str
    title: str
    category: str
    kind: str
    readme: Path
    source_var: str
    source_default: str
    source_values: list[str]
    source_surfaces: list[SourceSurface]
    track_keys: list[str]
    required_dependencies: list[str]
    optional_dependencies: list[str]
    runtime_calls: list[str]
    kong_aliases: list[str]
    env_vars: list[EnvVarSurface]
    port_vars: list[str]
    diagram_svg: Path | None
    diagram_html: Path | None


@dataclass(frozen=True)
class DocsModel:
    root: Path
    public_url: str
    hero_image: Path
    poster_image: Path
    wizard_screenshot: Path
    top_level_diagram: Path
    services: list[ServicePage]
    tracks: list[TrackPage]

    @property
    def services_by_name(self) -> dict[str, ServicePage]:
        return {service.name: service for service in self.services}

    @property
    def tracks_by_key(self) -> dict[str, TrackPage]:
        return {track.key: track for track in self.tracks}


def _load_tracks(root: Path) -> list[TrackPage]:
    data = yaml.safe_load((root / "bootstrapper" / "tracks.yml").read_text(encoding="utf-8"))
    tracks: list[TrackPage] = []
    for row in data["tracks"]:
        services = row.get("services", [])
        all_services = services == "*"
        tracks.append(
            TrackPage(
                key=row["key"],
                label=row.get("display_name", row["key"]),
                description=row.get("description", ""),
                services=[] if all_services else list(services),
                all_services=all_services,
            )
        )
    return tracks


def _service_dirs(services_dir: Path) -> dict[str, Path]:
    return {
        path.name: path
        for path in services_dir.iterdir()
        if path.is_dir()
        and not path.name.startswith(("_", "."))
        and ((path / "service.yml").exists() or (path / "README.md").exists())
    }


def _track_membership(
    names: set[str],
    tracks: list[TrackPage],
    manifests: dict[str, Manifest],
) -> dict[str, list[str]]:
    membership: dict[str, set[str]] = {name: set() for name in names}
    all_track_keys = {track.key for track in tracks}
    curated_track_keys = {track.key for track in tracks if not track.all_services and track.services}

    for track in tracks:
        if track.all_services:
            for name in names:
                membership[name].add(track.key)
            continue
        for service in track.services:
            if service in membership:
                membership[service].add(track.key)

    for name, manifest in manifests.items():
        if manifest.sources is None or len(manifest.sources.options) <= 1:
            membership.setdefault(name, set()).update(curated_track_keys)

    return {
        name: sorted(keys) if keys else sorted(all_track_keys if name in manifests and manifests[name].virtual else [])
        for name, keys in membership.items()
    }


def _topology_lookup(services_dir: Path) -> dict[str, Any]:
    topology = get_topology(services_dir)
    aliases_by_manifest: dict[str, list[str]] = {}
    for row in topology.rows:
        if row.alias:
            aliases_by_manifest.setdefault(row.manifest, []).append(row.alias)
    return {
        name: {
            "aliases": aliases_by_manifest.get(name, []),
        }
        for name in set(topology.category_of)
    }


def _dedupe_stable(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _runtime_surface_values(manifest: Manifest, source_var: str) -> list[str]:
    stem = source_var.removesuffix("_SOURCE").lower()
    candidate_keys = (
        stem,
        stem.replace("-", "_"),
        stem.replace("_", "-"),
    )
    for key in candidate_keys:
        variants = manifest.runtime_sc.get(key)
        if isinstance(variants, dict):
            return [str(option) for option in variants.keys()]
    return []


def _source_metadata(
    manifest: Manifest | None,
) -> tuple[str, str, list[str], list[SourceSurface]]:
    if manifest is None:
        return "", "", [], []
    if manifest.sources:
        source_surfaces = [
            SourceSurface(
                var=manifest.sources.var,
                default=manifest.sources.default,
                values=[option.id for option in manifest.sources.options],
            )
        ]
    else:
        source_surfaces = [
            SourceSurface(
                var=env.name,
                default=str(env.default) if env.default is not None else "",
                values=_runtime_surface_values(manifest, env.name),
            )
            for env in manifest.env
            if env.name.endswith("_SOURCE")
        ]

    if not source_surfaces:
        return "", "", [], []

    primary_surface = source_surfaces[0]
    return (
        primary_surface.var,
        primary_surface.default,
        primary_surface.values,
        source_surfaces,
    )


def _readme_path(root: Path, services_dir: Path, name: str, manifest: Manifest | None) -> Path:
    if manifest and manifest.docs:
        return root / manifest.docs
    return services_dir / name / "README.md"


def _manifest_docs(root: Path, tracks: list[TrackPage]) -> list[ServicePage]:
    services_dir = root / "services"
    manifests = {manifest.name: manifest for manifest in load_manifests(services_dir)}
    service_dirs = _service_dirs(services_dir)
    membership = _track_membership(set(service_dirs), tracks, manifests)
    topology = _topology_lookup(services_dir)
    docs: list[ServicePage] = []

    for name in sorted(service_dirs):
        manifest = manifests.get(name)
        topological = topology.get(name, {})
        readme = _readme_path(root, services_dir, name, manifest)
        source_var, source_default, source_values, source_surfaces = _source_metadata(manifest)
        required = list(manifest.depends_on.required) if manifest else []
        optional = list(manifest.depends_on.optional) if manifest else []
        runtime_calls = list(manifest.data_flow.get("calls", [])) if manifest else []
        aliases = _dedupe_stable(
            list(topological.get("aliases", []))
            + (list(manifest.extra_kong_aliases) if manifest else [])
        )
        env_vars = (
            [
                EnvVarSurface(
                    name=env.name,
                    default="" if env.default is None else str(env.default),
                    description=env.description,
                )
                for env in manifest.env
            ]
            if manifest
            else []
        )
        port_vars = [env.name for env in manifest.env if env.name.endswith("_PORT")] if manifest else []
        diagram_svg = services_dir / name / "architecture.svg"
        diagram_html = services_dir / name / "architecture.html"

        docs.append(
            ServicePage(
                name=name,
                title=manifest.label if manifest else name,
                category=manifest.category if manifest else "aggregate",
                kind="virtual" if manifest and manifest.virtual else "container" if manifest else "doc-only",
                readme=readme,
                source_var=source_var,
                source_default=source_default,
                source_values=source_values,
                source_surfaces=source_surfaces,
                track_keys=membership.get(name, []),
                required_dependencies=required,
                optional_dependencies=optional,
                runtime_calls=runtime_calls,
                kong_aliases=aliases,
                env_vars=env_vars,
                port_vars=port_vars,
                diagram_svg=diagram_svg if diagram_svg.exists() else None,
                diagram_html=diagram_html if diagram_html.exists() else None,
            )
        )
    return docs


def load_docs_model(root: Path) -> DocsModel:
    tracks = _load_tracks(root)
    return DocsModel(
        root=root,
        public_url=PUBLIC_URL,
        hero_image=Path("assets/images/atlas-source.png"),
        poster_image=Path("assets/atlas-poster-blue.png"),
        wizard_screenshot=Path("screenshots/wizard-running.png"),
        top_level_diagram=Path("diagrams/architecture.svg"),
        services=_manifest_docs(root, tracks),
        tracks=tracks,
    )
