"""Cache bounds: response cache, Redis eviction, and KV quantization.

Three settings that all exist for the same reason — memory is the scarce
resource on this stack — but which fail in different ways if reverted.

The Redis policy is the subtle one. Redis db 0 mixes LiteLLM's response
cache with n8n's BullMQ queue, Kong's rate-limit counters, Langfuse's queue
and the backend's media store. Under ``noeviction`` a full instance rejects
WRITES, so an oversized cache takes the queue down with it. ``volatile-lru``
evicts only keys carrying a TTL — the cache — leaving TTL-less queue entries
intact. With nothing volatile left it returns the same OOM error
``noeviction`` would, so the worst case is unchanged.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REDIS_COMPOSE = REPO_ROOT / "services" / "redis" / "compose.yml"
OLLAMA_COMPOSE = REPO_ROOT / "services" / "ollama" / "compose.yml"
LITELLM_COMPOSE = REPO_ROOT / "services" / "litellm" / "compose.yml"


def _services(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


# ─── 1. LiteLLM response cache is bounded and namespaced ─────────────


def test_cache_ttl_defaults_to_an_hour_not_litellms_sixty_seconds() -> None:
    from utils.litellm_settings import DEFAULT_CACHE_TTL_SECONDS, base_settings

    assert DEFAULT_CACHE_TTL_SECONDS == 3600
    params = base_settings({})["litellm_settings"]["cache_params"]
    assert params["ttl"] == 3600, (
        "Without an explicit ttl LiteLLM falls back to BaseCache's 60s, which "
        "is too short for a response cache to pay for itself."
    )


def test_cache_ttl_is_operator_configurable() -> None:
    from utils.litellm_settings import base_settings

    params = base_settings({"LITELLM_CACHE_TTL": "120"})["litellm_settings"]["cache_params"]
    assert params["ttl"] == 120


def test_a_broken_ttl_falls_back_instead_of_failing_the_render() -> None:
    """An unparseable tuning knob must never stop the gateway from starting."""
    from utils.litellm_settings import base_settings

    for bad in ("", "   ", "abc", "0", "-5"):
        params = base_settings({"LITELLM_CACHE_TTL": bad})["litellm_settings"]["cache_params"]
        assert params["ttl"] == 3600, f"{bad!r} should have fallen back"


def test_the_ttl_is_a_number_not_an_env_reference() -> None:
    """``os.environ/...`` resolves to a STRING; ttl must be numeric, so this
    one value is deliberately baked at render time."""
    from utils.litellm_settings import base_settings

    ttl = base_settings({"LITELLM_CACHE_TTL": "900"})["litellm_settings"]["cache_params"]["ttl"]
    assert isinstance(ttl, int)


def test_cache_keys_are_namespaced() -> None:
    """db 0 is shared; an unnamespaced cache cannot be scanned or dropped
    without touching a neighbour's keyspace."""
    from utils.litellm_settings import base_settings

    params = base_settings({})["litellm_settings"]["cache_params"]
    assert params["namespace"] == "litellm.cache"
    custom = base_settings({"LITELLM_CACHE_NAMESPACE": "gw"})["litellm_settings"]["cache_params"]
    assert custom["namespace"] == "gw"


def test_the_init_container_receives_the_cache_vars() -> None:
    """base_settings runs inside litellm-init, so the vars must reach it."""
    env = _services(LITELLM_COMPOSE)["litellm-init"]["environment"]
    assert env.get("LITELLM_CACHE_TTL") == "${LITELLM_CACHE_TTL:-3600}"
    assert env.get("LITELLM_CACHE_NAMESPACE") == "${LITELLM_CACHE_NAMESPACE:-litellm.cache}"


# ─── 2. Redis sheds cache, never the queue ───────────────────────────


def _redis_command() -> str:
    return " ".join(str(_services(REDIS_COMPOSE)["redis"]["command"]).split())


def test_redis_evicts_only_expiring_keys() -> None:
    cmd = _redis_command()
    assert "volatile-lru" in cmd, (
        "db 0 mixes the LiteLLM cache with n8n/Kong/Langfuse/backend state. "
        "volatile-lru sheds only TTL-carrying keys (the cache); the queue "
        "entries carry no TTL and survive."
    )
    assert "noeviction" not in cmd, (
        "noeviction rejects WRITES when full, so an oversized cache takes the "
        "queue down with it."
    )


def test_redis_memory_cap_is_configurable_and_defaults_to_unlimited() -> None:
    """0 preserves the historical behaviour; a cap is opt-in so this change
    cannot shrink anyone's working set on upgrade."""
    cmd = _redis_command()
    assert "--maxmemory ${REDIS_MAXMEMORY:-0}" in cmd
    assert "--maxmemory-policy ${REDIS_MAXMEMORY_POLICY:-volatile-lru}" in cmd


def test_redis_keeps_persistence_and_auth() -> None:
    """Guard the whole command line — it is one string, easy to clobber."""
    cmd = _redis_command()
    assert "--appendonly yes" in cmd
    assert "--requirepass ${REDIS_PASSWORD}" in cmd


# ─── 3. Ollama KV cache is quantized, and actually active ────────────


def test_ollama_quantizes_its_kv_cache() -> None:
    env = _services(OLLAMA_COMPOSE)["ollama"]["environment"]
    assert env.get("OLLAMA_KV_CACHE_TYPE") == "${OLLAMA_KV_CACHE_TYPE:-q8_0}", (
        "The attention KV cache is the dominant per-slot memory cost and "
        "OLLAMA_NUM_PARALLEL multiplies it; q8_0 roughly halves it."
    )


def test_flash_attention_is_pinned_because_quantization_needs_it() -> None:
    """Quantization is a NO-OP without flash attention. Leaving it to
    autodetection would let the memory setting silently do nothing — worse
    than not setting it at all, because it reads as configured."""
    env = _services(OLLAMA_COMPOSE)["ollama"]["environment"]
    assert env.get("OLLAMA_FLASH_ATTENTION") == "${OLLAMA_FLASH_ATTENTION:-1}"


def test_the_parallel_settings_still_stand() -> None:
    """KV quantization and NUM_PARALLEL are the two halves of the same
    memory budget (#849) — neither should be tuned without the other in view."""
    env = _services(OLLAMA_COMPOSE)["ollama"]["environment"]
    assert env.get("OLLAMA_NUM_PARALLEL") == "${OLLAMA_NUM_PARALLEL:-8}"
    assert env.get("OLLAMA_MAX_LOADED_MODELS") == "${OLLAMA_MAX_LOADED_MODELS:-2}"
