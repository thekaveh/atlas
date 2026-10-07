"""Acceptance coverage for model capability metadata (#417)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from utils import llm_catalog
from tests.three_surface_test_utils import surface_text


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
    qwen = next(entry for entry in llm_catalog.ollama_entries() if entry.name == "qwen3.8:latest")
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
    surfaces = (
        (ROOT / "services/litellm/README.md").read_text(encoding="utf-8"),
        surface_text("services/litellm/README.md", "site"),
        surface_text("services/litellm/README.md", "wiki"),
    )
    for text in surfaces:
        assert "metadata_version" in text
        assert "recommended_roles" in text
        assert "LightRAG" in text
        assert "extract" in text
        assert "query" in text
        assert "/v1/model/info" in text
        assert "catalog_name" in text
        assert "operator preference" in text
        assert "lexical" in text


# ─── opt-in capability probes (#1195) ───────────────────────────────────

_TOOL_CALL = {"choices": [{"message": {"tool_calls": [{"function": {"name": "get_utc_time"}}]}}]}
_REPLY = lambda text: {"choices": [{"message": {"content": text}}]}  # noqa: E731
# What LiteLLM returns for an alias it does not serve: a 400, not a 404.
_UNKNOWN_ALIAS = {"error": {"message": "400: {'error': '/chat/completions: Invalid model name passed in model=gpt-5'}",
                            "type": "None", "code": "400"}}


@pytest.mark.parametrize(("kind", "good", "bad"), [
    ("tools", _TOOL_CALL, _REPLY("It is noon.")),
    ("json", _REPLY('<think>sum</think>```json\n{"answer": 4.0}\n```'), _REPLY("four")),
    ("vision", _REPLY("Red."), _REPLY("I considered it, but I cannot view images.")),
])
def test_each_probe_tells_supported_unsupported_and_unavailable_apart(kind, good, bad):
    import start

    probe = {"tools": start.probe_tools, "json": start.probe_json, "vision": start.probe_vision}[kind]
    for reply, expected in (((200, good), "supported"), ((200, bad), "unsupported"),
                            ((400, {}), "unsupported"), ((None, {}), "unavailable"), ((503, {}), "unavailable"),
                            ((401, {}), "unavailable"), ((429, {}), "unavailable"), ((200, []), "unsupported"),
                            ((400, _UNKNOWN_ALIAS), "unavailable")):
        assert probe(lambda _path, _body, r=reply: r, "m") == expected, (kind, reply)


def _tool_model() -> str:
    return next(e.name for e in llm_catalog.ollama_entries() if e.capabilities.get("tools"))


def test_a_declared_tool_capability_the_provider_ignores_fails(tmp_path, capsys):
    import start

    (tmp_path / ".env").write_text("LITELLM_DEFAULT_MODEL=x\n")
    alias = f"ollama/{_tool_model()}"
    plan = start.plan_capability_probes([alias], ["tools"], {}, refresh=False)
    ignore = lambda _path, _body: (200, _REPLY("noon"))  # noqa: E731

    assert start.run_capability_probes(tmp_path, ("http://gw/v1", "k", ignore), plan, 5) == 0
    assert start.report_capability_probes(tmp_path, [alias], ["tools"]) == 1
    assert "tools: unsupported  FAIL: declared by the catalog" in capsys.readouterr().out
    # A stored verdict is reported (and fails) again without a new request.
    assert start.plan_capability_probes([alias], ["tools"], start._probe_store(tmp_path), False) == []
    assert start.report_capability_probes(tmp_path, [alias], ["tools"]) == 1


def test_the_probe_command_never_changes_model_selection(tmp_path, monkeypatch):
    import start
    from click.testing import CliRunner

    env_text = "LITELLM_DEFAULT_MODEL=ollama/qwen3.8:latest\nLITELLM_PORT=63004\n"
    (tmp_path / ".env").write_text(env_text)
    parser = type("P", (), {"root_dir": str(tmp_path), "parse_env_file": lambda self: {
        "LITELLM_DEFAULT_MODEL": "ollama/qwen3.8:latest", "LITELLM_PORT": "63004"}})()
    monkeypatch.setattr(start, "AtlasStarter", lambda: type("S", (), {"config_parser": parser})())
    monkeypatch.setattr(start, "gateway_post", lambda *_a: lambda _p, _b: (200, _REPLY("noon")))
    result = CliRunner().invoke(start.main, ["models", "probe", "--kind", "tools"])
    assert result.exit_code == 1 and "FAIL" in result.output, result.output
    assert (tmp_path / ".env").read_text() == env_text


def test_the_embedding_probe_is_lightrag_inits_own(monkeypatch):
    import start
    import urllib.error

    probe = start._shared_embedding_probe()
    assert probe.__code__.co_filename.endswith("services/lightrag/init/scripts/resolve-models.py")
    namespace = probe.__globals__  # the loaded resolve-models.py module
    import io

    unknown = io.BytesIO(json.dumps(_UNKNOWN_ALIAS).encode())
    for raised, expected in ((urllib.error.HTTPError("u", 400, "bad", None, None), "unsupported"),
                             (urllib.error.HTTPError("u", 400, "bad", None, unknown), "unavailable"),
                             (urllib.error.URLError("down"), "unavailable")):
        monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *_a, e=raised, **_k: (_ for _ in ()).throw(e))
        assert probe("http://gw/v1", "k", "m") == (expected, None)
    monkeypatch.setitem(namespace, "probe_embedding_dim", lambda *_a, **_k: ("supported", 2560))
    assert namespace["resolve_dim"]("some-unlisted-embedder") == 2560  # resolve_dim calls the shared probe
    monkeypatch.setitem(namespace, "probe_embedding_dim", lambda *_a, **_k: ("unavailable", None))
    assert namespace["resolve_dim"]("some-unlisted-embedder") == 768  # lightrag-init's fallback stays


def test_probes_are_opt_in_and_capped(tmp_path):
    import start

    root = Path(start.__file__).resolve().parent
    callers = [path for folder in ("core", "ui", "wizard", "services", "utils")
               for path in (root / folder).rglob("*.py") if "run_capability_probes" in path.read_text()]
    assert callers == []  # only `./start.sh models probe` runs them
    source = Path(start.__file__).read_text()
    command = source[source.index("def models_probe_command"):]
    assert source.count("run_capability_probes(") == 2 and "run_capability_probes(" in command
    calls = []
    plan = [("a", "tools", {}), ("b", "tools", {}), ("c", "tools", {})]
    assert start.run_capability_probes(tmp_path, ("u", "k", lambda *a: calls.append(a)), plan, 2) == 3
    assert calls == []
    # An unavailable result measured nothing, so it is never reused.
    alias = f"ollama/{_tool_model()}"
    identity = start.probe_identity(alias, start._catalog_entry(alias))
    stale = {json.dumps(identity, sort_keys=True): {"results": {"tools": "unavailable"}}}
    assert start.plan_capability_probes([alias], ["tools"], stale, refresh=False)


def test_a_stored_result_is_reused_only_for_the_same_identity():
    import start

    alias = f"ollama/{_tool_model()}"
    identity = start.probe_identity(alias, start._catalog_entry(alias))
    stored = lambda ident: {json.dumps(ident, sort_keys=True): {"results": {"tools": "supported"}}}  # noqa: E731
    assert start.plan_capability_probes([alias], ["tools"], stored(identity), refresh=False) == []
    for field in ("model", "provider", "alias", "revision"):
        changed = {**identity, field: identity[field] + "-other"}
        assert start.plan_capability_probes([alias], ["tools"], stored(changed), refresh=False), field
