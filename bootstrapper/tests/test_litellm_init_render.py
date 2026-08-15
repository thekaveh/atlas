"""
Tests for ``services/litellm/init/scripts/init.py`` — specifically the
resolver-driven ``fetch_active_models()`` path added in B4.

These tests verify that:
  • The active model list is computed from the YAML catalogs + env vars via
    ``model_resolver``, NOT from a DB query.
  • ``psycopg2.connect`` is never called during ``fetch_active_models()``.
  • ``render_model_list`` produces correct LiteLLM config entries for the
    resolved models.
  • Cloud provider env vars produce the expected model entries.

Setup notes:
  • ``psycopg2`` is stubbed in ``sys.modules`` before the module-level
    ``import psycopg2`` runs (same pattern as test_lightrag_litellm_registration.py).
  • ``ATLAS_MODELS_DIR`` is set so ``llm_catalog`` (loaded transitively via
    model_resolver) finds the YAML files relative to the repo root rather
    than needing /catalog to exist on the host.
  • The bootstrapper/utils directory is added to sys.path so the loose-
    module imports inside model_resolver (the ``except ImportError`` fallback
    branch) can still resolve during tests — the test env runs in the
    package context, not the container, so the try-branch fires. Both
    branches are exercised by the container (where sys.path lacks ``utils``);
    the test verifies that the package-context path works correctly.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_PY = REPO_ROOT / "services/litellm/init/scripts/init.py"
UTILS_DIR = REPO_ROOT / "bootstrapper/utils"
SERVICES_DIR = REPO_ROOT / "services"


def _load_init_module(env_overrides: dict | None = None):
    """Load init.py with psycopg2 stubbed and the catalog dirs wired up.

    Uses the same importlib pattern as test_lightrag_litellm_registration.py.

    Two env vars control path resolution for the catalog modules:
      • ATLAS_CATALOG_DIR — tells init.py's _load_catalog_module() where to
        find model_resolver.py / ollama_discovery.py (bootstrapper/utils/).
      • ATLAS_MODELS_DIR — tells llm_catalog._find_models_dir() where to find
        the YAML files (repo root's services/).

    bootstrapper/bootstrapper is added to sys.path so model_resolver's
    try-branch (package-context: ``from utils import llm_catalog``) fires
    correctly from the test environment instead of the container fallback.
    """
    # Wipe any previously loaded module to get a fresh env-sensitive load.
    for key in list(sys.modules.keys()):
        if key in (
            "litellm_init",
            "litellm_settings",
            "model_resolver",
            "llm_catalog",
            "cloud_providers",
            "ollama_discovery",
        ):
            del sys.modules[key]

    # Stub psycopg2 BEFORE exec_module runs; connect must be a MagicMock
    # so tests can assert it was not called by fetch_active_models().
    pg_mock = MagicMock()
    sys.modules["psycopg2"] = pg_mock
    sys.modules["psycopg2.extras"] = MagicMock()

    # Wire up catalog paths for the host test environment.
    # ATLAS_CATALOG_DIR → _load_catalog_module finds model_resolver.py etc.
    # ATLAS_MODELS_DIR  → llm_catalog finds ollama/models.yaml etc.
    os.environ["ATLAS_CATALOG_DIR"] = str(UTILS_DIR)
    os.environ["ATLAS_MODELS_DIR"] = str(SERVICES_DIR)

    # Ensure bootstrapper/ is on sys.path so ``from utils import llm_catalog``
    # (the try-branch in model_resolver) resolves without raising ImportError.
    utils_parent = str(REPO_ROOT / "bootstrapper")
    if utils_parent not in sys.path:
        sys.path.insert(0, utils_parent)

    # Apply any extra env overrides. These stay in place for the lifetime of
    # the test call: module-level constants (LITELLM_OLLAMA_UPSTREAM etc.) bind
    # at exec_module time, but fetch_active_models() / render_model_list() read
    # os.environ at call time. Keeping the overrides in effect through both
    # phases avoids the "resolver sees different env than constants" split.
    # Each test is responsible for isolation (monkeypatch or fresh _load_init_module
    # call per test).
    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    spec = importlib.util.spec_from_file_location("litellm_init", INIT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["litellm_init"] = mod
    spec.loader.exec_module(mod)

    mod._psycopg2_mock = pg_mock
    return mod


@pytest.fixture(autouse=True)
def _restore_environ():
    """#817: ``_load_init_module`` mutates ``os.environ`` directly — ``ATLAS_CATALOG_DIR``,
    ``ATLAS_MODELS_DIR`` and arbitrary ``env_overrides`` — because module-level
    constants bind at ``exec_module`` while resolvers read env at call time, so a
    plain ``monkeypatch`` can't cover both phases. Those writes previously leaked
    process-globally (the class ``_clean_env`` fixtures only reset a fixed
    subset), risking order-dependence across tests/files. Snapshot + restore the
    whole environment around every test in this module so nothing leaks."""
    saved = os.environ.copy()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ---------------------------------------------------------------------------
# Test 1: default ollama-container-cpu config
# ---------------------------------------------------------------------------

class TestDefaultOllamaConfig:
    """With LLM_PROVIDER_SOURCE=ollama-container-cpu and no cloud providers,
    the active model set comes from the YAML catalog's default_active entries.
    """

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_SOURCE", "ollama-container-cpu")
        monkeypatch.delenv("OLLAMA_USER_MODELS", raising=False)
        monkeypatch.delenv("OLLAMA_CUSTOM_MODELS", raising=False)
        monkeypatch.delenv("LITELLM_OPENAI_ENABLED", raising=False)
        monkeypatch.delenv("LITELLM_ANTHROPIC_ENABLED", raising=False)
        monkeypatch.delenv("LITELLM_OPENROUTER_ENABLED", raising=False)

    def test_fetch_active_models_returns_default_ollama(self):
        """Default catalog yields the default_active ollama models."""
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
        })
        rows = mod.fetch_active_models()
        assert all(hasattr(row, "provider") and hasattr(row, "kind") for row in rows)
        providers = {row.provider for row in rows}
        names = {row.name for row in rows}
        assert "ollama" in providers
        assert "qwen3.8:latest" in names
        # Embedding models should also be in the default active set
        assert any("embed" in name.lower() for name in names), (
            "Expected at least one embedding model in default active set"
        )

    def test_qwen3_registered_with_ollama_chat_adapter(self):
        """qwen3.8:latest (a chat model) must get the ollama_chat/ adapter,
        not the ollama/ adapter (which would break thinking-capable models).
        """
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
        })
        rows = mod.fetch_active_models()
        model_list = mod.render_model_list(rows)

        # Find the bare-name entry for qwen3.8:latest
        bare_entries = [
            e for e in model_list
            if e["model_name"] == "qwen3.8:latest"
        ]
        assert bare_entries, "Expected bare 'qwen3.8:latest' entry in model_list"
        params = bare_entries[0]["litellm_params"]
        assert params["model"] == "ollama_chat/qwen3.8:latest", (
            f"Chat model must use ollama_chat/ adapter, got: {params['model']}"
        )
        assert params.get("think") is False, "Chat model must have think=False"

    def test_qwen3_dual_registration(self):
        """qwen3.8:latest must appear as BOTH 'ollama/qwen3.8:latest' AND bare
        'qwen3.8:latest' in the model_list (dual-alias registration)."""
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
        })
        rows = mod.fetch_active_models()
        model_list = mod.render_model_list(rows)

        names = {e["model_name"] for e in model_list}
        assert "qwen3.8:latest" in names, "Bare name must be in model_list"
        assert "ollama/qwen3.8:latest" in names, "Prefixed name must be in model_list"

    def test_embedding_model_uses_ollama_adapter(self):
        """Embedding models (names containing 'embed') must use the ``ollama/``
        adapter (not ``ollama_chat/``), because LiteLLM only routes embeddings
        via the ollama/ provider path.
        """
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
        })
        rows = mod.fetch_active_models()
        model_list = mod.render_model_list(rows)

        embed_entries = [
            e for e in model_list
            if "embed" in e["model_name"].lower()
        ]
        assert embed_entries, "Expected at least one embedding entry in model_list"
        for entry in embed_entries:
            model_val = entry["litellm_params"]["model"]
            assert model_val.startswith("ollama/"), (
                f"Embedding entry {entry['model_name']} must use ollama/ adapter, "
                f"got {model_val!r}"
            )
            # Embedding entries must NOT have think=False
            assert "think" not in entry["litellm_params"], (
                f"Embedding entry must not have 'think' param"
            )


# ---------------------------------------------------------------------------
# Test 2: cloud provider enabled
# ---------------------------------------------------------------------------

class TestCloudEnabled:
    """When a cloud provider is enabled + keyed, its models appear in fetch."""

    def test_openai_user_model_appears(self):
        """With LITELLM_OPENAI_ENABLED=true + key + OPENAI_USER_MODELS=gpt-5,
        fetch_active_models must return an (openai, gpt-5) row.
        """
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "sk-test-x",
            "OPENAI_USER_MODELS": "gpt-5",
        })
        rows = mod.fetch_active_models()
        assert any(row.provider == "openai" and row.name == "gpt-5" for row in rows), (
            f"Expected (openai, gpt-5) in rows, got: {rows}"
        )

    def test_openai_model_entry_has_correct_key_directive(self):
        """The rendered model_list entry for an openai model must use the
        ``os.environ/OPENAI_API_KEY`` directive (not the literal key).
        """
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "sk-test-x",
            "OPENAI_USER_MODELS": "gpt-5",
        })
        rows = mod.fetch_active_models()
        model_list = mod.render_model_list(rows)

        gpt5_entries = [e for e in model_list if e["model_name"] == "gpt-5"]
        assert gpt5_entries, "Expected gpt-5 entry in model_list"
        assert gpt5_entries[0]["litellm_params"]["api_key"] == "os.environ/OPENAI_API_KEY"

    def test_disabled_cloud_provider_not_included(self):
        """Without LITELLM_OPENAI_ENABLED=true, no openai rows appear."""
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "false",
            "OPENAI_API_KEY": "sk-test-x",
        })
        rows = mod.fetch_active_models()
        openai_rows = [row for row in rows if row.provider == "openai"]
        assert not openai_rows, (
            f"Expected no openai rows when disabled, got: {openai_rows}"
        )


class TestCapabilityMetadataRendering:
    def test_bge_m3_metadata_overrides_name_heuristic(self):
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
            "OLLAMA_USER_MODELS": "bge-m3",
        })
        rows = mod.fetch_active_models()
        assert len(rows) == 1
        assert rows[0].kind == "embedding"
        assert rows[0].dim == 1024

        rendered = mod.render_model_list(rows)
        bare = next(entry for entry in rendered if entry["model_name"] == "bge-m3")
        assert bare["litellm_params"]["model"] == "ollama/bge-m3"
        assert "think" not in bare["litellm_params"]
        assert bare["model_info"]["mode"] == "embedding"
        assert bare["model_info"]["output_vector_size"] == 1024
        atlas = bare["model_info"]["atlas_model_metadata"]
        assert atlas["metadata_version"] == 1
        assert atlas["catalog_name"] == "bge-m3"
        assert atlas["recommended_roles"] == ["embedding"]

    def test_partial_metadata_infers_only_missing_adapter_with_warning(self, capsys):
        from utils.llm_catalog import CatalogEntry

        mod = _load_init_module({"LLM_PROVIDER_SOURCE": "ollama-container-cpu"})
        partial = CatalogEntry(
            provider="ollama",
            name="partial-chat",
            metadata_version=1,
            kind="chat",
            capabilities={"chat": True},
        )

        rendered = mod.render_model_list([partial])
        bare = next(entry for entry in rendered if entry["model_name"] == partial.name)
        assert bare["litellm_params"]["model"] == "ollama_chat/partial-chat"
        assert "think" not in bare["litellm_params"]
        atlas = bare["model_info"]["atlas_model_metadata"]
        assert atlas["metadata_version"] == 1
        assert atlas["inferred"] is True
        assert atlas["inferred_fields"] == ["adapter"]
        assert "missing adapter metadata" in capsys.readouterr().out

    def test_renderer_rejects_provider_adapter_mismatch(self):
        from utils.llm_catalog import CatalogEntry

        mod = _load_init_module({"LLM_PROVIDER_SOURCE": "none"})
        contradictory = CatalogEntry(
            provider="openai",
            name="contradictory-chat",
            metadata_version=1,
            kind="chat",
            adapter="anthropic",
            capabilities={"chat": True},
        )
        with pytest.raises(ValueError, match="provider openai requires adapter openai"):
            mod.render_model_list([contradictory])

    def test_request_defaults_are_per_model(self):
        from utils.llm_catalog import CatalogEntry

        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
            "OLLAMA_USER_MODELS": None,
        })
        explicit = CatalogEntry(
            provider="ollama",
            name="chat-without-defaults",
            metadata_version=1,
            kind="chat",
            adapter="ollama_chat",
            capabilities={"chat": True},
        )
        rendered = mod.render_model_list([explicit])
        bare = next(entry for entry in rendered if entry["model_name"] == explicit.name)
        assert "think" not in bare["litellm_params"]

        qwen = next(
            row for row in mod.fetch_active_models()
            if row.name == "qwen3.8:latest"
        )
        qwen_bare = next(
            entry for entry in mod.render_model_list([qwen])
            if entry["model_name"] == qwen.name
        )
        assert qwen_bare["litellm_params"]["think"] is False

    def test_legacy_custom_model_uses_warned_embedding_heuristic(self, capsys):
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
            "OLLAMA_USER_MODELS": "",
            "OLLAMA_CUSTOM_MODELS": "legacy-embedder",
        })
        rows = mod.fetch_active_models()
        legacy = next(row for row in rows if row.name == "legacy-embedder")
        rendered = mod.render_model_list([legacy])
        bare = next(entry for entry in rendered if entry["model_name"] == legacy.name)
        assert bare["litellm_params"]["model"] == "ollama/legacy-embedder"
        assert "missing capability metadata" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Test 3: No DB connection
# ---------------------------------------------------------------------------

class TestNoDbConnection:
    """fetch_active_models() must NOT connect to Postgres."""

    def test_fetch_does_not_call_psycopg2_connect(self):
        """The psycopg2.connect mock must remain uncalled after fetch_active_models()."""
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
        })
        pg_mock = mod._psycopg2_mock

        # Reset call count in case the module-level code incidentally touched it.
        pg_mock.connect.reset_mock()

        # Run the resolver-based fetch — must NOT call psycopg2.connect.
        mod.fetch_active_models()

        pg_mock.connect.assert_not_called()

    def test_fetch_does_not_call_psycopg2_connect_cloud(self):
        """Same DB-free guarantee holds with a cloud provider enabled."""
        mod = _load_init_module({
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "sk-test-y",
            "OPENAI_USER_MODELS": "gpt-4o",
        })
        pg_mock = mod._psycopg2_mock
        pg_mock.connect.reset_mock()

        mod.fetch_active_models()

        pg_mock.connect.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: llm_catalog docstring alignment (litellm-init consumer note)
# ---------------------------------------------------------------------------

class TestLlmCatalogComment:
    """Smoke-test that the litellm-catalog third-consumer note in llm_catalog.py
    still refers to model_resolver (not public.llms query) after B4.  This is
    a documentation-drift guard, not a functional test — the module loads
    correctly if the catalog is importable.
    """

    def test_catalog_importable_from_bootstrapper(self):
        """llm_catalog must be importable from the bootstrapper package."""
        # This import will use the try-branch (package context).
        from utils import llm_catalog  # noqa: F401
        assert hasattr(llm_catalog, "OLLAMA_DEFAULT_CATALOG")
        assert hasattr(llm_catalog, "CLOUD_CATALOG")


class TestConsumerModelMerge:
    """#411 — init.py merges bootstrapper-generated consumer-owned model rows
    from LITELLM_CONSUMER_MODELS_FILE into the rendered config, after the stack
    rows, without crashing on a missing/malformed file.
    """

    _GOOD_FILE = (
        "# generated\n"
        "model_list:\n"
        "  - model_name: graph-rag\n"
        "    litellm_params:\n"
        "      model: openai/graph-rag\n"
        "      api_base: http://backend:8000/graph-rag/v1\n"
        "      api_key: os.environ/RAG_KEY\n"
        "    model_info:\n"
        "      atlas_owner: rag-showcase\n"
        "      atlas_managed: true\n"
        "  - model_name: vanilla-rag\n"
        "    litellm_params:\n"
        "      model: openai/vanilla-rag\n"
        "      api_base: http://backend:8000/vanilla-rag/v1\n"
        "    model_info:\n"
        "      atlas_owner: rag-showcase\n"
    )

    def test_render_config_appends_consumer_rows(self, tmp_path):
        f = tmp_path / "consumer-models.yaml"
        f.write_text(self._GOOD_FILE, encoding="utf-8")
        mod = _load_init_module({
            "LITELLM_CONSUMER_MODELS_FILE": str(f),
            "LLM_PROVIDER_SOURCE": "none",
            "HERMES_SOURCE": "disabled",
            "LIGHTRAG_SOURCE": "disabled",
        })
        config = mod.render_config([])
        names = [row["model_name"] for row in config["model_list"]]
        assert names[-2:] == ["graph-rag", "vanilla-rag"]
        graph = next(r for r in config["model_list"] if r["model_name"] == "graph-rag")
        assert graph["litellm_params"]["api_base"] == "http://backend:8000/graph-rag/v1"
        assert graph["model_info"]["atlas_owner"] == "rag-showcase"

    def test_missing_file_is_noop(self, tmp_path):
        mod = _load_init_module({
            "LITELLM_CONSUMER_MODELS_FILE": str(tmp_path / "does-not-exist.yaml"),
            "LLM_PROVIDER_SOURCE": "none",
            "HERMES_SOURCE": "disabled",
            "LIGHTRAG_SOURCE": "disabled",
        })
        assert mod.load_consumer_model_rows() == []
        config = mod.render_config([])
        names = [row["model_name"] for row in config["model_list"]]
        assert "graph-rag" not in names

    def test_malformed_rows_skipped(self, tmp_path):
        f = tmp_path / "consumer-models.yaml"
        f.write_text(
            "model_list:\n"
            "  - model_name: keep-me\n"
            "    litellm_params: {model: openai/keep-me, api_base: http://backend:8000/x}\n"
            "  - litellm_params: {model: openai/no-name}\n"  # missing model_name
            "  - not-a-mapping\n",
            encoding="utf-8",
        )
        mod = _load_init_module({"LITELLM_CONSUMER_MODELS_FILE": str(f)})
        rows = mod.load_consumer_model_rows()
        assert [r["model_name"] for r in rows] == ["keep-me"]

    def test_invalid_yaml_degrades_without_crash(self, tmp_path):
        f = tmp_path / "consumer-models.yaml"
        f.write_text("model_list: [unclosed\n", encoding="utf-8")
        mod = _load_init_module({"LITELLM_CONSUMER_MODELS_FILE": str(f)})
        assert mod.load_consumer_model_rows() == []

    def test_consumer_row_colliding_with_stack_is_skipped(self, tmp_path):
        """Last-line anti-hijack: a consumer row whose model_name collides with an
        already-rendered stack row (here the runtime hermes-agent) is SKIPPED, so
        the stack row always wins — even if the generated file was hand-crafted to
        bypass the bootstrapper's host-side reserved-alias reject.
        """
        f = tmp_path / "consumer-models.yaml"
        f.write_text(
            "model_list:\n"
            "  - model_name: hermes-agent\n"          # collides with the stack row
            "    litellm_params:\n"
            "      model: openai/hermes-agent\n"
            "      api_base: http://backend:8000/evil/v1\n"
            "    model_info:\n"
            "      atlas_owner: attacker\n"
            "  - model_name: safe-consumer-model\n"    # unique → should be kept
            "    litellm_params:\n"
            "      model: openai/safe-consumer-model\n"
            "      api_base: http://backend:8000/safe/v1\n",
            encoding="utf-8",
        )
        mod = _load_init_module({
            "LITELLM_CONSUMER_MODELS_FILE": str(f),
            "LLM_PROVIDER_SOURCE": "none",
            "HERMES_SOURCE": "container",                 # renders the stack hermes row
            "HERMES_ENDPOINT": "http://hermes:9000",
            "LIGHTRAG_SOURCE": "disabled",
        })
        config = mod.render_config([])
        hermes_rows = [r for r in config["model_list"] if r["model_name"] == "hermes-agent"]
        # Exactly one hermes-agent, and it is the STACK row (not the consumer's).
        assert len(hermes_rows) == 1
        assert hermes_rows[0]["litellm_params"]["api_base"] == "http://hermes:9000/v1"
        assert "evil" not in hermes_rows[0]["litellm_params"]["api_base"]
        # The non-colliding consumer row is still merged.
        names = [r["model_name"] for r in config["model_list"]]
        assert "safe-consumer-model" in names
