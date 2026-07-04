"""Backend-only Graphiti experiment configuration.

This module deliberately does not import ``graphiti_core``. The current Atlas
slice is a disabled-by-default evaluation scaffold: it defines the namespace
contract and exposes status metadata without adding Graphiti to backend startup.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from uuid import UUID


GROUP_ID_PATTERN = "atlas:<project>:backend:<namespace>:user:<uuid>"

_PATHLIKE = re.compile(r"[./\\]")
_MULTI_HYPHEN = re.compile(r"-+")
_SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _slug(value: str, *, name: str) -> str:
    """Normalize a human label into a strict Graphiti namespace segment."""
    raw = (value or "").strip().lower()
    if not raw:
        raise ValueError(f"{name} must not be empty")
    if _PATHLIKE.search(raw):
        raise ValueError(f"{name} must not contain path separators or dots")
    normalized = re.sub(r"[^a-z0-9]+", "-", raw)
    normalized = _MULTI_HYPHEN.sub("-", normalized).strip("-")
    if not normalized or not _SAFE_SLUG.fullmatch(normalized):
        raise ValueError(
            f"{name} must normalize to 1-64 lowercase letters, numbers, or hyphens"
        )
    return normalized


def build_graphiti_group_id(
    *,
    user_id: str,
    namespace: str = "langmem",
    project_name: str | None = None,
    prefix: str | None = None,
) -> str:
    """Build Atlas' strict Graphiti group_id for backend memory experiments.

    Shape: ``atlas:<project>:backend:<namespace>:user:<uuid>``.
    """
    try:
        user_uuid = UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("user_id must be a valid UUID") from exc

    prefix_slug = _slug(prefix or os.getenv("GRAPHITI_GROUP_ID_PREFIX", "atlas"), name="prefix")
    project_slug = _slug(project_name or os.getenv("PROJECT_NAME", "atlas"), name="project")
    namespace_slug = _slug(namespace, name="namespace")
    return f"{prefix_slug}:{project_slug}:backend:{namespace_slug}:user:{user_uuid}"


@dataclass(frozen=True)
class GraphitiExperimentConfig:
    """Runtime view of the disabled-by-default Graphiti experiment."""

    enabled: bool
    group_id_prefix: str
    default_namespace: str
    llm_model: str
    embedding_model: str
    neo4j_uri: str
    expose_to_agents: bool

    @property
    def backend_only(self) -> bool:
        return not self.expose_to_agents

    @classmethod
    def from_env(cls) -> "GraphitiExperimentConfig":
        return cls(
            enabled=_env_bool("GRAPHITI_ENABLED", False),
            group_id_prefix=_slug(
                os.getenv("GRAPHITI_GROUP_ID_PREFIX", "atlas"),
                name="GRAPHITI_GROUP_ID_PREFIX",
            ),
            default_namespace=_slug(
                os.getenv("GRAPHITI_DEFAULT_NAMESPACE", "langmem"),
                name="GRAPHITI_DEFAULT_NAMESPACE",
            ),
            llm_model=os.getenv("GRAPHITI_LLM_MODEL")
            or os.getenv("LANGMEM_EXTRACTION_MODEL")
            or os.getenv("LITELLM_DEFAULT_MODEL", ""),
            embedding_model=os.getenv("GRAPHITI_EMBEDDING_MODEL")
            or os.getenv("LANGMEM_EMBEDDING_MODEL")
            or os.getenv("LITELLM_EMBEDDING_MODEL", ""),
            neo4j_uri=os.getenv("NEO4J_URI", ""),
            expose_to_agents=_env_bool("GRAPHITI_EXPOSE_TO_AGENTS", False),
        )

    def status_payload(self) -> dict:
        return {
            "enabled": self.enabled,
            "backend_only": self.backend_only,
            "group_id_pattern": GROUP_ID_PATTERN,
            "group_id_prefix": self.group_id_prefix,
            "default_namespace": self.default_namespace,
            "llm_model": self.llm_model,
            "embedding_model": self.embedding_model,
            "neo4j_configured": bool(self.neo4j_uri),
            "agent_exposure": {
                "hermes": self.expose_to_agents,
                "openclaw": self.expose_to_agents,
            },
        }
