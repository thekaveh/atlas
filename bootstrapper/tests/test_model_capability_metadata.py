"""Acceptance coverage for model capability metadata (#417)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from utils import llm_catalog


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "bootstrapper/schemas/models.schema.json").read_text(encoding="utf-8")
)


def _load_flat_catalog(tmp_path: Path, payload: dict) -> list[llm_catalog.CatalogEntry]:
    catalog_dir = tmp_path / "ollama"
    catalog_dir.mkdir(parents=True)
    (catalog_dir / "models.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    return llm_catalog._load_ollama_catalog(tmp_path)


def _load_cloud_catalog(tmp_path: Path, payload: dict) -> list[llm_catalog.CatalogEntry]:
    catalog_dir = tmp_path / "litellm"
    catalog_dir.mkdir(parents=True)
    (catalog_dir / "models.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    return llm_catalog._load_cloud_catalog(tmp_path)


def test_schema_accepts_versioned_capability_metadata_and_legacy_entries() -> None:
    jsonschema.validate(
        {
            "content": [
                {
                    "name": "explicit-chat",
                    "metadata_version": 1,
                    "kind": "chat",
                    "adapter": "ollama_chat",
                    "capabilities": {
                        "chat": True,
                        "tools": True,
                        "reasoning": False,
                        "structured_output": True,
                    },
                    "request_defaults": {"think": False},
                    "recommended_roles": ["extract", "keyword", "query"],
                }
            ]
        },
        SCHEMA,
    )
    jsonschema.validate({"content": [{"name": "legacy-chat"}]}, SCHEMA)


@pytest.mark.parametrize(
    "entry",
    [
        {
            "name": "bad-adapter",
            "metadata_version": 1,
            "kind": "chat",
            "adapter": "unknown",
        },
        {
            "name": "bad-role",
            "metadata_version": 1,
            "kind": "chat",
            "recommended_roles": ["summon"],
        },
        {
            "name": "missing-dimension",
            "metadata_version": 1,
            "kind": "embedding",
            "adapter": "ollama",
        },
        {
            "name": "embedding-with-chat-default",
            "metadata_version": 1,
            "kind": "embedding",
            "adapter": "ollama",
            "dim": 768,
            "request_defaults": {"think": False},
        },
        {
            "name": "embedding-with-chat-adapter",
            "metadata_version": 1,
            "kind": "embedding",
            "adapter": "ollama_chat",
            "dim": 768,
        },
    ],
)
def test_schema_rejects_invalid_or_contradictory_metadata(entry: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"embeddings": [entry]}, SCHEMA)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "content": [
                {
                    "name": "ollama-wrong-adapter",
                    "metadata_version": 1,
                    "kind": "chat",
                    "adapter": "anthropic",
                }
            ]
        },
        {
            "openai": {
                "content": [
                    {
                        "name": "openai-wrong-adapter",
                        "metadata_version": 1,
                        "kind": "chat",
                        "adapter": "anthropic",
                    }
                ]
            }
        },
        {
            "anthropic": {
                "content": [
                    {
                        "name": "anthropic-wrong-adapter",
                        "metadata_version": 1,
                        "kind": "chat",
                        "adapter": "openai",
                    }
                ]
            }
        },
        {
            "openrouter": {
                "content": [
                    {
                        "name": "openrouter-wrong-adapter",
                        "metadata_version": 1,
                        "kind": "chat",
                        "adapter": "openai",
                    }
                ]
            }
        },
    ],
)
def test_schema_rejects_adapter_for_wrong_implied_provider(payload: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "content": [
                {
                    "name": "implicit-chat-with-embedding-capability",
                    "metadata_version": 1,
                    "capabilities": {"embedding": True},
                }
            ]
        },
        {
            "content": [
                {
                    "name": "implicit-chat-disabled",
                    "metadata_version": 1,
                    "capabilities": {"chat": False},
                }
            ]
        },
        {
            "embeddings": [
                {
                    "name": "implicit-embedding-with-chat-capability",
                    "metadata_version": 1,
                    "adapter": "ollama",
                    "dim": 768,
                    "capabilities": {"chat": True},
                }
            ]
        },
    ],
)
def test_schema_rejects_capabilities_contradicting_implied_section_kind(
    payload: dict,
) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)


def test_loader_merges_duplicate_multi_role_metadata() -> None:
    qwen = next(entry for entry in llm_catalog.ollama_entries() if entry.name == "qwen3.6:latest")
    assert qwen.metadata_version == 1
    assert qwen.kind == "chat"
    assert qwen.adapter == "ollama_chat"
    assert qwen.capabilities["vision"] is True
    assert qwen.capabilities["reasoning"] is True
    assert qwen.capabilities["structured_output"] is True
    assert qwen.request_defaults == {"think": False}
    assert {"extract", "keyword", "query", "judge", "vision"} <= set(qwen.recommended_roles)


def test_loader_rejects_conflicting_duplicate_metadata(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="conflicting kind"):
        _load_flat_catalog(
            tmp_path,
            {
                "content": [
                    {
                        "name": "same-model",
                        "metadata_version": 1,
                        "kind": "chat",
                    }
                ],
                "vision": [
                    {
                        "name": "same-model",
                        "metadata_version": 1,
                        "kind": "embedding",
                        "dim": 1024,
                    }
                ],
            },
        )


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            {
                "name": "missing-dimension",
                "metadata_version": 1,
                "kind": "embedding",
                "adapter": "ollama",
            },
            "requires dim",
        ),
        (
            {
                "name": "embedding-with-chat-default",
                "metadata_version": 1,
                "kind": "embedding",
                "adapter": "ollama",
                "dim": 768,
                "request_defaults": {"think": False},
            },
            "request_defaults",
        ),
        (
            {
                "name": "embedding-with-chat-adapter",
                "metadata_version": 1,
                "kind": "embedding",
                "adapter": "ollama_chat",
                "dim": 768,
            },
            "adapter",
        ),
    ],
)
def test_loader_enforces_runtime_metadata_invariants(
    tmp_path: Path,
    entry: dict,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _load_flat_catalog(tmp_path, {"embeddings": [entry]})


def test_loader_rejects_provider_adapter_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="provider openai requires adapter openai"):
        _load_cloud_catalog(
            tmp_path,
            {
                "openai": {
                    "content": [
                        {
                            "name": "wrong-provider-adapter",
                            "metadata_version": 1,
                            "kind": "chat",
                            "adapter": "anthropic",
                        }
                    ]
                }
            },
        )


@pytest.mark.parametrize(
    ("section", "entry", "message"),
    [
        (
            "content",
            {
                "name": "implicit-chat-with-embedding-capability",
                "metadata_version": 1,
                "capabilities": {"embedding": True},
            },
            "contradictory chat metadata",
        ),
        (
            "content",
            {
                "name": "implicit-chat-disabled",
                "metadata_version": 1,
                "capabilities": {"chat": False},
            },
            "contradictory chat metadata",
        ),
        (
            "embeddings",
            {
                "name": "implicit-embedding-with-chat-capability",
                "metadata_version": 1,
                "adapter": "ollama",
                "dim": 768,
                "capabilities": {"chat": True},
            },
            "contradictory embedding metadata",
        ),
    ],
)
def test_loader_rejects_capabilities_contradicting_inferred_kind(
    tmp_path: Path,
    section: str,
    entry: dict,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _load_flat_catalog(tmp_path, {section: [entry]})


def test_non_embed_named_embedding_is_explicit_and_dimensioned() -> None:
    bge = next(entry for entry in llm_catalog.ollama_entries() if entry.name == "bge-m3")
    assert "embed" not in bge.name
    assert bge.kind == "embedding"
    assert bge.adapter == "ollama"
    assert bge.dim == 1024
    assert bge.capabilities == {"embedding": True}
    assert bge.recommended_roles == ["embedding"]


def test_capability_docs_are_synchronized_across_three_surfaces() -> None:
    surfaces = [
        ROOT / "services/litellm/README.md",
        ROOT / "docs/site/services/litellm.md",
        ROOT / "docs/wiki/Core-Concepts.md",
    ]
    for path in surfaces:
        text = path.read_text(encoding="utf-8")
        assert "metadata_version" in text
        assert "recommended_roles" in text
        assert "LightRAG" in text
        assert "extract" in text
        assert "query" in text
        assert "/v1/model/info" in text
        assert "catalog_name" in text
        assert "operator preference" in text
        assert "lexical" in text
