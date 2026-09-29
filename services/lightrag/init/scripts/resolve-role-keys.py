"""Scope LightRAG role API keys to LiteLLM, then exec the LightRAG server.

Compose cannot express "default a role key to the LiteLLM master key only when
that role talks to LiteLLM", so the lightrag container runs this first (#1271).
It mirrors LightRAG 1.5.4's own resolution of each role's effective host:

- a role on its own binding with no host uses get_default_host(binding),
  which is LLM_BINDING_HOST for every binding except azure_openai, whose
  default is AZURE_OPENAI_ENDPOINT (lightrag/api/config.py);
- a role on the base binding with no host uses the base LLM_BINDING_HOST
  (lightrag/api/lightrag_server.py).

A role whose key is unset gets LITELLM_MASTER_KEY only when that effective
host is the in-network LiteLLM (litellm:4000). A role that has its own binding
or its own host, and resolves anywhere else, stops here naming the variable to
set. A role on its own binding would otherwise stop inside LightRAG, which
requires a key for every non-Bedrock role on its own binding (native Ollama
included). A role on the base binding with its own host would otherwise
inherit the base key, which is the master key (lightrag_server.py#L1638,
#1291). A role that mirrors the base binding and host is left alone: what it
sends is the base configuration's concern. Bedrock roles never get a key;
LightRAG rejects one and signs with AWS credentials instead.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from urllib.parse import urlsplit

ROLES = ("EXTRACT", "KEYWORD", "QUERY")
LITELLM_HOST = ("litellm", 4000)


class RoleKeyError(RuntimeError):
    """A role pointed away from LiteLLM has no API key."""


def _value(env: Mapping[str, str], name: str) -> str | None:
    """LightRAG's get_env_value(special_none=True): unset or "None" -> None."""
    value = env.get(name)
    return None if value is None or value == "None" else value


def _binding(value: str | None) -> str | None:
    return "bedrock" if value == "aws_bedrock" else value


def default_host(binding: str, env: Mapping[str, str]) -> str:
    """get_default_host(): only azure_openai differs, and no default is LiteLLM."""
    if binding == "azure_openai":
        return env.get("AZURE_OPENAI_ENDPOINT", "https://api.openai.com/v1")
    return env.get("LLM_BINDING_HOST", "http://localhost:11434")


def is_litellm(host: str) -> bool:
    try:
        parts = urlsplit(host)
        return (parts.hostname, parts.port) == LITELLM_HOST
    except ValueError:
        return False


def _display(host: str) -> str:
    """scheme://host[:port] with no userinfo, or a neutral placeholder."""
    try:
        parts = urlsplit(host)
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "an unparseable host"
    if not (parts.scheme and parts.hostname):
        return "an unparseable host"
    return f"{parts.scheme}://{parts.hostname}{port}"


def _base(env: Mapping[str, str]) -> tuple[str, str]:
    """The base binding and host, as LightRAG's parse_args() settles them."""
    base = _binding(_value(env, "LLM_BINDING")) or "ollama"
    if base == "openai-ollama":  # LightRAG splits it into openai LLM + ollama embedding
        base = "openai"
    base_host = _value(env, "LLM_BINDING_HOST")
    return base, default_host(base, env) if base_host is None else base_host


def _role_host(env: Mapping[str, str], role: str, own_binding: str | None, base_host: str) -> str:
    """The role's host, else its own binding's default host, else the base host."""
    host = _value(env, f"{role}_LLM_BINDING_HOST")
    if host:
        return host
    return default_host(own_binding, env) if own_binding else base_host


def _role_key(env: Mapping[str, str], role: str, base: str, base_host: str) -> str | None:
    """The key to give one role, or None to leave it; raise if it needs its own."""
    key_var = f"{role}_LLM_BINDING_API_KEY"
    role_binding = _binding(_value(env, f"{role}_LLM_BINDING"))
    binding = role_binding or base
    if _value(env, key_var) or binding == "bedrock":
        return None
    own_binding = bool(role_binding) and role_binding != base
    host = _role_host(env, role, binding if own_binding else None, base_host)
    if is_litellm(host):
        return env.get("LITELLM_MASTER_KEY") or None
    if own_binding or host != base_host:
        raise RoleKeyError(
            f"LightRAG {role} role uses binding {binding!r} at "
            f"{_display(host)}, which is not the in-network LiteLLM, and has "
            f"no API key. Set LIGHTRAG_{key_var} in .env (native Ollama "
            "ignores the value, but LightRAG requires one). Atlas defaults a "
            "role key to the LiteLLM master key only for a role that talks "
            "to LiteLLM."
        )
    return None


def resolve_role_keys(env: Mapping[str, str]) -> dict[str, str]:
    """Return the role keys to set; raise RoleKeyError for a missing one."""
    base, base_host = _base(env)
    keys = {f"{role}_LLM_BINDING_API_KEY": _role_key(env, role, base, base_host) for role in ROLES}
    return {name: key for name, key in keys.items() if key}


def main() -> None:
    try:
        keys = resolve_role_keys(os.environ)
    except RoleKeyError as exc:
        print(f"lightrag: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
    os.environ.update(keys)
    os.execvp(sys.executable, [sys.executable, "-m", "lightrag.api.lightrag_server"])


if __name__ == "__main__":
    main()
