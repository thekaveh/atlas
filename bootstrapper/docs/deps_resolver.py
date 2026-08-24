"""Manifest-graph resolver — data-flow model.

For a given focus doc-folder, walks every manifest under services/ and builds
a DepGraph whose edges come exclusively from `data_flow.calls`. The legacy
fields (depends_on.required, runtime_adaptive.adapts_to, runtime_deps.optional,
doc_extras.diagram.extra_consumers) are NOT read here — they remain in
manifests for compose orchestration but are invisible to the diagram.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from services.manifests import Manifest, load_manifests  # noqa: E402


EdgeDirection = Literal["upstream", "downstream"]


@dataclass(frozen=True, order=True)
class DepEdge:
    """One edge in the data-flow graph.

    Simpler than Phase A's DepEdge — no kind/mechanism/failure_mode, because
    we now have a single edge type ("calls"). `other_category` carries the
    target's category so the renderer can colour-code without re-loading.
    """

    other: str
    direction: EdgeDirection
    bidirectional: bool = False
    other_category: str = "external"


@dataclass(frozen=True)
class DepGraph:
    focus: str
    category: str
    port_var: str | None
    source: str
    upstream: tuple[DepEdge, ...] = ()
    downstream: tuple[DepEdge, ...] = ()
    init_containers: tuple[str, ...] = ()


# Category ordering matches services.topology.CATEGORY_ORDER.
_CATEGORY_RANK = {
    "infra": 0, "data": 1, "llm": 2, "media": 3, "agents": 4, "apps": 5,
    "external": 6,
}


def _edge_sort_key(e: DepEdge) -> tuple[int, str]:
    """Stable sort: by category-rank, then alphabetically."""
    return (_CATEGORY_RANK.get(e.other_category, 99), e.other)


def _calls_of(m: Manifest) -> list[str]:
    """Read m's data_flow.calls. Returns empty list if absent."""
    df = m.data_flow or {}
    return list(df.get("calls") or [])


# ─────────────────────────────────────────────────────────────────────────
# Doc-folder ↔ manifest mapping (spec A.7, unchanged)
# ─────────────────────────────────────────────────────────────────────────

_AGGREGATE_DOC_FOLDERS: dict[str, tuple[str, ...]] = {
    "stt-provider":   ("parakeet", "speaches"),
    "tts-provider":   ("chatterbox", "speaches", "tts-provider"),
    "doc-processor":  ("docling",),
    "multi2vec-clip": (),
}


def doc_folder_to_manifests(doc_folder: str) -> tuple[str, ...]:
    if doc_folder in _AGGREGATE_DOC_FOLDERS:
        return _AGGREGATE_DOC_FOLDERS[doc_folder]
    return (doc_folder,)


# A reverse map: which doc-folder a manifest belongs to (for inverse-pass
# edge naming). E.g. parakeet → stt-provider.
def _manifest_to_doc_folder(name: str) -> str:
    for folder, members in _AGGREGATE_DOC_FOLDERS.items():
        if name in members:
            return folder
    return name


# ─────────────────────────────────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────────────────────────────────


def build_graph(
    focus: str,
    services_root: Path,
    *,
    manifests: Iterable[Manifest] | None = None,
) -> DepGraph:
    """Build the DepGraph for a single manifest-name focus."""
    loaded = load_manifests(services_root) if manifests is None else manifests
    manifests_by_name = {m.name: m for m in loaded}
    if focus not in manifests_by_name:
        raise KeyError(f"no manifest for service '{focus}' under {services_root}")
    return _build_for_manifests(focus, [manifests_by_name[focus]], manifests_by_name)


def build_doc_graph(
    doc_folder: str,
    services_root: Path,
    *,
    manifests: Iterable[Manifest] | None = None,
) -> DepGraph:
    """Build the DepGraph for a doc folder. Folds aggregate manifests."""
    manifest_names = doc_folder_to_manifests(doc_folder)
    loaded = load_manifests(services_root) if manifests is None else manifests
    manifests_by_name = {m.name: m for m in loaded}

    if not manifest_names:
        # Pointer-only doc (e.g., multi2vec-clip)
        return DepGraph(
            focus=doc_folder,
            category="data",
            port_var=None,
            source="(pointer doc — see weaviate)",
        )

    members = [manifests_by_name[n] for n in manifest_names if n in manifests_by_name]
    if len(members) == 1 and members[0].name == doc_folder:
        return _build_for_manifests(doc_folder, members, manifests_by_name)
    # Aggregate
    return _build_for_manifests(doc_folder, members, manifests_by_name, aggregate=True)


def _build_for_manifests(
    focus: str,
    members: list[Manifest],
    all_m: dict[str, Manifest],
    *,
    aggregate: bool = False,
) -> DepGraph:
    """Common builder. `members` is one manifest for singletons, multiple for
    aggregates. Edges are derived from members' data_flow.calls (upstream)
    and from any other manifest whose data_flow.calls names the focus or any
    member (downstream)."""

    member_names = {m.name for m in members}

    # Upstream — union of each member's data_flow.calls, with intra-aggregate
    # edges suppressed. Targets are resolved to their doc-folder name where
    # possible (so a member calling 'speaches' renders as 'stt-provider' or
    # 'tts-provider' depending on context — but since 'speaches' the manifest
    # is itself the underlying for both aggregates, we keep the raw name).
    upstream: dict[str, DepEdge] = {}
    for m in members:
        for target in _calls_of(m):
            if target in member_names:
                continue  # intra-aggregate edge
            # Resolve target name: prefer the doc-folder name if it's an
            # aggregate (e.g. someone calling 'stt-provider' is calling the
            # logical service, not an underlying manifest).
            resolved = target
            if resolved not in upstream:
                upstream[resolved] = DepEdge(
                    other=resolved,
                    direction="upstream",
                    other_category=_resolve_category(resolved, all_m),
                )

    # Downstream — every other manifest whose data_flow.calls names focus,
    # any member, or the doc folder containing the focus. The last is
    # load-bearing for aggregate MEMBERS: consumers call the role name
    # (e.g. kong/n8n/open-webui/hermes call `stt-provider`, not `parakeet`),
    # so without it a member like parakeet/chatterbox/speaches/docling
    # rendered a misleading "No downstream consumers". The self-loop guard
    # below keeps the focus's own folder from being drawn as a consumer.
    downstream_keys: set[str] = {focus, *member_names, _manifest_to_doc_folder(focus)}
    downstream: dict[str, DepEdge] = {}
    for other_name, other_m in all_m.items():
        if other_name in member_names:
            continue
        for target in _calls_of(other_m):
            if target in downstream_keys:
                # Render the consumer under its doc-folder name where applicable
                rendered = _manifest_to_doc_folder(other_name)
                if rendered == focus or rendered in member_names:
                    continue  # don't draw a self-loop via doc-folder collapse
                if rendered not in downstream:
                    downstream[rendered] = DepEdge(
                        other=rendered,
                        direction="downstream",
                        other_category=_resolve_category(rendered, all_m),
                    )
                break  # one inbound edge per consumer

    # Bidirectional collapse: same name in both directions.
    both = set(upstream) & set(downstream)
    for name in both:
        u = upstream[name]
        d = downstream[name]
        upstream[name] = DepEdge(**{**u.__dict__, "bidirectional": True})
        downstream[name] = DepEdge(**{**d.__dict__, "bidirectional": True})

    # Focus metadata (use first member as canonical, or aggregate's defaults)
    if aggregate:
        category = members[0].category
        source = "(aggregate)"
        port_var = None
    else:
        me = members[0]
        category = me.category
        source = me.sources.default if me.sources else "single"
        port_var = next(
            (env.name for env in me.env
             if env.name.endswith("_PORT") or env.name.endswith("_API_PORT")),
            None,
        )

    init_containers = tuple(sorted({c for m in members for c in m.containers if c.endswith("-init")}))

    return DepGraph(
        focus=focus,
        category=category,
        port_var=port_var,
        source=source,
        upstream=tuple(sorted(upstream.values(), key=_edge_sort_key)),
        downstream=tuple(sorted(downstream.values(), key=_edge_sort_key)),
        init_containers=init_containers,
    )


def _resolve_category(name: str, all_m: dict[str, Manifest]) -> str:
    """Lookup category for a target name. Handles three cases:
       - Doc-folder name that's also a manifest name (1:1): use that manifest.
       - Aggregate doc-folder name: use the first underlying manifest's category.
       - Pure manifest name (e.g. underlying manifest for an aggregate, or
         a virtual manifest like cloud-providers): look up directly.
    """
    if name in _AGGREGATE_DOC_FOLDERS:
        members = _AGGREGATE_DOC_FOLDERS[name]
        if members and members[0] in all_m:
            return all_m[members[0]].category
        return "data"  # fallback for pointer-only docs
    if name in all_m:
        return all_m[name].category
    return "external"
