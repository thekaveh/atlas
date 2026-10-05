from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from docs.deps_resolver import _AGGREGATE_DOC_FOLDERS, doc_folder_to_manifests
from services.manifests import Manifest, call_edges, load_manifests
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
    support_tier: str = "n/a"


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

    # tracks.py's always-on tier ({llm-provider, prometheus, grafana}) is in
    # every track; llm-provider is the role Ollama fills.
    always_on = {"ollama", "llm-provider", "prometheus", "grafana"}
    aggregate_members = {m for members in _AGGREGATE_DOC_FOLDERS.values() for m in members}

    for track in tracks:
        if track.all_services:
            for name in names:
                membership[name].add(track.key)
            continue
        listed = set(track.services) | always_on
        for service in listed:
            # A role folder (tts-provider, doc-processor, ...) stands for the
            # manifests that implement it.
            for member in {service, *doc_folder_to_manifests(service)}:
                if member in membership:
                    membership[member].add(track.key)

    for name, manifest in manifests.items():
        if (
            manifest.virtual
            and manifest.sources is None
            and not manifest.rows
            and not manifest.env
        ):
            # Internal compatibility nodes are conditional graph structure,
            # not wizard-selectable services. They belong to the unfiltered
            # `all` view only; assigning every curated track would imply that
            # each track enables them independently.
            continue
        if name in aggregate_members:
            # No SOURCE of its own: it follows its role's track membership.
            continue
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
        # Also surface secondary SOURCE vars declared as env entries — chiefly
        # the *_INIT_SOURCE init-container selectors (COMFYUI_INIT_SOURCE,
        # HERMES_INIT_SOURCE, …) — so the canonical source-values.md matrix
        # lists EVERY *_SOURCE var, not just the primary (#836). The primary
        # (manifest.sources.var) is already surfaced above; skip it. Values
        # resolve via the same runtime_sc lookup the no-sources-block branch
        # uses (empty → "-"), never the primary's option list.
        for env in manifest.env:
            if env.name.endswith("_SOURCE") and env.name != manifest.sources.var:
                source_surfaces.append(
                    SourceSurface(
                        var=env.name,
                        default=str(env.default) if env.default is not None else "",
                        values=_runtime_surface_values(manifest, env.name),
                    )
                )
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


def _template_defaults(path: Path) -> dict[str, str]:
    """Values as written in the generated ``.env.example``.

    The template is the canonical rendering: slot-allocated ports, lower-case
    booleans and model-resolver defaults that the raw manifest default lacks.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value
    return values


def _manifest_default(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _env_surfaces(manifest: Manifest, template: dict[str, str]) -> list[EnvVarSurface]:
    surfaces = [
        EnvVarSurface(
            name=env.name,
            default=template.get(env.name, _manifest_default(env.default)),
            description=env.description,
        )
        for env in manifest.env
    ]
    # Image references are env vars in .env.example too.
    surfaces.extend(
        EnvVarSurface(
            name=image.var,
            default=template.get(image.var, image.default),
            description=image.notes or f"Container image for `{image.container}`.",
        )
        for image in manifest.images
    )
    return surfaces


def _manifest_docs(root: Path, tracks: list[TrackPage]) -> list[ServicePage]:
    services_dir = root / "services"
    manifests = {manifest.name: manifest for manifest in load_manifests(services_dir)}
    service_dirs = _service_dirs(services_dir)
    membership = _track_membership(set(service_dirs), tracks, manifests)
    topology = _topology_lookup(services_dir)
    template = _template_defaults(root / ".env.example")
    docs: list[ServicePage] = []

    for name in sorted(service_dirs):
        manifest = manifests.get(name)
        topological = topology.get(name, {})
        readme = _readme_path(root, services_dir, name, manifest)
        source_var, source_default, source_values, source_surfaces = _source_metadata(manifest)
        required = list(manifest.depends_on.required) if manifest else []
        optional = list(manifest.depends_on.optional) if manifest else []
        runtime_calls = (
            [
                edge.target if edge.status == "current"
                else f"{edge.target} ({': '.join(filter(None, (edge.status, edge.condition)))})"
                for edge in call_edges(manifest.data_flow)
            ]
            if manifest
            else []
        )
        aliases = _dedupe_stable(
            list(topological.get("aliases", []))
            + (list(manifest.extra_kong_aliases) if manifest else [])
        )
        env_vars = _env_surfaces(manifest, template) if manifest else []
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
                support_tier=manifest.support.tier if manifest else "n/a",
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
