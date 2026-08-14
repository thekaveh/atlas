"""Tests for bootstrapper.utils.model_resolver.

All tests are pure unit tests — no DB, no network, no running containers.
Ollama tags are passed explicitly where needed; env dicts are synthetic.

Test matrix:
  1. Default config  — ollama-container-cpu, no cloud keys
  2. Ollama disabled — LLM_PROVIDER_SOURCE=none
  3. Cloud enabled   — openai enabled+keyed, ollama also active → ordering
  4. User override   — OPENAI_USER_MODELS=gpt-5 activates exactly that model
  5. Cloud no key    — enabled but no API key → no actives for that provider
  6. Custom ollama   — OLLAMA_CUSTOM_MODELS synthesized entry
  7. ollama_tags     — host-side auto-import unions into active set
  8. best('vision')  — picks vision-capable model; None when none active
  9. Embedding carve-out — resolved_defaults never contains LITELLM_EMBEDDING_MODEL
"""

from __future__ import annotations

import pytest

from utils.model_resolver import (
    MEMORY_FACTS_EMBEDDING_DIM,
    _synthesize,
    active_models,
    best,
    dim_for_model_id,
    embedding_dim_warning,
    looks_like_embedding,
    resolved_defaults,
)
from utils.llm_catalog import cloud_entries, ollama_entries


# ── helpers ──────────────────────────────────────────────────────────────────

def _names(entries) -> list[str]:
    return [e.name for e in entries]


# ── 1. Default config ────────────────────────────────────────────────────────

class TestDefaultConfig:
    """Empty env (or just ollama-container-cpu) → default ollama actives."""

    def test_active_models_includes_default_active_ollama(self):
        models = active_models({})
        names = _names(models)
        # qwen3.8:latest is the default content+vision model
        assert "qwen3.8:latest" in names

    def test_active_models_includes_embedding_model(self):
        models = active_models({})
        names = _names(models)
        # nomic-embed-text is default_active for embeddings
        assert "nomic-embed-text" in names

    def test_active_models_has_three_default_ollama_entries(self):
        # The catalog has 3 default_active ollama entries:
        # qwen3.8:latest (content+vision), qwen3-embedding:0.6b (embeddings),
        # nomic-embed-text (embeddings)
        models = active_models({})
        ollama_models = [e for e in models if e.provider == "ollama"]
        assert len(ollama_models) >= 3

    def test_best_content_is_qwen(self):
        result = best("content", {})
        assert result == "ollama/qwen3.8:latest"

    def test_best_embeddings_is_ollama(self):
        result = best("embeddings", {})
        assert result is not None
        assert result.startswith("ollama/")

    def test_resolved_defaults_has_exactly_two_keys(self):
        defaults = resolved_defaults({})
        assert set(defaults.keys()) == {"LITELLM_DEFAULT_MODEL", "LITELLM_VISION_MODEL"}

    def test_resolved_defaults_default_model_is_qwen(self):
        defaults = resolved_defaults({})
        assert defaults["LITELLM_DEFAULT_MODEL"] == "ollama/qwen3.8:latest"

    def test_resolved_defaults_vision_model_is_qwen(self):
        defaults = resolved_defaults({})
        assert defaults["LITELLM_VISION_MODEL"] == "ollama/qwen3.8:latest"

    def test_explicit_ollama_container_cpu_same_as_empty(self):
        env = {"LLM_PROVIDER_SOURCE": "ollama-container-cpu"}
        assert active_models(env) == active_models({})


# ── 2. Ollama disabled ────────────────────────────────────────────────────────

class TestOllamaDisabled:
    """LLM_PROVIDER_SOURCE=none → no ollama actives."""

    def test_no_ollama_actives_when_source_none(self):
        env = {"LLM_PROVIDER_SOURCE": "none"}
        models = active_models(env)
        ollama = [e for e in models if e.provider == "ollama"]
        assert ollama == []

    def test_no_ollama_actives_when_source_disabled(self):
        env = {"LLM_PROVIDER_SOURCE": "disabled"}
        models = active_models(env)
        ollama = [e for e in models if e.provider == "ollama"]
        assert ollama == []

    def test_best_content_is_none_when_no_cloud(self):
        env = {"LLM_PROVIDER_SOURCE": "none"}
        assert best("content", env) is None

    def test_best_content_falls_to_cloud_when_enabled(self):
        env = {
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "sk-test",
        }
        result = best("content", env)
        # Should be a cloud model (not ollama/)
        assert result is not None
        assert not result.startswith("ollama/")


# ── 3. Cloud enabled + ollama active → ordering ──────────────────────────────

class TestCloudEnabledOrderingWithOllama:
    """Ollama precedes cloud in active_models → ollama wins best()."""

    def setup_method(self):
        self.env = {
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "sk-test",
        }

    def test_active_models_has_both_ollama_and_openai(self):
        models = active_models(self.env)
        providers = {e.provider for e in models}
        assert "ollama" in providers
        assert "openai" in providers

    def test_ollama_comes_before_openai_in_active_list(self):
        models = active_models(self.env)
        providers_ordered = [e.provider for e in models]
        first_ollama = next(i for i, p in enumerate(providers_ordered) if p == "ollama")
        first_openai = next(i for i, p in enumerate(providers_ordered) if p == "openai")
        assert first_ollama < first_openai, (
            "Ollama entries must precede cloud entries (ollama is higher priority)"
        )

    def test_best_content_is_ollama_not_openai(self):
        result = best("content", self.env)
        assert result is not None
        assert result.startswith("ollama/"), (
            f"Expected ollama/ prefix (ollama has higher priority), got {result!r}"
        )


# ── 4. User override ──────────────────────────────────────────────────────────

class TestUserModelOverride:
    """OPENAI_USER_MODELS=gpt-5 activates exactly gpt-5 for openai."""

    def test_user_models_activates_exactly_named_model(self):
        env = {
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_USER_MODELS": "gpt-5",
        }
        models = active_models(env)
        openai_models = [e for e in models if e.provider == "openai"]
        assert len(openai_models) == 1
        assert openai_models[0].name == "gpt-5"

    def test_user_models_csv_activates_multiple(self):
        env = {
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_USER_MODELS": "gpt-5,gpt-4o",
        }
        models = active_models(env)
        openai_names = [e.name for e in models if e.provider == "openai"]
        assert set(openai_names) == {"gpt-5", "gpt-4o"}

    def test_user_models_overrides_defaults(self):
        env = {
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_USER_MODELS": "gpt-5",
        }
        models = active_models(env)
        openai_names = [e.name for e in models if e.provider == "openai"]
        # gpt-5-mini is a default_active, but OPENAI_USER_MODELS overrides that
        assert "gpt-5-mini" not in openai_names


# ── 5. Cloud enabled but no key ───────────────────────────────────────────────

class TestCloudNoKey:
    """Enabled flag set but no API key → no actives for that provider."""

    def test_no_actives_when_enabled_but_no_key(self):
        env = {
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "",
        }
        models = active_models(env)
        openai = [e for e in models if e.provider == "openai"]
        assert openai == []

    def test_no_actives_when_key_only_whitespace(self):
        env = {
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "   ",
        }
        models = active_models(env)
        openai = [e for e in models if e.provider == "openai"]
        assert openai == []

    def test_no_actives_when_not_enabled(self):
        env = {
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "false",
            "OPENAI_API_KEY": "sk-test",
        }
        models = active_models(env)
        openai = [e for e in models if e.provider == "openai"]
        assert openai == []

    def test_no_actives_when_key_missing_entirely(self):
        env = {
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            # OPENAI_API_KEY not set
        }
        models = active_models(env)
        openai = [e for e in models if e.provider == "openai"]
        assert openai == []


# ── 6. Custom/live-only ollama ────────────────────────────────────────────────

class TestCustomOllamaModels:
    """OLLAMA_CUSTOM_MODELS=mymodel:7b appears in active_models as synthesized."""

    def test_custom_model_appears_in_active_models(self):
        env = {
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
            "OLLAMA_CUSTOM_MODELS": "mymodel:7b",
        }
        models = active_models(env)
        names = _names(models)
        assert "mymodel:7b" in names

    def test_custom_model_is_synthesized_not_from_catalog(self):
        env = {
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
            "OLLAMA_CUSTOM_MODELS": "totally-custom:99b",
        }
        models = active_models(env)
        custom = next((e for e in models if e.name == "totally-custom:99b"), None)
        assert custom is not None
        assert custom.provider == "ollama"
        # Synthesized entries have content=8 (the live-only default)
        assert custom.content == 8

    def test_custom_model_combined_with_catalog_defaults(self):
        env = {
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
            "OLLAMA_CUSTOM_MODELS": "mymodel:7b",
        }
        models = active_models(env)
        names = _names(models)
        # Both catalog default and custom should be present
        assert "qwen3.8:latest" in names
        assert "mymodel:7b" in names

    def test_custom_models_csv(self):
        env = {
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
            "OLLAMA_CUSTOM_MODELS": "model-a:1b,model-b:3b",
        }
        models = active_models(env)
        names = _names(models)
        assert "model-a:1b" in names
        assert "model-b:3b" in names


# ── 7. ollama_tags auto-import ────────────────────────────────────────────────

class TestOllamaTagsAutoImport:
    """Passing ollama_tags= unions extra model names into the active set."""

    def test_tags_union_into_active_set(self):
        env = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
        models = active_models(env, ollama_tags=["extra:1b"])
        names = _names(models)
        assert "extra:1b" in names

    def test_tags_combined_with_defaults(self):
        env = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
        models = active_models(env, ollama_tags=["extra:1b"])
        names = _names(models)
        assert "qwen3.8:latest" in names
        assert "extra:1b" in names

    def test_tags_none_does_not_add_anything(self):
        env = {"LLM_PROVIDER_SOURCE": "ollama-container-cpu"}
        models_without = active_models(env)
        models_with_none = active_models(env, ollama_tags=None)
        assert _names(models_without) == _names(models_with_none)

    def test_tags_empty_list_adds_nothing(self):
        env = {"LLM_PROVIDER_SOURCE": "ollama-container-cpu"}
        models_no_tags = active_models(env, ollama_tags=None)
        models_empty_tags = active_models(env, ollama_tags=[])
        # Empty list should not add any new entries
        assert set(_names(models_empty_tags)) == set(_names(models_no_tags))

    def test_tags_are_synthesized_when_not_in_catalog(self):
        env = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
        models = active_models(env, ollama_tags=["unknown-model:2b"])
        custom = next((e for e in models if e.name == "unknown-model:2b"), None)
        assert custom is not None
        assert custom.provider == "ollama"
        assert custom.content == 8  # synthesized default

    def test_tags_already_in_catalog_uses_catalog_entry(self):
        env = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
        # qwen3.8:latest is already in the catalog with real capability values
        models = active_models(env, ollama_tags=["qwen3.8:latest"])
        qwen = next((e for e in models if e.name == "qwen3.8:latest"), None)
        assert qwen is not None
        # Should be the catalog entry, not a synthesized one (vision > 0 in catalog)
        assert qwen.content > 0


# ── 8. best('vision') ─────────────────────────────────────────────────────────

class TestBestVision:
    """best('vision') picks a vision-capable active model; None when none active."""

    def test_best_vision_default_config(self):
        # qwen3.8:latest appears in the vision section of ollama/models.yaml
        result = best("vision", {})
        assert result == "ollama/qwen3.8:latest"

    def test_best_vision_none_when_no_vision_capable_active(self):
        # Force ollama off and no cloud → no vision model
        env = {"LLM_PROVIDER_SOURCE": "none"}
        result = best("vision", env)
        assert result is None

    def test_best_vision_cloud_when_ollama_off(self):
        env = {
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "sk-test",
        }
        result = best("vision", env)
        # gpt-5 and gpt-5-mini are vision-capable in the openai catalog
        assert result is not None
        assert not result.startswith("ollama/")

    def test_best_vision_with_custom_only_model_is_none(self):
        # A pure-custom model with no vision capability should not win vision
        env = {
            "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
            "OLLAMA_USER_MODELS": "mynotvision:1b",
        }
        # OLLAMA_USER_MODELS overrides defaults, so only custom is active
        # Synthesized entries have vision=0
        result = best("vision", env)
        assert result is None


# ── 9. Embedding carve-out ────────────────────────────────────────────────────

class TestEmbeddingCarveOut:
    """resolved_defaults NEVER contains LITELLM_EMBEDDING_MODEL."""

    def test_resolved_defaults_never_has_embedding_key_default_env(self):
        defaults = resolved_defaults({})
        assert "LITELLM_EMBEDDING_MODEL" not in defaults

    def test_resolved_defaults_never_has_embedding_key_cloud_env(self):
        env = {
            "LLM_PROVIDER_SOURCE": "none",
            "LITELLM_OPENAI_ENABLED": "true",
            "OPENAI_API_KEY": "sk-test",
        }
        defaults = resolved_defaults(env)
        assert "LITELLM_EMBEDDING_MODEL" not in defaults

    def test_resolved_defaults_never_has_embedding_key_all_disabled(self):
        env = {"LLM_PROVIDER_SOURCE": "none"}
        defaults = resolved_defaults(env)
        assert "LITELLM_EMBEDDING_MODEL" not in defaults

    def test_resolved_defaults_exactly_two_keys(self):
        defaults = resolved_defaults({})
        assert len(defaults) == 2
        assert set(defaults.keys()) == {"LITELLM_DEFAULT_MODEL", "LITELLM_VISION_MODEL"}

    def test_resolved_defaults_exactly_two_keys_various_envs(self):
        for env in [
            {},
            {"LLM_PROVIDER_SOURCE": "none"},
            {"LLM_PROVIDER_SOURCE": "ollama-container-gpu"},
            {
                "LITELLM_OPENAI_ENABLED": "true",
                "OPENAI_API_KEY": "sk-x",
            },
        ]:
            defaults = resolved_defaults(env)
            assert set(defaults.keys()) == {"LITELLM_DEFAULT_MODEL", "LITELLM_VISION_MODEL"}, (
                f"resolved_defaults returned wrong keys for env={env!r}: {set(defaults.keys())!r}"
            )

    def test_best_embeddings_still_exists_and_works(self):
        # best('embeddings') is NOT wired into resolved_defaults, but it
        # should still function (used by B3 wizard step, B4 picker).
        result = best("embeddings", {})
        assert result is not None
        assert result.startswith("ollama/")


# ── 10. Embedding dimension safety ───────────────────────────────────────────

class TestEmbeddingDimensionSafety:
    """best('embeddings') must resolve to the 768-dim model, and
    embedding_dim_warning() must flag non-768 picks (pgvector(768) contract)."""

    def test_best_embeddings_is_768_dim_safe_default(self):
        # services/ollama/models.yaml lists nomic-embed-text (768-dim) FIRST,
        # so the catalog's "best" embedding model matches the memory_facts
        # vector(768) column. A regression here (e.g. re-ranking the 1536-dim
        # qwen3-embedding first) would silently break memory writes.
        assert best("embeddings", {}) == "ollama/nomic-embed-text"

    def test_warning_none_for_768_models(self):
        assert embedding_dim_warning("nomic-embed-text") is None
        assert embedding_dim_warning("ollama/nomic-embed-text") is None

    def test_warning_none_for_empty_or_unknown(self):
        assert embedding_dim_warning("") is None
        assert embedding_dim_warning(None) is None
        assert embedding_dim_warning("some/unlisted-embedder") is None

    def test_warning_flags_1536_dim_qwen(self):
        for name in ("qwen3-embedding:0.6b", "ollama/qwen3-embedding:0.6b"):
            msg = embedding_dim_warning(name)
            assert msg is not None
            assert "1536" in msg
            assert str(MEMORY_FACTS_EMBEDDING_DIM) in msg

    def test_warning_flags_3072_dim_openai(self):
        # canonical (bare) and provider-prefixed forms both resolve
        for name in ("text-embedding-3-large", "openai/text-embedding-3-large"):
            msg = embedding_dim_warning(name)
            assert msg is not None
            assert "3072" in msg

    def test_dim_sourced_from_yaml_catalog(self):
        # The dims come from services/*/models.yaml `dim:` fields, not a Python
        # map — declaring dim on a new entry is all it takes to extend the guard.
        assert dim_for_model_id("ollama/nomic-embed-text") == MEMORY_FACTS_EMBEDDING_DIM
        assert dim_for_model_id("ollama/qwen3-embedding:0.6b") == 1536
        assert dim_for_model_id("text-embedding-3-large") == 3072
        assert dim_for_model_id("text-embedding-3-small") == 1536

    def test_dim_none_for_non_embedding_or_unknown(self):
        # content/vision models declare no dim; unknown ids return None.
        assert dim_for_model_id("ollama/qwen3.8:latest") is None
        assert dim_for_model_id("totally-unknown-model") is None
        assert dim_for_model_id("") is None
        assert dim_for_model_id(None) is None

    def test_warning_tolerates_surrounding_whitespace(self):
        assert embedding_dim_warning("  ollama/qwen3-embedding:0.6b  ") is not None


# ── Embedding-name classification (non-catalog models) ───────────────────────

class TestLooksLikeEmbedding:
    """The heuristic that keeps non-catalog embedding models out of the
    content/vision pickers + resolver defaults."""

    def test_known_embedding_names(self):
        for n in (
            "mxbai-embed-large:latest",
            "nomic-embed-text",
            "ollama/snowflake-arctic-embed",
            "qwen3-embedding:0.6b",
            "openai/text-embedding-3-large",
            "granite-embedding:278m",
        ):
            assert looks_like_embedding(n) is True, n

    def test_content_names_are_not_embedding(self):
        for n in ("qwen3.8:latest", "gemma4:31b", "ollama/llama3.3", "gpt-4o", "deepseek-r1"):
            assert looks_like_embedding(n) is False, n

    def test_empty_and_none(self):
        assert looks_like_embedding("") is False
        assert looks_like_embedding(None) is False

    def test_synthesize_embedding_name_is_embeddings_not_content(self):
        # A live-discovered / custom embedding model must NOT be synthesized as a
        # content model (the bug: it surfaced as a chat-default candidate).
        e = _synthesize("ollama", "mxbai-embed-large:latest")
        assert e.content == 0 and e.embeddings == 1 and e.vision == 0

    def test_synthesize_non_embedding_name_is_content(self):
        e = _synthesize("ollama", "some-custom-chat-model:7b")
        assert e.content == 8 and e.embeddings == 0

    def test_no_catalog_content_model_looks_like_embedding(self):
        # Guards the heuristic against false positives: no curated *content*
        # model carries `embed` in its name, so the heuristic never demotes a
        # real chat model out of the content picker.
        catalogs = [ollama_entries()] + [cloud_entries(p) for p in ("openai", "anthropic", "openrouter")]
        for entries in catalogs:
            for e in entries:
                if getattr(e, "content", 0) > 0:
                    assert not looks_like_embedding(e.name), e.name
