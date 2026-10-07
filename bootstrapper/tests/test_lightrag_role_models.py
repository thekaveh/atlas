"""LightRAG role-specific LLM model configuration tests."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
LIGHTRAG_MANIFEST = REPO_ROOT / "services" / "lightrag" / "service.yml"
LIGHTRAG_COMPOSE = REPO_ROOT / "services" / "lightrag" / "compose.yml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke-lightrag-role-models.sh"
RESOLVER = REPO_ROOT / "services" / "lightrag" / "init" / "scripts" / "resolve-role-keys.py"
OLLAMA_CATALOG = REPO_ROOT / "services" / "ollama" / "models.yaml"

ROLE_INPUTS = {
    "LIGHTRAG_EXTRACT_LLM_MODEL": {"native": "EXTRACT_LLM_MODEL", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_MODEL": {"native": "KEYWORD_LLM_MODEL", "secret": False},
    "LIGHTRAG_QUERY_LLM_MODEL": {"native": "QUERY_LLM_MODEL", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_BINDING": {"native": "EXTRACT_LLM_BINDING", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_BINDING": {"native": "KEYWORD_LLM_BINDING", "secret": False},
    "LIGHTRAG_QUERY_LLM_BINDING": {"native": "QUERY_LLM_BINDING", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_BINDING_HOST": {"native": "EXTRACT_LLM_BINDING_HOST", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_BINDING_HOST": {"native": "KEYWORD_LLM_BINDING_HOST", "secret": False},
    "LIGHTRAG_QUERY_LLM_BINDING_HOST": {"native": "QUERY_LLM_BINDING_HOST", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY": {
        "native": "EXTRACT_LLM_BINDING_API_KEY",
        "secret": True,
    },
    "LIGHTRAG_KEYWORD_LLM_BINDING_API_KEY": {
        "native": "KEYWORD_LLM_BINDING_API_KEY",
        "secret": True,
    },
    "LIGHTRAG_QUERY_LLM_BINDING_API_KEY": {
        "native": "QUERY_LLM_BINDING_API_KEY",
        "secret": True,
    },
    "LIGHTRAG_EXTRACT_MAX_ASYNC_LLM": {"native": "EXTRACT_MAX_ASYNC_LLM", "secret": False},
    "LIGHTRAG_KEYWORD_MAX_ASYNC_LLM": {"native": "KEYWORD_MAX_ASYNC_LLM", "secret": False},
    "LIGHTRAG_QUERY_MAX_ASYNC_LLM": {"native": "QUERY_MAX_ASYNC_LLM", "secret": False},
    "LIGHTRAG_EXTRACT_LLM_TIMEOUT": {"native": "EXTRACT_LLM_TIMEOUT", "secret": False},
    "LIGHTRAG_KEYWORD_LLM_TIMEOUT": {"native": "KEYWORD_LLM_TIMEOUT", "secret": False},
    "LIGHTRAG_QUERY_LLM_TIMEOUT": {"native": "QUERY_LLM_TIMEOUT", "secret": False},
}

# #796: EXTRACT generation caps for a native-Ollama EXTRACT binding. LightRAG
# 1.5.4 forwards a non-integer value (even "") to Ollama as a string, which
# rejects every call, so each default and Compose fallback must be an integer.
EXTRACT_OLLAMA_CAPS = {
    "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT": {
        "native": "EXTRACT_OLLAMA_LLM_NUM_PREDICT",
        "default": "4096",
        "compose": "${LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT:-4096}",
    },
    "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_CTX": {
        "native": "EXTRACT_OLLAMA_LLM_NUM_CTX",
        "default": "16384",
        "compose": "${LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_CTX:-16384}",
    },
}

QUERY_INPUTS = {
    "LIGHTRAG_QUERY_ENABLE_RERANK": {
        "native": "RERANK_BY_DEFAULT",
        "default": "false",
        "compose": "${LIGHTRAG_QUERY_ENABLE_RERANK:-false}",
    },
    "LIGHTRAG_QUERY_TOP_K": {
        "native": "TOP_K",
        "default": "10",
        "compose": "${LIGHTRAG_QUERY_TOP_K:-10}",
    },
    "LIGHTRAG_QUERY_CHUNK_TOP_K": {
        "native": "CHUNK_TOP_K",
        "default": "5",
        "compose": "${LIGHTRAG_QUERY_CHUNK_TOP_K:-5}",
    },
    "LIGHTRAG_QUERY_MAX_TOTAL_TOKENS": {
        "native": "MAX_TOTAL_TOKENS",
        "default": "12000",
        "compose": "${LIGHTRAG_QUERY_MAX_TOTAL_TOKENS:-12000}",
    },
}


def _manifest_env_by_name() -> dict[str, dict]:
    data = yaml.safe_load(LIGHTRAG_MANIFEST.read_text(encoding="utf-8"))
    return {entry["name"]: entry for entry in data["env"]}


def _compose_lightrag_environment() -> dict[str, str]:
    data = yaml.safe_load(LIGHTRAG_COMPOSE.read_text(encoding="utf-8"))
    return data["services"]["lightrag"]["environment"]


def test_lightrag_manifest_declares_role_llm_inputs():
    env_by_name = _manifest_env_by_name()

    for atlas_name, meta in ROLE_INPUTS.items():
        assert atlas_name in env_by_name
        assert env_by_name[atlas_name].get("default", "") == ""
        if meta["secret"]:
            assert env_by_name[atlas_name].get("secret") is True


def test_lightrag_manifest_declares_query_controls():
    env_by_name = _manifest_env_by_name()

    for atlas_name, meta in QUERY_INPUTS.items():
        assert atlas_name in env_by_name
        assert env_by_name[atlas_name].get("default", "") == meta["default"]


def test_lightrag_compose_maps_role_inputs_to_native_env_names():
    env = _compose_lightrag_environment()

    for atlas_name, meta in ROLE_INPUTS.items():
        native_name = meta["native"]
        assert native_name in env
        expected = meta.get("compose", f"${{{atlas_name}:-}}")
        assert env[native_name] == expected


def test_lightrag_role_api_keys_resolve_at_start_not_in_compose():
    """#1271: Compose passes each role key through as set, with no master-key
    default, and the entrypoint execs the read-only mounted resolver, which
    decides the LiteLLM fallback per role before LightRAG starts."""
    service = yaml.safe_load(LIGHTRAG_COMPOSE.read_text(encoding="utf-8"))["services"]["lightrag"]
    env = service["environment"]

    assert (
        [env[f"{role}_LLM_BINDING_API_KEY"] for role in _ROLES],
        env["LITELLM_MASTER_KEY"],
        env["LLM_BINDING_API_KEY"],
        service["entrypoint"][-1].endswith("; exec python /atlas/resolve-role-keys.py"),
        "./init/scripts/resolve-role-keys.py:/atlas/resolve-role-keys.py:ro" in service["volumes"],
    ) == (
        [f"${{LIGHTRAG_{role}_LLM_BINDING_API_KEY:-}}" for role in _ROLES],
        "${LITELLM_MASTER_KEY}",
        "${LITELLM_MASTER_KEY}",
        True,
        True,
    )


_ROLES = ("EXTRACT", "KEYWORD", "QUERY")
# Not secret-shaped on purpose: the value only has to be recognisable.
_MASTER_KEY = "litellm-master-canary"
_LITELLM_BASE = {
    "LLM_BINDING": "openai",
    "LLM_BINDING_HOST": "http://litellm:4000/v1",
    "LITELLM_MASTER_KEY": _MASTER_KEY,
}


# Host userinfo the error message must not echo; assembled so the source holds
# no credential-shaped URL.
_USERINFO = ":".join(("operator", "canary"))


def _resolver():
    spec = importlib.util.spec_from_file_location("lightrag_resolve_role_keys", RESOLVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _master_for(*roles: str) -> dict[str, str]:
    return {f"{role}_LLM_BINDING_API_KEY": _MASTER_KEY for role in roles}


@pytest.mark.parametrize(
    ("role_env", "expected"),
    [
        # Every role left on the base binding and host talks to LiteLLM.
        ({}, _master_for(*_ROLES)),
        # #796 AC5: EXTRACT pointed at LiteLLM explicitly.
        ({"EXTRACT_LLM_BINDING": "openai", "EXTRACT_LLM_BINDING_HOST": "http://litellm:4000/v1"},
         _master_for(*_ROLES)),
        # An explicit key passes through untouched.
        ({"EXTRACT_LLM_BINDING": "ollama",
          "EXTRACT_LLM_BINDING_HOST": "http://host.docker.internal:11434",
          "EXTRACT_LLM_BINDING_API_KEY": "ollama"}, _master_for("KEYWORD", "QUERY")),
        # LightRAG's get_default_host: an empty ollama host is LLM_BINDING_HOST.
        ({"EXTRACT_LLM_BINDING": "ollama"}, _master_for(*_ROLES)),
        # LightRAG reads the literal string "None" as unset.
        ({"QUERY_LLM_BINDING_API_KEY": "None"}, _master_for(*_ROLES)),
        # Bedrock rejects a role key and signs with AWS credentials instead.
        ({"KEYWORD_LLM_BINDING": "aws_bedrock"}, _master_for("EXTRACT", "QUERY")),
        # No master key, nothing to fall back to.
        ({"LITELLM_MASTER_KEY": ""}, {}),
        # Roles that mirror a non-LiteLLM base are the base's concern; LightRAG
        # splits openai-ollama into an openai LLM binding, so QUERY=openai is
        # the base binding, not its own.
        ({"LLM_BINDING": "openai-ollama", "LLM_BINDING_HOST": "http://host.docker.internal:11434",
          "QUERY_LLM_BINDING": "openai"}, {}),
    ],
)
def test_role_keys_default_to_master_only_for_litellm_hosts(role_env, expected):
    """#1271 AC1: the resolver hands a role the master key only when that
    role's effective host, resolved as LightRAG 1.5.4 does, is LiteLLM."""
    assert _resolver().resolve_role_keys({**_LITELLM_BASE, **role_env}) == expected


@pytest.mark.parametrize(
    ("role", "role_env"),
    [
        ("EXTRACT", {"EXTRACT_LLM_BINDING": "ollama",
                     "EXTRACT_LLM_BINDING_HOST": "http://host.docker.internal:11434"}),
        # An empty azure_openai host is AZURE_OPENAI_ENDPOINT, never LiteLLM,
        # so a literal "empty host means LiteLLM" check would leak the key.
        ("KEYWORD", {"KEYWORD_LLM_BINDING": "azure_openai",
                     "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com"}),
        ("QUERY", {"QUERY_LLM_BINDING": "gemini",
                   "QUERY_LLM_BINDING_HOST": f"https://{_USERINFO}@llm.example.com/v1"}),
        # #1291: on the base binding but its own host, LightRAG would hand the
        # role the base key, which is the master key.
        ("QUERY", {"QUERY_LLM_BINDING_HOST": "https://api.example.com/v1"}),
        # Scheme-less userinfo and a malformed port still give the actionable
        # message, not a traceback or an echoed credential.
        ("KEYWORD", {"KEYWORD_LLM_BINDING": "ollama",
                     "KEYWORD_LLM_BINDING_HOST": f"{_USERINFO}@llm.example.com:8080"}),
        ("EXTRACT", {"EXTRACT_LLM_BINDING": "ollama",
                     "EXTRACT_LLM_BINDING_HOST": "http://ollama:11434x"}),
    ],
)
def test_role_pointed_away_from_litellm_without_key_stops_naming_the_variable(role, role_env):
    """#1271 AC2 and #1291: a role with its own binding or host that resolves
    anywhere but LiteLLM, with no key, stops at start naming the variable, and
    the message carries no userinfo from the host."""
    resolver = _resolver()
    with pytest.raises(resolver.RoleKeyError) as raised:
        resolver.resolve_role_keys({**_LITELLM_BASE, **role_env})

    message = str(raised.value)
    assert (f"LIGHTRAG_{role}_LLM_BINDING_API_KEY" in message, _USERINFO in message) == (
        True, False)


# #658: a base binding on a native provider. The hosts are the Ollama
# endpoints Atlas derives for the in-stack and host sources
# (services/ollama/service.yml runtime_sc).
_NATIVE_HOSTS = [
    pytest.param("http://ollama:11434", id="ollama-container"),
    pytest.param("http://host.docker.internal:11434", id="ollama-localhost"),
]
_NATIVE_BASE = {
    "LLM_BINDING": "ollama",
    "LLM_BINDING_HOST": "http://host.docker.internal:11434",
    "LITELLM_MASTER_KEY": _MASTER_KEY,
}
# The catalog chat model that declares request_defaults {think: false}.
_THINK_OFF_MODEL = "qwen3.8:latest"


def _catalog(path: Path = OLLAMA_CATALOG) -> dict[str, dict]:
    return _resolver().catalog_request_defaults(path)


@pytest.mark.parametrize("host", _NATIVE_HOSTS)
@pytest.mark.parametrize("role", ["KEYWORD", "QUERY"])
def test_role_inheriting_a_native_base_keeps_catalog_request_defaults(role, host):
    """#658 AC1: a KEYWORD or QUERY role whose model declares {think: false}
    and would inherit native Ollama, in the stack or on the host
    (LLM_PROVIDER_SOURCE=ollama-localhost), stays on LiteLLM with the master
    key, so the default still applies. EXTRACT, whose model declares none,
    keeps the native binding."""
    resolver = _resolver()
    catalog = _catalog()
    env = {**_NATIVE_BASE, "LLM_BINDING_HOST": host, "LLM_MODEL": "mistral-small3.2:24b",
           f"{role}_LLM_MODEL": _THINK_OFF_MODEL}
    resolved = {**env, **resolver.resolve_role_env(env, catalog)}
    transports = resolver.role_transports(resolved, catalog)

    assert (
        [resolved[f"{role}_LLM_BINDING{part}"] for part in ("", "_HOST", "_API_KEY")],
        transports[role]["transport"], transports[role]["request_defaults"],
        transports["EXTRACT"]["transport"], "EXTRACT_LLM_BINDING" in resolved,
    ) == (
        ["openai", "http://litellm:4000/v1", _MASTER_KEY],
        "litellm", {"think": False},
        "native", False,
    )


def test_base_model_alias_keeps_every_inheriting_role_on_litellm():
    """#658: lightrag-init may resolve the base model to LiteLLM's ollama/
    alias; the catalog lookup still finds it, for every inheriting role."""
    routed = {
        **{f"{role}_LLM_BINDING": "openai" for role in _ROLES},
        **{f"{role}_LLM_BINDING_HOST": "http://litellm:4000/v1" for role in _ROLES},
        **_master_for(*_ROLES),
    }
    env = {**_NATIVE_BASE, "LLM_MODEL": f"ollama/{_THINK_OFF_MODEL}"}

    assert _resolver().resolve_role_env(env, _catalog()) == routed


def _catalog_with_plain_chat(tmp_path: Path) -> Path:
    """The Ollama catalog plus a chat entry that declares no request_defaults."""
    data = yaml.safe_load(OLLAMA_CATALOG.read_text(encoding="utf-8"))
    data["content"].append({
        "name": "chat-without-defaults", "metadata_version": 1, "kind": "chat",
        "adapter": "ollama_chat", "capabilities": {"chat": True},
    })
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("base", "before"),
    [
        pytest.param(_LITELLM_BASE, _master_for(*_ROLES), id="litellm-base"),
        pytest.param(_NATIVE_BASE, {}, id="native-base"),
    ],
)
@pytest.mark.parametrize("model", ["nomic-embed-text", "ollama/nomic-embed-text", "chat-without-defaults"])
def test_models_without_request_defaults_resolve_as_before(base, before, model, tmp_path):
    """#658 AC4: an embedding entry, or a chat entry with no request_defaults,
    resolves to the pre-#658 value, which is what the key resolution alone
    returns, on either base."""
    resolver = _resolver()
    env = {**base, "LLM_MODEL": model}
    resolved = resolver.resolve_role_env(env, _catalog(_catalog_with_plain_chat(tmp_path)))

    assert (resolved, resolver.resolve_role_keys(env)) == (before, before)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("KEYWORD_LLM_BINDING", "ollama"),
        # The base host itself, so the role's own host does not stop it (#1291).
        ("KEYWORD_LLM_BINDING_HOST", _NATIVE_BASE["LLM_BINDING_HOST"]),
        ("KEYWORD_LLM_BINDING_API_KEY", "ollama"),
    ],
)
def test_explicit_role_settings_win_over_catalog_routing(name, value):
    """#658 AC5: a role that sets its own binding, host or key is returned
    unchanged, while a role that inherits the same base is still routed."""
    env = {**_NATIVE_BASE, "LLM_MODEL": _THINK_OFF_MODEL, name: value}
    updates = _resolver().resolve_role_env(env, _catalog())

    assert (
        {**env, **updates}[name],
        [key for key in updates if key.startswith("KEYWORD_")],
        updates["QUERY_LLM_BINDING"],
    ) == (value, [], "openai")


def test_unreadable_catalog_keeps_every_role_on_its_inherited_binding(tmp_path):
    """#658: with no catalog to read, no role is routed, as before #658."""
    env = {**_NATIVE_BASE, "LLM_MODEL": _THINK_OFF_MODEL}
    resolver = _resolver()

    assert resolver.resolve_role_env(env, _catalog(tmp_path / "missing.yaml")) == {}


def test_entrypoint_routes_roles_and_logs_transports_without_keys(monkeypatch, capsys):
    """#658 AC1/AC6: as the entrypoint, the resolver applies the routing before
    exec-ing LightRAG and logs each role's transport and request defaults, with
    no key value in the log."""
    resolver = _resolver()
    monkeypatch.setattr(resolver, "CATALOG", str(OLLAMA_CATALOG))
    monkeypatch.setattr(resolver.os, "execvp", lambda *_args: None)
    role_vars = {f"{role}_LLM_{part}": "" for role in _ROLES
                 for part in ("MODEL", "BINDING", "BINDING_HOST", "BINDING_API_KEY")}
    for name, value in {**role_vars, **_NATIVE_BASE, "LLM_MODEL": _THINK_OFF_MODEL}.items():
        monkeypatch.setenv(name, value)

    resolver.main()

    log = capsys.readouterr().err
    assert (
        os.environ["KEYWORD_LLM_BINDING_HOST"],
        log.count('(litellm), model qwen3.8:latest, request defaults {"think": false}'),
        _MASTER_KEY in log,
    ) == ("http://litellm:4000/v1", 3, False)


def test_extract_ollama_caps_are_declared_in_all_three_files():
    """#796 AC1/AC2: the caps reach a native-Ollama EXTRACT binding as
    EXTRACT_OLLAMA_LLM_NUM_PREDICT / _NUM_CTX from the manifest, Compose and
    .env.example alike, with an integer output cap inside 2k-4k."""
    env_by_name = _manifest_env_by_name()
    compose = _compose_lightrag_environment()
    example = ENV_EXAMPLE.read_text(encoding="utf-8")

    for atlas_name, meta in EXTRACT_OLLAMA_CAPS.items():
        assert env_by_name[atlas_name]["default"] == meta["default"]
        assert compose[meta["native"]] == meta["compose"]
        assert f"\n{atlas_name}={meta['default']}\n" in example
    assert 2048 <= int(EXTRACT_OLLAMA_CAPS["LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT"]["default"]) <= 4096
    for path in (LIGHTRAG_COMPOSE, LIGHTRAG_MANIFEST, ENV_EXAMPLE):
        assert "OLLAMA_LLM_NUM_PREDICT" in path.read_text(encoding="utf-8"), path


def test_lightrag_compose_maps_query_controls_to_native_env_names():
    env = _compose_lightrag_environment()

    for meta in QUERY_INPUTS.values():
        assert env[meta["native"]] == meta["compose"]


def test_lightrag_compose_keeps_base_models_init_resolved():
    env = _compose_lightrag_environment()

    assert "LLM_MODEL" not in env
    assert "EMBEDDING_MODEL" not in env
    assert "EMBEDDING_DIM" not in env


def test_lightrag_role_smoke_fails_when_model_evidence_is_missing():
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "missing expected EXTRACT model" in script
    assert "missing expected QUERY model" in script
    assert "pass criteria:" not in script
    assert "--since=10m" not in script
    assert "--since=\"$smoke_started_at\"" in script


def _docker_available() -> bool:
    return shutil.which("docker") is not None


_needs_docker = pytest.mark.skipif(
    not _docker_available() or not ENV_EXAMPLE.is_file(),
    reason="docker not on PATH or .env.example missing",
)


def _render_lightrag_environment(tmp_path: Path, overrides: dict[str, str]) -> dict[str, str]:
    """``docker compose config`` over .env.example with ``overrides`` applied."""
    env_file = tmp_path / ".env"
    out_lines = []
    seen = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            out_lines.append(line)
            continue
        key = line.split("=", 1)[0]
        if key in overrides:
            out_lines.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            out_lines.append(line)
    for key, value in overrides.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")
    env_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-p",
            "atlas",
            "-f",
            str(COMPOSE),
            "config",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    return rendered["services"]["lightrag"]["environment"]


_LIGHTRAG_ON = {
    "PROJECT_NAME": "atlas",
    "LIGHTRAG_SOURCE": "container",
    "LIGHTRAG_SCALE": "1",
    "LIGHTRAG_INIT_SCALE": "1",
}


@_needs_docker
def test_lightrag_role_models_render_into_container_environment(tmp_path: Path):
    env = _render_lightrag_environment(tmp_path, {
        **_LIGHTRAG_ON,
        "LIGHTRAG_EXTRACT_LLM_MODEL": "mistral-small3.2:24b",
        "LIGHTRAG_KEYWORD_LLM_MODEL": "mistral-small3.2:24b",
        "LIGHTRAG_QUERY_LLM_MODEL": "qwen3.8:latest",
        "LIGHTRAG_EXTRACT_MAX_ASYNC_LLM": "1",
        "LIGHTRAG_QUERY_LLM_TIMEOUT": "900",
        "LIGHTRAG_QUERY_ENABLE_RERANK": "false",
        "LIGHTRAG_QUERY_TOP_K": "10",
        "LIGHTRAG_QUERY_CHUNK_TOP_K": "5",
        "LIGHTRAG_QUERY_MAX_TOTAL_TOKENS": "12000",
    })

    assert env["EXTRACT_LLM_MODEL"] == "mistral-small3.2:24b"
    assert env["KEYWORD_LLM_MODEL"] == "mistral-small3.2:24b"
    assert env["QUERY_LLM_MODEL"] == "qwen3.8:latest"
    assert env["EXTRACT_MAX_ASYNC_LLM"] == "1"
    assert env["QUERY_LLM_TIMEOUT"] == "900"
    assert env["KEYWORD_LLM_TIMEOUT"] == ""
    assert env["RERANK_BY_DEFAULT"] == "false"
    assert env["TOP_K"] == "10"
    assert env["CHUNK_TOP_K"] == "5"
    assert env["MAX_TOTAL_TOKENS"] == "12000"


def test_resolver_entrypoint_execs_lightrag_or_exits_naming_the_key(tmp_path: Path):
    """#1271: as the container entrypoint, the resolver execs LightRAG with the
    scoped keys, or exits 1 with the variable to set before LightRAG starts."""
    server = tmp_path / "lightrag" / "api" / "lightrag_server.py"
    server.parent.mkdir(parents=True)
    for package in (server.parent.parent, server.parent):
        (package / "__init__.py").write_text("", encoding="utf-8")
    server.write_text(
        "import os\nprint(os.environ.get('EXTRACT_LLM_BINDING_API_KEY', ''))\n",
        encoding="utf-8",
    )
    base = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(tmp_path), **_LITELLM_BASE}

    def run(extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RESOLVER)], env={**base, **extra},
            capture_output=True, text=True, check=False, timeout=60,
        )

    started = run({})
    stopped = run({"EXTRACT_LLM_BINDING": "ollama",
                   "EXTRACT_LLM_BINDING_HOST": "http://host.docker.internal:11434"})
    assert (
        started.returncode, started.stdout.strip(),
        stopped.returncode, stopped.stdout, "LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY" in stopped.stderr,
    ) == (0, _MASTER_KEY, 1, "", True)


def _effective_lightrag_environment(tmp_path: Path, overrides: dict[str, str]) -> dict[str, str]:
    """What LightRAG sees: the Compose render, then the entrypoint resolver."""
    rendered = _render_lightrag_environment(tmp_path, overrides)
    env = {key: "" if value is None else str(value) for key, value in rendered.items()}
    return {**env, **_resolver().resolve_role_keys(env)}


@_needs_docker
def test_litellm_routed_extract_needs_no_key_wiring(tmp_path: Path):
    """#796 AC5, kept by #1271: setting only the EXTRACT binding and host (here
    LiteLLM itself) gives EXTRACT the LiteLLM master key at start."""
    env = _effective_lightrag_environment(tmp_path, {
        **_LIGHTRAG_ON,
        "LITELLM_MASTER_KEY": _MASTER_KEY,
        "LIGHTRAG_EXTRACT_LLM_BINDING": "openai",
        "LIGHTRAG_EXTRACT_LLM_BINDING_HOST": "http://litellm:4000/v1",
    })

    assert (env["EXTRACT_LLM_BINDING"], env["EXTRACT_LLM_BINDING_API_KEY"]) == (
        "openai", _MASTER_KEY)


_NATIVE_OLLAMA_EXTRACT = {
    **_LIGHTRAG_ON,
    "LITELLM_MASTER_KEY": _MASTER_KEY,
    "LIGHTRAG_EXTRACT_LLM_MODEL": "mistral-small3.2:24b",
    "LIGHTRAG_EXTRACT_LLM_BINDING": "ollama",
    "LIGHTRAG_EXTRACT_LLM_BINDING_HOST": "http://host.docker.internal:11434",
}


@_needs_docker
def test_native_ollama_extract_renders_numeric_caps_and_its_own_key(tmp_path: Path):
    """#796 AC1/AC2 and #1271 AC1: with the documented native-Ollama settings
    and the caps left blank, Compose renders numeric caps and the explicit key
    reaches LightRAG unchanged."""
    env = _effective_lightrag_environment(tmp_path, {
        **_NATIVE_OLLAMA_EXTRACT,
        "LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY": "ollama",
        "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT": "",
        "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_CTX": "",
    })

    assert (
        env["EXTRACT_LLM_BINDING_API_KEY"],
        env["EXTRACT_OLLAMA_LLM_NUM_PREDICT"], env["EXTRACT_OLLAMA_LLM_NUM_CTX"],
    ) == ("ollama", "4096", "16384")


@_needs_docker
def test_native_ollama_extract_without_key_stops_instead_of_leaking(tmp_path: Path):
    """#1271 AC1/AC2: a native-Ollama EXTRACT with no key renders no master key
    and stops at start naming the variable to set."""
    rendered = _render_lightrag_environment(tmp_path, _NATIVE_OLLAMA_EXTRACT)
    env = {key: "" if value is None else str(value) for key, value in rendered.items()}
    resolver = _resolver()

    with pytest.raises(resolver.RoleKeyError, match="LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY"):
        resolver.resolve_role_keys(env)
    assert env["EXTRACT_LLM_BINDING_API_KEY"] == ""


def test_extract_generation_caps_are_documented():
    """#796 AC6 (and the upstream findings for AC3/AC4): the README, the
    consumer guide and the generated env reference name the caps."""
    readme = (REPO_ROOT / "services" / "lightrag" / "README.md").read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs" / "operations" / "reusing-atlas.md").read_text(encoding="utf-8")
    reference = (REPO_ROOT / "docs" / "reference" / "env-vars.md").read_text(encoding="utf-8")

    for text in (readme, guide, reference):
        assert "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT" in text
        assert "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_CTX" in text
    for fact in (
        "LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT=4096",
        "LIGHTRAG_EXTRACT_LLM_MODEL=",
        "LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY=ollama",
        "POST /documents/clear_cache",
        "blob/v1.5.4/lightrag/utils.py#L1274-L1277",
        "blob/v1.5.4/lightrag/operate.py#L3746-L3779",
        "not configurable in 1.5.4",
    ):
        assert fact in readme
    assert "LIGHTRAG_EXTRACT_LLM_MODEL" in guide
    # #1271 AC3: the key is documented as required, not as a leak workaround.
    assert (
        "any string such as `ollama` works" in guide,
        "resolve-role-keys.py" in readme,
        any("placeholder" in text for text in (readme, guide)),
        any("defaults to `${LITELLM_MASTER_KEY}`" in text for text in (readme, guide)),
    ) == (True, True, False, False)


# --- the smoke proves each role's own request (#1389) ------------------------

_FAKE_DOCKER = """#!/bin/sh
case "$*" in
  *" exec "*) echo "EXTRACT_LLM_MODEL=x" ;;
  *" logs "*) printf '%s\\n' "$FAKE_LITELLM_LOGS" ;;
esac
"""
_FAKE_CURL = """#!/bin/sh
echo "$*" >>"$CURL_LOG"
case "$*" in
  */documents/upload*) printf '{"status":"%s","message":"m"}' "${FAKE_UPLOAD_STATUS:-success}" ;;
  *) printf '{"response":"r"}' ;;
esac
"""


def _run_smoke(tmp_path: Path, logs: str, **env):
    bin_dir, tmp = tmp_path / "bin", tmp_path / "tmp"
    for path, body in ((bin_dir / "docker", _FAKE_DOCKER), (bin_dir / "curl", _FAKE_CURL)):
        path.parent.mkdir(exist_ok=True)
        path.write_text(body)
        path.chmod(0o755)
    tmp.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(SMOKE_SCRIPT)], text=True, capture_output=True, check=False, timeout=60,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "TMPDIR": str(tmp), "LIGHTRAG_API_KEY": "k",
             "LIGHTRAG_EXTRACT_LLM_MODEL": "gpt-4o", "LIGHTRAG_QUERY_LLM_MODEL": "qwen3:8b",
             "LIGHTRAG_SMOKE_WAIT_SECONDS": "0", "FAKE_LITELLM_LOGS": logs,
             "CURL_LOG": str(tmp_path / "curl.log"), **env},
    )


def test_lightrag_smoke_needs_each_exact_role_model_and_reruns_cleanly(tmp_path: Path) -> None:
    # LiteLLM logs the routed model name.
    both = ("litellm.acompletion(model=openai/gpt-4o) 200 OK\n"
            "litellm.acompletion(model=ollama_chat/qwen3:8b) 200 OK")
    for _ in range(2):
        passed = _run_smoke(tmp_path, both)
        assert passed.returncode == 0, passed.stderr
    calls = (tmp_path / "curl.log").read_text().splitlines()
    # A fresh name, text and query per run, so a rerun reaches both models again.
    assert len({line for line in calls if "upload" in line}) == 2
    assert len({line for line in calls if "/query" in line}) == 2
    assert list((tmp_path / "tmp").iterdir()) == []  # every temp file removed

    mini_only = _run_smoke(tmp_path, "model=openai/gpt-4o-mini\nmodel=ollama_chat/qwen3:8b")
    assert mini_only.returncode == 1 and "missing expected EXTRACT model" in mini_only.stderr
    same = _run_smoke(tmp_path, both, LIGHTRAG_QUERY_LLM_MODEL="gpt-4o")
    assert same.returncode == 2 and "set different models" in same.stderr
    refused = _run_smoke(tmp_path, both, FAKE_UPLOAD_STATUS="failure")
    assert refused.returncode == 1 and "not accepted for extraction" in refused.stderr
