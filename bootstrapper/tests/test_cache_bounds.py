"""Cache bounds: response cache, Redis eviction, and KV quantization.

Three settings that all exist for the same reason — memory is the scarce
resource on this stack — but which fail in different ways if reverted.

The Redis policy is the subtle one. Redis db 0 mixes LiteLLM's response
cache with n8n's BullMQ queue, Langfuse's queue
and the backend's media store. Under ``noeviction`` a full instance rejects
WRITES, so an oversized cache takes the queue down with it. ``volatile-lru``
evicts only keys carrying a TTL, including ordinary media records, leaving TTL-less entries
intact. With nothing volatile left it returns the same OOM error
``noeviction`` would, so the worst case is unchanged.
"""
from __future__ import annotations

from pathlib import Path

import pytest
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
    assert env.get("LITELLM_CACHE_CHAT") == "${LITELLM_CACHE_CHAT:-false}"


# ─── 2. Redis sheds cache, never the queue ───────────────────────────


def _redis_command() -> str:
    return " ".join(_services(REDIS_COMPOSE)["redis"]["command"])


def test_redis_evicts_only_expiring_keys() -> None:
    cmd = _redis_command()
    assert "volatile-lru" in cmd, (
        "db 0 mixes the LiteLLM cache with n8n/Kong/Langfuse/backend state. "
        "volatile-lru sheds only TTL-carrying keys; persistent "
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
    """Guard persistence and auth. The password reaches Redis through a config
    file, never argv (#1381). A blank password must stop Compose: an empty
    value would write `requirepass ""`, which turns authentication off."""
    cmd = _redis_command()
    assert "--appendonly yes" in cmd
    assert "--requirepass" not in cmd
    assert "requirepass" in cmd and "/dev/shm/redis.conf" in cmd
    assert "umask 077" in cmd
    env = _services(REDIS_COMPOSE)["redis"]["environment"]
    assert env["REDIS_PASSWORD"] == "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"


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


def test_the_response_cache_serves_embeddings_and_chat_only_on_opt_in():
    """A cached chat reply replaced a fresh hermes-agent or lightrag run (#1359)."""
    from utils.litellm_settings import base_settings

    default = base_settings({})["litellm_settings"]["cache_params"]["supported_call_types"]
    assert {"embedding", "aembedding", "rerank", "arerank"} <= set(default)
    assert not {"completion", "acompletion", "responses"} & set(default)
    chat = base_settings({"LITELLM_CACHE_CHAT": "true"})["litellm_settings"]["cache_params"]
    assert {"completion", "acompletion", "embedding"} <= set(chat["supported_call_types"])


def test_the_redis_docs_name_the_evictable_coordination_leases():
    """volatile-lru evicts any TTL key, and two leases carry one: an evicted
    lease lets a second worker claim a running job. The manifest called what
    a cap sheds "recomputable data" (#1040)."""
    backend = REPO_ROOT / "services" / "backend" / "app" / "app"
    rag_store = (backend / "rag_ingestion" / "store.py").read_text(encoding="utf-8")
    celery = (backend / "celery_app.py").read_text(encoding="utf-8")
    assert '_LEASE_PREFIX = "atlas:rag:execution:"' in rag_store
    assert "redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])" in celery
    assert 'f"atlas:celery:memory-execution:{task_id}"' in celery

    readme = (REPO_ROOT / "services" / "redis" / "README.md").read_text(encoding="utf-8")
    for prefix in ("atlas:rag:execution:", "atlas:celery:memory-execution:",
                   "atlas:rag:ingestions:", "atlas:media:operations:"):
        assert f"| `{prefix}" in readme

    manifest = yaml.safe_load((REPO_ROOT / "services" / "redis" / "service.yml").read_text())
    text = {e["name"]: e["description"] for e in manifest["env"]}
    assert "recomputable" not in text["REDIS_MAXMEMORY"]
    assert "leases" in text["REDIS_MAXMEMORY"] and "leases" in text["REDIS_MAXMEMORY_POLICY"]


def _redis_password_script() -> str:
    """The `sh -c` script that writes Redis's password file, taken from compose."""
    return _redis_command_list()[2]


def _redis_command_list():
    return _services(REDIS_COMPOSE)["redis"]["command"]


def _run_redis_script(tmp_path, password):
    """Run the compose script with a stub redis-server and a local config path."""
    import subprocess

    shm = tmp_path / "shm"
    shm.mkdir()
    stub = tmp_path / "bin"
    stub.mkdir()
    captured = tmp_path / "server-args.txt"
    (stub / "redis-server").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8")
    (stub / "redis-server").chmod(0o755)
    # Compose fills ${VAR:-default} and turns $$ into $ before the container sees it.
    import re
    script = re.sub(r"\$\{\w+:-([^}]*)\}", r"\1", _redis_password_script()).replace("$$", "$")
    script = script.replace("/dev/shm/redis.conf", str(shm / "redis.conf"))
    # The ownership repair and the privilege drop need root and the image's users;
    # this harness checks the password handling, so those steps are stripped.
    script = "\n".join(
        line for line in script.splitlines()
        if not line.lstrip().startswith(("find /data", "chown redis:redis"))
    ).replace("setpriv --reuid redis --regid redis --clear-groups ", "")
    env = {"PATH": f"{stub}:/usr/bin:/bin", "REDIS_PASSWORD": password, "CAPTURE": str(captured)}
    result = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    return result, shm / "redis.conf", captured


def _redis_quoted_value(body):
    """Decode a Redis config double-quoted value. Only \\\\ and \\" are escapes; an
    unescaped quote or any other backslash sequence is a config error."""
    out, index = [], 0
    while index < len(body):
        char = body[index]
        if char == '"':
            raise AssertionError(f"unescaped quote in config value: {body!r}")
        if char == "\\":
            nxt = body[index + 1:index + 2]
            if nxt not in ('"', "\\"):
                raise AssertionError(f"invalid escape in config value: {body!r}")
            out.append(nxt)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


@pytest.mark.parametrize("password", [
    "synthetic-safe", "synthetic two words", 'synthetic"quoted"secret',
    "back\\slash", "synthetic#secret", "synthetic'é雪", "-leading-dash",
])
def test_redis_password_file_round_trips_every_special_character(tmp_path, password):
    """The config line must parse back to the exact password. A `"` or `\` that
    the sed escapes incorrectly would split or truncate it (#1381 review)."""
    result, config, args = _run_redis_script(tmp_path, password)
    assert result.returncode == 0, result.stderr
    line = config.read_text(encoding="utf-8")
    assert line.startswith('requirepass "') and line.endswith('"\n')
    decoded = _redis_quoted_value(line[len('requirepass "'):-2])
    assert decoded == password
    assert password not in args.read_text(encoding="utf-8")


def test_redis_password_file_is_private_and_not_in_the_image_layer():
    """The file lives in /dev/shm (memory-backed) and is written mode 0600."""
    assert "umask 077" in _redis_password_script()
    assert "/dev/shm/redis.conf" in _redis_password_script()
    assert "/tmp/redis.conf" not in _redis_password_script()


def test_redis_refuses_a_password_with_a_newline(tmp_path):
    result, config, args = _run_redis_script(tmp_path, "line-one\nline-two")
    assert result.returncode != 0
    assert "must not contain a newline" in result.stderr
    assert not config.exists()
    assert not args.exists()


def test_redis_drops_to_the_redis_user_before_serving():
    """The command starts with `sh`, so the image entrypoint does not drop
    privileges for it. The script repairs /data ownership as root, then runs the
    server as `redis` with the same setpriv call the image entrypoint uses (#1381)."""
    script = _redis_password_script()
    assert "find /data \\! -user redis" in script
    assert "exec setpriv --reuid redis --regid redis --clear-groups redis-server" in script
    assert "user" not in _services(REDIS_COMPOSE)["redis"]
