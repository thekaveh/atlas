"""Load the compiled RAG ingestion profiles the bootstrapper generates (#413).

The bootstrapper validates + normalizes the consumer manifest's
``rag_ingestion_profiles`` into a single JSON file and bind-mounts it into the
backend at ``RAG_INGESTION_PROFILES_FILE``. The backend only READS it (all
validation already happened at load time), resolving a profile by name.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class ProfileNotFoundError(KeyError):
    """Raised when a submit references an unknown profile name."""


@dataclass(frozen=True)
class LoadedProfile:
    consumer: str
    name: str
    revision: str
    corpus: Dict[str, Any]
    parser_order: List[str]
    chunker: Dict[str, Any]
    vector_targets: List[Dict[str, Any]]
    graph_targets: List[Dict[str, Any]]

    def to_dict(self, *, corpus: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "consumer": self.consumer,
            "name": self.name,
            "revision": self.revision,
            "corpus": dict(self.corpus if corpus is None else corpus),
            "parser_order": list(self.parser_order),
            "chunker": dict(self.chunker),
            "vector_targets": [dict(target) for target in self.vector_targets],
            "graph_targets": [dict(target) for target in self.graph_targets],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoadedProfile":
        return cls(
            consumer=str(data["consumer"]),
            name=str(data["name"]),
            revision=str(data.get("revision", "")),
            corpus=dict(data.get("corpus") or {}),
            parser_order=list(data.get("parser_order") or ["plain_text"]),
            chunker=dict(data.get("chunker") or {}),
            vector_targets=list(data.get("vector_targets") or []),
            graph_targets=list(data.get("graph_targets") or []),
        )


def _profiles_path() -> Optional[str]:
    return os.getenv("RAG_INGESTION_PROFILES_FILE")


def load_profiles(path: Optional[str] = None) -> List[LoadedProfile]:
    path = path or _profiles_path()
    if not path or not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    if not isinstance(doc, dict):
        return []
    return [LoadedProfile.from_dict(p) for p in doc.get("profiles", [])]


def get_profile(name: str, path: Optional[str] = None) -> LoadedProfile:
    for profile in load_profiles(path):
        if profile.name == name:
            return profile
    raise ProfileNotFoundError(name)
