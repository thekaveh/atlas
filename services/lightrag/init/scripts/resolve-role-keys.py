"""Route LightRAG roles and scope their API keys, then exec the LightRAG server.

The Ollama catalog's model-scoped request_defaults (today think: false) reach
a model only through LiteLLM, whose litellm-init renders them into that
model's litellm_params. A native LightRAG 1.5.4 binding does not send them:
its Ollama binding forwards only Ollama's options object, and think is a
top-level sibling of it (lightrag/llm/binding_options.py). So a role is kept
on LiteLLM, at the cost of one gateway hop, when it sets none of its own
binding, host or key, the base it would inherit points somewhere other than
LiteLLM, and its model declares catalog request_defaults (#658). A role's
explicit settings always win, and a role whose model declares none keeps the
base binding.

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

import json
import os
import sys
from collections.abc import Mapping
from urllib.parse import urlsplit

ROLES = ("EXTRACT", "KEYWORD", "QUERY")
LITELLM_HOST = ("litellm", 4000)
LITELLM_URL = "http://litellm:4000/v1"
# services/ollama/models.yaml, mounted read-only by the lightrag compose fragment.
CATALOG = "/atlas/ollama-models.yaml"
CATALOG_SECTIONS = ("content", "embeddings", "vision")


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


def _effective(env: Mapping[str, str], role: str, base: str, base_host: str) -> tuple[str, str]:
    """The role's binding and host, as LightRAG 1.5.4 resolves them."""
    binding = _binding(_value(env, f"{role}_LLM_BINDING")) or base
    own_binding = binding if binding != base else None
    return binding, _role_host(env, role, own_binding, base_host)


def _role_key(env: Mapping[str, str], role: str, base: str, base_host: str) -> str | None:
    """The key to give one role, or None to leave it; raise if it needs its own."""
    key_var = f"{role}_LLM_BINDING_API_KEY"
    binding, host = _effective(env, role, base, base_host)
    if _value(env, key_var) or binding == "bedrock":
        return None
    if is_litellm(host):
        return env.get("LITELLM_MASTER_KEY") or None
    if binding != base or host != base_host:
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


def catalog_request_defaults(path: str | os.PathLike[str]) -> dict[str, dict]:
    """Model name -> the request_defaults the Ollama model catalog declares.

    Sections merge as llm_catalog merges them. Only the Ollama catalog counts:
    litellm-init applies request_defaults to Ollama rows alone. An unreadable
    catalog declares nothing, so every role keeps the binding it inherits.
    """
    try:
        import yaml  # a lightrag-hku 1.5.4 dependency

        with open(path, encoding="utf-8") as handle:
            catalog = yaml.safe_load(handle)
        entries = [entry for section in CATALOG_SECTIONS for entry in catalog.get(section) or []]
    except Exception as exc:  # noqa: BLE001 - degrade to the inherited binding
        print(
            f"lightrag: model catalog {path} unreadable ({type(exc).__name__}); "
            "every role keeps the binding it inherits",
            file=sys.stderr, flush=True,
        )
        return {}
    defaults: dict[str, dict] = {}
    for entry in entries:
        if entry.get("request_defaults"):
            defaults.setdefault(entry["name"], {}).update(entry["request_defaults"])
    return defaults


def request_defaults(catalog: Mapping[str, dict], model: str) -> dict:
    """The model's catalog request defaults; LightRAG may name it by LiteLLM's ollama/ alias."""
    return dict(catalog.get(model) or catalog.get(model.removeprefix("ollama/")) or {})


def _role_model(env: Mapping[str, str], role: str) -> str:
    """The role's model, else the base LLM_MODEL that lightrag-init resolved."""
    return _value(env, f"{role}_LLM_MODEL") or _value(env, "LLM_MODEL") or ""


def route_roles(env: Mapping[str, str], catalog: Mapping[str, dict]) -> dict[str, str]:
    """The settings that keep a role on LiteLLM (#658).

    Only a role that sets none of its binding, host or key, under a base whose
    host is not LiteLLM, and whose model declares catalog request defaults.
    """
    _, base_host = _base(env)
    routes: dict[str, str] = {}
    if is_litellm(base_host):
        return routes
    for role in ROLES:
        own = any(_value(env, f"{role}_LLM_BINDING{part}") for part in ("", "_HOST", "_API_KEY"))
        if not own and request_defaults(catalog, _role_model(env, role)):
            routes[f"{role}_LLM_BINDING"] = "openai"
            routes[f"{role}_LLM_BINDING_HOST"] = LITELLM_URL
    return routes


def resolve_role_env(env: Mapping[str, str], catalog: Mapping[str, dict]) -> dict[str, str]:
    """The role settings to set before LightRAG starts: routes, then keys.

    Raise RoleKeyError for a role that needs a key of its own.
    """
    routes = route_roles(env, catalog)
    return {**routes, **resolve_role_keys({**env, **routes})}


def role_transports(env: Mapping[str, str], catalog: Mapping[str, dict]) -> dict[str, dict]:
    """Each role's effective transport and model, with the catalog request
    defaults its calls carry through LiteLLM or lose on a native binding.

    Hosts carry no userinfo and no key is ever included.
    """
    base, base_host = _base(env)
    transports: dict[str, dict] = {}
    for role in ROLES:
        binding, host = _effective(env, role, base, base_host)
        model = _role_model(env, role)
        declared = request_defaults(catalog, model)
        litellm = is_litellm(host)
        transports[role] = {
            "transport": "litellm" if litellm else "native",
            "binding": binding,
            "host": _display(host),
            "model": model,
            "request_defaults": declared if litellm else {},
            "unsent_request_defaults": {} if litellm else declared,
        }
    return transports


def describe(role: str, transport: Mapping[str, object]) -> str:
    """One line naming a role's transport, model and request defaults."""
    line = (
        f"{role} role: {transport['binding']} at {transport['host']} "
        f"({transport['transport']}), model {transport['model'] or 'resolved by lightrag-init'}, "
        f"request defaults {json.dumps(transport['request_defaults'])}"
    )
    if transport["unsent_request_defaults"]:
        line += (
            "; a native binding does not send the catalog request defaults "
            f"{json.dumps(transport['unsent_request_defaults'])}, so leave "
            f"LIGHTRAG_{role}_LLM_BINDING, _BINDING_HOST and _BINDING_API_KEY "
            "empty to keep them (#658)"
        )
    return line


def main() -> None:
    catalog = catalog_request_defaults(CATALOG)
    try:
        updates = resolve_role_env(os.environ, catalog)
    except RoleKeyError as exc:
        print(f"lightrag: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
    os.environ.update(updates)
    for role, transport in role_transports(os.environ, catalog).items():
        print(f"lightrag: {describe(role, transport)}", file=sys.stderr, flush=True)
    os.execvp(sys.executable, [sys.executable, "-m", "lightrag.api.lightrag_server"])


if __name__ == "__main__":
    main()
