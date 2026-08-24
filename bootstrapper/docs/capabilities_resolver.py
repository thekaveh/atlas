"""Resolve typed manifest capability contracts for service README pages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .deps_resolver import doc_folder_to_manifests

from services.manifests import Manifest  # noqa: E402


CAPABILITY_SECTION_EXCEPTIONS = frozenset({"multi2vec-clip"})


@dataclass(frozen=True)
class CapabilityRow:
    """One rendered capability row and its source manifest."""

    service: str
    capability: str
    status: str
    verification: str
    notes: str


def capability_section_enabled(doc_name: str) -> bool:
    """Whether a README represents a capability-bearing Atlas service role."""
    return doc_name not in CAPABILITY_SECTION_EXCEPTIONS


def is_aggregate_capability_doc(doc_name: str) -> bool:
    """Return whether ``doc_name`` renders member contracts as an aggregate."""
    members = doc_folder_to_manifests(doc_name)
    return bool(members) and members != (doc_name,)


def resolve_capability_rows(
    doc_name: str,
    manifests: Iterable[Manifest],
) -> tuple[CapabilityRow, ...]:
    """Resolve rows in aggregate-member and manifest declaration order."""
    manifests_by_name = {manifest.name: manifest for manifest in manifests}
    member_names = tuple(dict.fromkeys(doc_folder_to_manifests(doc_name)))

    rows: list[CapabilityRow] = []
    for member_name in member_names:
        manifest = manifests_by_name.get(member_name)
        if manifest is None:
            continue
        rows.extend(
            CapabilityRow(
                service=manifest.name,
                capability=capability.name,
                status=capability.status,
                verification=capability.verification,
                notes=capability.note,
            )
            for capability in manifest.capabilities
        )
    return tuple(rows)
