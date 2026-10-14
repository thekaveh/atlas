"""Acceptance coverage for model capability metadata (#417)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.parametrize("kind", ["tools", "json", "vision"])
@pytest.mark.parametrize("choices", [{"unexpected": []}, True, 1])
def test_successful_probe_reply_with_invalid_choices_is_unsupported(kind, choices):
    import start

    probe = getattr(start, f"probe_{kind}")
    assert probe(lambda *_a: (200, {"choices": choices}), "m") == "unsupported"


@pytest.mark.parametrize("calls", [
    1,
    {"function": {"name": "get_utc_time"}},
    ["unexpected"],
    [None],
    [{"function": ["get_utc_time"]}],
    [{"function": "get_utc_time"}],
    [{"function": 1}],
])
def test_successful_tool_probe_reply_with_invalid_calls_is_unsupported(calls):
    import start

    payload = {"choices": [{"message": {"tool_calls": calls}}]}
    assert start.probe_tools(lambda *_a: (200, payload), "m") == "unsupported"


def test_tool_probe_accepts_the_offered_call_among_invalid_entries():
    import start

    payload = {"choices": [{"message": {"tool_calls": [
        "unexpected", {"function": ["unexpected"]},
        {"function": {"name": "get_utc_time"}},
    ]}}]}
    assert start.probe_tools(lambda *_a: (200, payload), "m") == "supported"


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


@pytest.mark.parametrize("vector", [
    "encoded-vector", {"dimension": 1}, 1, True,
    ["1"], [True], [[0.1]], [None],
    [float("nan")], [float("inf")], [float("-inf")], [10 ** 400],
])
def test_embedding_probe_rejects_non_numeric_or_nonfinite_vectors(monkeypatch, vector):
    import io
    import start

    probe = start._shared_embedding_probe()
    raw = json.dumps({"data": [{"embedding": vector}]}).encode()
    monkeypatch.setattr(probe.__globals__["urllib"].request, "urlopen",
                        lambda *_a, **_k: io.BytesIO(raw))
    assert probe("http://owned.invalid/v1", "owned-key", "owned-model") == ("unsupported", None)


@pytest.mark.parametrize("vector", [[0.1, -0.2, 0.3], [0, 1, -1], [1e308]])
def test_embedding_probe_preserves_finite_numeric_dimensions(monkeypatch, vector):
    import io
    import start

    probe = start._shared_embedding_probe()
    raw = json.dumps({"data": [{"embedding": vector}]}).encode()
    monkeypatch.setattr(probe.__globals__["urllib"].request, "urlopen",
                        lambda *_a, **_k: io.BytesIO(raw))
    assert probe("http://owned.invalid/v1", "owned-key", "owned-model") == ("supported", len(vector))


def test_lightrag_dimension_resolution_does_not_use_an_encoded_string_length(monkeypatch, capsys):
    import io
    import start

    probe = start._shared_embedding_probe()
    raw = json.dumps({"data": [{"embedding": "encoded-vector"}]}).encode()
    monkeypatch.setattr(probe.__globals__["urllib"].request, "urlopen",
                        lambda *_a, **_k: io.BytesIO(raw))
    assert probe.__globals__["resolve_dim"]("owned-unlisted-model") == 768
    assert "unsupported" in capsys.readouterr().err


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


def test_an_unavailable_refresh_keeps_the_real_verdict(tmp_path, monkeypatch, capsys):
    """`--refresh` with the gateway down overwrote a measured verdict with
    `unavailable`, and the stale dimension was printed beside it."""
    import start

    alias = "ollama/nomic-embed-text"
    identity = start.probe_identity(alias, start._catalog_entry(alias))
    monkeypatch.setattr(start, "_measure", lambda kind, a, gw: ("supported", 768))
    start.run_capability_probes(tmp_path, ("u", "k", None), [(alias, "embedding", identity)], 20)
    monkeypatch.setattr(start, "_measure", lambda kind, a, gw: ("unavailable", None))
    plan = start.plan_capability_probes([alias], [], start._probe_store(tmp_path), True)
    start.run_capability_probes(tmp_path, ("u", "k", None), plan, 20)
    start.report_capability_probes(tmp_path, [alias], [])
    assert "embedding: supported (768 dimensions)" in capsys.readouterr().out
    monkeypatch.setattr(start, "_measure", lambda kind, a, gw: ("unsupported", None))
    start.run_capability_probes(tmp_path, ("u", "k", None), plan, 20)
    start.report_capability_probes(tmp_path, [alias], [])
    out = capsys.readouterr().out
    assert "embedding: unsupported" in out and "dimensions" not in out


@pytest.mark.parametrize("dimension", [1, 4000, 4001, 65536])
@pytest.mark.parametrize("action", ["selection", "repair"])
def test_catalog_memory_dimension_is_validated_before_env_write(
    tmp_path, monkeypatch, dimension, action
):
    import start
    from utils.model_resolver import MAX_PGVECTOR_DIMENSION

    payload = {"embeddings": [{
        "name": "owned-dimension-fixture", "metadata_version": 1,
        "kind": "embedding", "adapter": "ollama", "dim": dimension,
    }]}
    # Catalogs can describe wider models for other consumers; the selected
    # memory contract must enforce its own bound before touching .env.
    jsonschema.validate(payload, SCHEMA)
    entries = _load_flat_catalog(tmp_path, payload)
    monkeypatch.setattr(llm_catalog, "all_catalog_entries", lambda: entries)
    writes = []
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = SimpleNamespace(parse_env_file=lambda: {})
    starter.source_override_manager = SimpleNamespace(
        update_env_file=lambda values: writes.append(dict(values)) or True
    )
    selections = {"LITELLM_EMBEDDING_MODEL": "ollama/owned-dimension-fixture"}
    if action == "selection":
        apply = lambda: starter.apply_user_model_selections(selections)
        expected_write = {**selections, "LANGMEM_EMBEDDING_DIM": str(dimension)}
    else:
        apply = lambda: starter._repair_catalog_embedding_dim(
            {**selections, "LANGMEM_EMBEDDING_DIM": "768"}
        )
        expected_write = {"LANGMEM_EMBEDDING_DIM": str(dimension)}

    if dimension <= MAX_PGVECTOR_DIMENSION:
        assert apply()
        assert writes == [expected_write]
    else:
        with pytest.raises(ValueError, match="LANGMEM_EMBEDDING_DIM"):
            apply()
        assert writes == []
    assert selections == {"LITELLM_EMBEDDING_MODEL": "ollama/owned-dimension-fixture"}


@pytest.fixture
def namespaced_dimension_catalog(tmp_path, monkeypatch):
    flat = {"embeddings": [
        {"name": "shared-embed", "metadata_version": 1,
         "kind": "embedding", "adapter": "ollama", "dim": 768},
        {"name": "standalone-embed", "metadata_version": 1,
         "kind": "embedding", "adapter": "ollama", "dim": 768},
        {"name": "openai/cloud-embed", "metadata_version": 1,
         "kind": "embedding", "adapter": "ollama", "dim": 4000},
    ]}
    cloud = {
        "openai": {"embeddings": [
            {"name": "cloud-embed", "metadata_version": 1,
             "kind": "embedding", "adapter": "openai", "dim": 1536},
            {"name": "shared-embed", "metadata_version": 1,
             "kind": "embedding", "adapter": "openai", "dim": 1024},
        ]},
        "openrouter": {"embeddings": [{
            "name": "openrouter/vendor/cloud-embed", "metadata_version": 1,
            "kind": "embedding", "adapter": "openrouter", "dim": 2048,
        }]},
    }
    jsonschema.validate(flat, SCHEMA)
    jsonschema.validate(cloud, SCHEMA)
    entries = _load_flat_catalog(tmp_path, flat) + _load_cloud_catalog(tmp_path, cloud)
    monkeypatch.setattr(llm_catalog, "all_catalog_entries", lambda: entries)


@pytest.mark.parametrize("model,expected", [
    ("standalone-embed", 768),
    ("shared-embed", 1024),
    ("ollama/shared-embed", 768),
    ("cloud-embed", 1536),
    ("openai/cloud-embed", 1536),
    ("openai/shared-embed", 1024),
    ("ollama/openai/cloud-embed", 4000),
    ("openrouter/vendor/cloud-embed", 2048),
    ("custom/shared-embed", None),
    ("openai/standalone-embed", None),
    ("ollama/cloud-embed", None),
    ("other/openrouter/vendor/cloud-embed", None),
])
def test_dimension_lookup_preserves_full_model_identity(
    namespaced_dimension_catalog, model, expected
):
    from utils.model_resolver import dim_for_model_id

    assert dim_for_model_id(model) == expected


@pytest.mark.parametrize("action", ["selection", "repair"])
def test_custom_model_dimension_is_not_replaced_by_a_catalog_suffix(
    namespaced_dimension_catalog, action
):
    import start

    model = "custom/shared-embed"
    values = {"LITELLM_EMBEDDING_MODEL": model, "LANGMEM_EMBEDDING_DIM": "2048"}
    writes = []
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = SimpleNamespace(parse_env_file=lambda: {})
    starter.source_override_manager = SimpleNamespace(
        update_env_file=lambda updates: writes.append(dict(updates)) or True
    )

    if action == "selection":
        assert starter.apply_user_model_selections(values)
        assert writes == [values]
    else:
        assert starter._repair_catalog_embedding_dim(values)
        assert writes == []
    assert values == {"LITELLM_EMBEDDING_MODEL": model, "LANGMEM_EMBEDDING_DIM": "2048"}


@pytest.fixture
def active_alias_catalog(tmp_path, monkeypatch):
    def render(case):
        import importlib.util
        import sys
        from utils.model_resolver import active_models

        flat = {"embeddings": [{
            "name": "unrelated-embed" if case.get("custom") else "shared-embed", "metadata_version": 1,
            "kind": "embedding", "adapter": "ollama", "dim": 768,
        }]}
        cloud = {provider: {"embeddings": [{
            "name": case.get("router_catalog", "shared-embed") if provider == "openrouter" else "shared-embed", "metadata_version": 1,
            "kind": "embedding", "adapter": provider, "dim": dimension,
            "default": provider == "openai" and case.get("openai_default", False),
        }]} for provider, dimension in (("openai", 1024), ("openrouter", 2048))}
        jsonschema.validate(flat, SCHEMA)
        jsonschema.validate(cloud, SCHEMA)
        monkeypatch.setattr(llm_catalog, "OLLAMA_DEFAULT_CATALOG", _load_flat_catalog(tmp_path, flat))
        monkeypatch.setattr(llm_catalog, "CLOUD_CATALOG", _load_cloud_catalog(tmp_path, cloud))
        env = {
            "LLM_PROVIDER_SOURCE": case.get("engine", "ollama-container-cpu"),
            "OLLAMA_USER_MODELS": "" if case.get("custom") else "shared-embed",
            "OLLAMA_CUSTOM_MODELS": "shared-embed" if case.get("custom") else "",
            "CLOUD_OPENAI_SOURCE": case.get("openai", "enabled"),
            "CLOUD_OPENROUTER_SOURCE": case.get("openrouter", "disabled"),
            "CLOUD_ANTHROPIC_SOURCE": "disabled",
            "OPENAI_USER_MODELS": "shared-embed", "OPENROUTER_USER_MODELS": "shared-embed",
            "OPENAI_API_KEY": case.get("key", "owned-synthetic-key"),
            "OPENROUTER_API_KEY": "owned-synthetic-key", "ANTHROPIC_API_KEY": "",
            # These persisted flags describe the prior launch, not this one.
            "LITELLM_OPENAI_ENABLED": "false", "LITELLM_OPENROUTER_ENABLED": "false",
            "LITELLM_ANTHROPIC_ENABLED": "false",
        }
        effective = {**env, **case.get("pending", {})}
        routed = {**effective, **case["flags"]}
        for key, value in routed.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("ATLAS_CATALOG_DIR", str(ROOT / "bootstrapper/utils"))
        monkeypatch.setenv("LITELLM_CONFIG_OUT", str(tmp_path / "owned-config.yaml"))
        monkeypatch.setenv("LITELLM_CONSUMER_MODELS_FILE", str(tmp_path / "absent-models.yaml"))
        monkeypatch.setitem(sys.modules, "llm_catalog", llm_catalog)
        spec = importlib.util.spec_from_file_location("owned_alias_init", ROOT / "services/litellm/init/scripts/init.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = module.render_config(active_models(routed))
        row = next(row for row in config["model_list"] if row["model_name"] == case.get("model", "shared-embed"))
        return env, row
    return render


_ALIAS_CASES = [
    {"id": "both-bare", "dimension": 768, "provider": "ollama", "flags": {"LITELLM_OPENAI_ENABLED": "true"}},
    {"id": "ollama-alone", "openai": "disabled", "dimension": 768, "provider": "ollama", "flags": {}},
    {"id": "cloud-alone", "engine": "none", "dimension": 1024, "provider": "openai", "flags": {"LITELLM_OPENAI_ENABLED": "true"}},
    {"id": "openrouter-alone", "engine": "none", "openai": "disabled", "openrouter": "enabled", "dimension": 2048, "provider": "openrouter", "flags": {"LITELLM_OPENROUTER_ENABLED": "true"}},
    {"id": "two-clouds", "engine": "none", "openrouter": "enabled", "dimension": 1024, "provider": "openai", "flags": {"LITELLM_OPENAI_ENABLED": "true", "LITELLM_OPENROUTER_ENABLED": "true"}},
    {"id": "unkeyed-first-cloud", "engine": "none", "key": "", "openrouter": "enabled", "dimension": 2048, "provider": "openrouter", "flags": {"LITELLM_OPENAI_ENABLED": "true", "LITELLM_OPENROUTER_ENABLED": "true"}},
    {"id": "ollama-canonical", "model": "ollama/shared-embed", "dimension": 768, "provider": "ollama", "flags": {"LITELLM_OPENAI_ENABLED": "true"}},
    {"id": "pending-deselection", "pending": {"OLLAMA_USER_MODELS": ""}, "dimension": 1024, "provider": "openai", "flags": {"LITELLM_OPENAI_ENABLED": "true"}},
]


@pytest.mark.parametrize("case", _ALIAS_CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("action", ["selection", "repair"])
def test_memory_dimension_matches_actual_active_alias_registration(active_alias_catalog, case, action):
    import start

    current, row = active_alias_catalog(case)
    dimension = row["model_info"]["output_vector_size"]
    assert (dimension, row["model_info"]["atlas_model_metadata"]["provider"]) == (case["dimension"], case["provider"])
    selections = {"LITELLM_EMBEDDING_MODEL": row["model_name"], **case.get("pending", {})}
    current = {**current, "LITELLM_EMBEDDING_MODEL": row["model_name"], "LANGMEM_EMBEDDING_DIM": "1024"}
    writes = []
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = SimpleNamespace(parse_env_file=lambda: current)
    starter.source_override_manager = SimpleNamespace(update_env_file=lambda values: writes.append(dict(values)) or True)
    if action == "selection":
        assert starter.apply_user_model_selections(selections)
        assert writes == [{**selections, "LANGMEM_EMBEDDING_DIM": str(dimension)}]
    else:
        assert starter._repair_catalog_embedding_dim({**current, **case.get("pending", {})})
        assert writes == ([] if dimension == 1024 else [{"LANGMEM_EMBEDDING_DIM": str(dimension)}])


@pytest.mark.parametrize("configured", [768, 1024])
def test_selected_explicit_dimension_is_checked_against_registered_winner(active_alias_catalog, configured):
    import start

    current, row = active_alias_catalog(_ALIAS_CASES[0])
    dimension = row["model_info"]["output_vector_size"]
    assert dimension == 768
    writes = []
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = SimpleNamespace(parse_env_file=lambda: current)
    starter.source_override_manager = SimpleNamespace(update_env_file=lambda values: writes.append(dict(values)) or True)
    selection = {"LITELLM_EMBEDDING_MODEL": "shared-embed", "LANGMEM_EMBEDDING_DIM": str(configured)}
    if configured == dimension:
        assert starter.apply_user_model_selections(selection)
        assert writes == [selection]
    else:
        with pytest.raises(ValueError, match="produces 768-dim"):
            starter.apply_user_model_selections(selection)
        assert writes == []


@pytest.mark.parametrize("action", ["selection", "repair", "new-selection"])
def test_unknown_active_winner_keeps_its_custom_contract(active_alias_catalog, action):
    import start

    current, row = active_alias_catalog({**_ALIAS_CASES[0], "custom": True})
    assert (
        row["model_info"].get("output_vector_size"),
        row["model_info"]["atlas_model_metadata"]["provider"],
    ) == (None, "ollama")
    current = {**current, "LITELLM_EMBEDDING_MODEL": "shared-embed", "LANGMEM_EMBEDDING_DIM": "3072"}
    writes = []
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = SimpleNamespace(parse_env_file=lambda: current)
    starter.source_override_manager = SimpleNamespace(update_env_file=lambda values: writes.append(dict(values)) or True)
    if action == "repair":
        assert starter._repair_catalog_embedding_dim(current)
        assert writes == []
    elif action == "selection":
        assert starter.apply_user_model_selections({"LITELLM_EMBEDDING_MODEL": "shared-embed"})
        assert writes == [{"LITELLM_EMBEDDING_MODEL": "shared-embed", "LANGMEM_EMBEDDING_DIM": "3072"}]
    else:
        current["LITELLM_EMBEDDING_MODEL"] = "another-model"
        with pytest.raises(ValueError, match="must declare LANGMEM_EMBEDDING_DIM"):
            starter.apply_user_model_selections({"LITELLM_EMBEDDING_MODEL": "shared-embed"})
        assert writes == []


_WIZARD_ALIAS_CASES = [
    {"id": "known-fresh", "dim": 768, "contract": 768, "prefill": ""},
    {"id": "known-saved", "saved": True, "dim": 768, "contract": 768, "prefill": ""},
    {"id": "custom-fresh", "custom": True, "dim": None, "contract": 3072, "prefill": ""},
    {"id": "custom-saved", "custom": True, "saved": True, "dim": None, "contract": 3072, "prefill": "3072"},
    {"id": "pending-custom-add", "custom": True, "current_custom": "", "pending_title": "shared-embed", "pending": {"OLLAMA_CUSTOM_MODELS": "shared-embed"}, "dim": None, "contract": 3072, "prefill": ""},
    {"id": "pending-custom-keep", "custom": True, "saved": True, "pending_title": "keep", "dim": None, "contract": 3072, "prefill": "3072"},
    {"id": "pending-custom-clear", "custom": True, "saved": True, "pending_title": "clear", "pending": {"OLLAMA_CUSTOM_MODELS": ""}, "dim": 1024, "contract": 1024, "prefill": ""},
]


@pytest.mark.parametrize("case", _WIZARD_ALIAS_CASES, ids=lambda case: case["id"])
def test_wizard_dimension_step_follows_registered_alias_contract(active_alias_catalog, case):
    import start
    from wizard.llm_steps import (
        LLM_DEFAULT_EMBED_TITLE, LLM_DEFAULT_EMBED_DIM_TITLE,
        OLLAMA_CUSTOM_TITLE, SECRET_CLEAR, SECRET_KEEP, build_default_model_steps,
    )
    from ui.textual.integration import _embedding_model_selections

    current, row = active_alias_catalog({**_ALIAS_CASES[0], **case})
    current.update({
        "LITELLM_EMBEDDING_MODEL": "shared-embed" if case.get("saved") else "ollama/unrelated-embed",
        "LANGMEM_EMBEDDING_DIM": "3072" if case.get("saved") else "768",
        "OLLAMA_CUSTOM_MODELS": case.get("current_custom", current["OLLAMA_CUSTOM_MODELS"]),
    })
    selections = {LLM_DEFAULT_EMBED_TITLE: "shared-embed"}
    if "pending_title" in case:
        selections[OLLAMA_CUSTOM_TITLE] = {"keep": SECRET_KEEP, "clear": SECRET_CLEAR}.get(case["pending_title"], case["pending_title"])
    steps = {step.title: step for step in build_default_model_steps(current)}
    options = steps[LLM_DEFAULT_EMBED_TITLE].options_provider(selections)
    dimension = steps[LLM_DEFAULT_EMBED_DIM_TITLE]
    assert (
        row["model_info"].get("output_vector_size"),
        "shared-embed" in [option.value for option in options],
        dimension.skip_if_prev(selections), dimension.default_value_provider(selections),
    ) == (case["dim"], True, case["dim"] is not None, case["prefill"])
    if case["dim"] is None:
        selections[LLM_DEFAULT_EMBED_DIM_TITLE] = str(case["contract"])
    payload = _embedding_model_selections(selections, current, LLM_DEFAULT_EMBED_TITLE, LLM_DEFAULT_EMBED_DIM_TITLE)
    values = {**payload, **case.get("pending", {})}
    writes = []
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = SimpleNamespace(parse_env_file=lambda: current)
    starter.source_override_manager = SimpleNamespace(update_env_file=lambda value: writes.append(dict(value)) or True)
    assert starter.apply_user_model_selections(values)
    assert writes == [{**values, "LANGMEM_EMBEDDING_DIM": str(case["contract"])}]


@pytest.mark.parametrize("secret", ["<KEEP>", "owned-new-synthetic-key"])
@pytest.mark.parametrize("case", [
    ("", None, "openrouter"),
    (None, 1024, "openai"),
    ("<KEEP>", 1024, "openai"),
    ("shared-embed", 1024, "openai"),
    (",", 1024, "openai"),
])
def test_wizard_cloud_models_verdict_matches_projected_alias(
    active_alias_catalog, secret, case
):
    import importlib.util
    import start
    from ui.textual.integration import _selections_to_args
    from utils.model_resolver import active_models
    from wizard.llm_steps import (
        LLM_DEFAULT_EMBED_TITLE, LLM_DEFAULT_EMBED_DIM_TITLE,
        build_default_model_steps, cloud_models_title, cloud_secret_title,
    )

    models, dimension, provider = case
    current, _ = active_alias_catalog({
        **_ALIAS_CASES[0], "engine": "none", "openrouter": "enabled",
        "openai_default": True, "router_catalog": "unrelated-embed",
        "flags": {"LITELLM_OPENAI_ENABLED": "true", "LITELLM_OPENROUTER_ENABLED": "true"},
    })
    current.update({"LITELLM_EMBEDDING_MODEL": "old-embed", "LANGMEM_EMBEDDING_DIM": "768"})
    selections = {
        LLM_DEFAULT_EMBED_TITLE: "shared-embed",
        cloud_secret_title("OpenAI"): secret,
        cloud_models_title("OpenAI"): models,
    }
    steps = {step.title: step for step in build_default_model_steps(current)}
    prompt = steps[LLM_DEFAULT_EMBED_DIM_TITLE]
    assert prompt.skip_if_prev(selections) == (dimension is not None)
    if dimension is None:
        selections[LLM_DEFAULT_EMBED_DIM_TITLE] = "3072"
    sources, options = _selections_to_args(selections, [], 63000, current)
    effective = {**current, **{key.upper(): value for key, value in sources.items()},
                 **options["cloud_api_keys"], **options["cloud_user_models"]}
    launch_env = start._launch_model_env(effective)
    spec = importlib.util.spec_from_file_location("owned_cloud_verdict_init", ROOT / "services/litellm/init/scripts/init.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rendered = module.render_config(active_models(launch_env))
    row = next(row for row in rendered["model_list"] if row["model_name"] == "shared-embed")
    assert (row["model_info"].get("output_vector_size"),
            row["model_info"]["atlas_model_metadata"]["provider"]) == (dimension, provider)
    writes = []
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = SimpleNamespace(parse_env_file=lambda: effective)
    starter.source_override_manager = SimpleNamespace(update_env_file=lambda values: writes.append(dict(values)) or True)
    assert (
        starter.apply_user_model_selections(options["default_model_selections"]),
        writes, "metadata-only" in repr((sources, options, writes)), effective["OPENAI_API_KEY"],
    ) == (
        True, [{"LITELLM_EMBEDDING_MODEL": "shared-embed", "LANGMEM_EMBEDDING_DIM": str(dimension or 3072)}],
        False, current["OPENAI_API_KEY"] if secret == "<KEEP>" or models == "" else secret,
    )
