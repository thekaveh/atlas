"""Offline launch regressions for source normalization and memory identity."""
from __future__ import annotations

import asyncio
from pathlib import Path
import socket
import subprocess
import sys
from types import MethodType, SimpleNamespace

import pytest

from core.config_parser import ConfigParser
from core.linear_startup import LinearStartupOptions, run_linear_startup
from services.source_validator import SourceValidator
from start import AtlasStarter, _launch_model_env, _routed_model_names
from utils import llm_catalog
from utils.atomic_write import render_env_assignment
from utils.cloud_providers import CLOUD_PROVIDERS
from utils.llm_catalog import CatalogEntry
from utils.source_override_manager import SourceOverrideManager

ROOT = Path(__file__).resolve().parents[2]
EMBEDDING_KEYS = ("LITELLM_EMBEDDING_MODEL", "LANGMEM_EMBEDDING_MODEL", "LANGMEM_EMBEDDING_DIM")
CHAT_KEYS = ("LITELLM_DEFAULT_MODEL", "LITELLM_VISION_MODEL")


def _forbidden(*args, **kwargs):
    raise AssertionError("operational/provider action reached by offline launch test")


def _offline_catalogs(monkeypatch):
    local = [
        CatalogEntry("ollama", "local-chat", content=10, vision=10, default_active=True),
        CatalogEntry("ollama", "local-embed", embeddings=10, dim=768, default_active=True),
    ]
    cloud = {
        p.key: [CatalogEntry(p.key, f"{p.key}/chat", content=9, vision=9, default_active=True)]
        for p in CLOUD_PROVIDERS
    }
    cloud["openai"].append(CatalogEntry(
        "openai", "text-embedding-3-large", embeddings=9, dim=3072, default_active=True,
    ))
    entries = {"ollama": local, **cloud}
    monkeypatch.setattr(llm_catalog, "ollama_entries", lambda: local)
    monkeypatch.setattr(llm_catalog, "cloud_entries", lambda provider: cloud[provider])
    monkeypatch.setattr(llm_catalog, "all_catalog_entries", lambda: sum(entries.values(), []))
    monkeypatch.setattr(llm_catalog, "default_active_names", lambda provider: [
        entry.name for entry in entries[provider] if entry.default_active
    ])
    for name in ("connect", "connect_ex", "bind"):
        monkeypatch.setattr(socket.socket, name, _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    _offline_catalogs(monkeypatch)


def _initial_env(provider):
    env = {
        "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
        "VLLM_METAL_SOURCE": "disabled",
        "OLLAMA_USER_MODELS": "local-chat,local-embed",
        "LITELLM_DEFAULT_MODEL": f"{provider.key}/chat",
        "LITELLM_VISION_MODEL": f"{provider.key}/chat",
        "LITELLM_EMBEDDING_MODEL": "custom/saved",
        "LANGMEM_EMBEDDING_MODEL": "custom/saved",
        "LANGMEM_EMBEDDING_DIM": "384",
    }
    for p in CLOUD_PROVIDERS:
        env.update({p.source_var: "enabled" if p == provider else "disabled",
                    p.api_key_var: "", p.user_models_var: f"{p.key}/chat"})
    return env


def _write_env(path, env):
    path.write_text("".join(key + "=" + render_env_assignment(key, value) + "\n" for key, value in env.items()))


def _record(starter, name, result=True):
    def call(*args, **kwargs):
        starter.calls.append(name)
        return result
    return call


def _real_step(starter, name):
    def call():
        starter.calls.append(name)
        return getattr(AtlasStarter, name)(starter)
    return call


def _starter(path):
    # Bypass constructor discovery/managers; keep real parser, writer and validator.
    starter = AtlasStarter.__new__(AtlasStarter)
    starter.config_parser = ConfigParser(str(ROOT))
    starter.config_parser.env_file_path = path
    starter.source_validator = SourceValidator(starter.config_parser)
    starter.source_override_manager = SourceOverrideManager(starter.config_parser)
    starter.banner = SimpleNamespace(console=SimpleNamespace(print=lambda *a, **kw: None))
    starter.calls = []
    starter.logs = []
    for name in (
        "show_banner", "prepare_environment", "backfill_missing_env_vars", "run_port_migration",
        "apply_source_overrides", "apply_profile_overrides", "preflight_managed_host_processes",
        "unset_port_environment_variables",
    ):
        setattr(starter, name, _record(starter, name))
    for name in ("validate_source_configurations", "reconcile_default_models"):
        setattr(starter, name, _real_step(starter, name))
    starter.handle_port_configuration = _record(starter, "port-boundary", False)
    for name in (
        "validate_supabase_keys", "generate_encryption_keys", "generate_service_configuration",
        "check_service_dependencies", "generate_kong_configuration", "generate_litellm_configuration",
        "generate_comfyui_manifest", "handle_hosts_configuration", "validate_localhost_services",
        "start_managed_host_processes", "start_docker_services", "rollback_managed_host_processes",
    ):
        setattr(starter, name, _forbidden)
    starter.docker_manager = SimpleNamespace(execute_compose_command=_forbidden,
                                            set_command_echo_callback=lambda *a: None)
    starter.hosts_manager = SimpleNamespace(set_logger=lambda *a: None)
    return starter


class _InlineExecutor:
    def __init__(self, *args):
        pass

    def bridge_stream(self):
        return lambda: None

    def cancel_requested(self):
        return False

    async def run_in_thread(self, fn):
        return fn()


def _options(selections):
    return LinearStartupOptions(
        cold=False, base_port=None, project_name=None, source_args={}, profile="default",
        explicit_prometheus=None, explicit_grafana=None, cloud_api_keys={},
        user_model_selections=selections, no_port_migrate=True, setup_hosts=False,
        skip_hosts=True, track=None, detach=True, json_output=False, no_splash=True,
    )


def _run(starter, flow, monkeypatch, selections=None):
    if flow == "linear":
        return run_linear_startup(starter, _options(selections or {}))
    import ui.textual.screens.wizard_screen as ws

    monkeypatch.setattr(ws, "_ThreadedComposeExecutor", _InlineExecutor)

    def log(message, *args, **kwargs):
        starter.logs.append(str(message))

    screen = SimpleNamespace(
        _starter=starter, _stack_options={"default_model_selections": selections or {}},
        _source_args={}, _safe_log=log, _write_status=log, _cold_cleanup_steps=lambda *a: [],
        _mark_launch_failed=lambda: starter.logs.append("failed"), _close_launch_log_tee=lambda: None,
    )
    screen._configure_launch_ports = MethodType(ws.WizardScreen._configure_launch_ports, screen)
    asyncio.run(ws.WizardScreen._run_pipeline_and_stream(screen))
    return 1  # Every successful configuration probe deliberately stops at ports.


def _configured(tmp_path, provider, overrides=None):
    env = {**_initial_env(provider), **(overrides or {})}
    path = tmp_path / ".env"
    _write_env(path, env)
    return _starter(path), env


def _assert_fallback(starter):
    env = starter.config_parser.parse_env_file()
    routed = _routed_model_names(_launch_model_env(env))
    assert all(env[key] == "ollama/local-chat" and env[key] in routed for key in CHAT_KEYS)
    assert "port-boundary" in starter.calls
    assert not any("crashed" in line for line in starter.logs), starter.logs
    return env


@pytest.mark.parametrize("credential", [(p, key) for p in CLOUD_PROVIDERS for key in ("", "   ")],
                         ids=[p.key + suffix for p in CLOUD_PROVIDERS for suffix in ("-empty", "-spaces")])
@pytest.mark.parametrize("flow", ["linear", "textual"])
def test_one_pass_normalizes_keyless_provider_and_routes_defaults(tmp_path, monkeypatch, credential, flow):
    provider, key = credential
    starter, initial = _configured(tmp_path, provider, {provider.api_key_var: key})
    assert _run(starter, flow, monkeypatch) == 1
    env = _assert_fallback(starter)
    assert env[provider.source_var] == "disabled"
    assert {k: env[k] for k in EMBEDDING_KEYS} == {k: initial[k] for k in EMBEDDING_KEYS}


@pytest.mark.parametrize("provider", CLOUD_PROVIDERS, ids=lambda p: p.key)
@pytest.mark.parametrize("flow", ["linear", "textual"])
def test_keyed_provider_keeps_operator_chat_and_vision(tmp_path, monkeypatch, provider, flow):
    starter, initial = _configured(tmp_path, provider, {provider.api_key_var: "test-key"})
    _run(starter, flow, monkeypatch)
    env = starter.config_parser.parse_env_file()
    assert env == initial
    assert all(env[k] in _routed_model_names(_launch_model_env(env)) for k in CHAT_KEYS)
    assert "port-boundary" in starter.calls


@pytest.mark.parametrize("flow", ["linear", "textual"])
def test_no_upstream_halts_with_actionable_error(tmp_path, monkeypatch, flow):
    starter, initial = _configured(tmp_path, CLOUD_PROVIDERS[0], {"LLM_PROVIDER_SOURCE": "none"})
    _run(starter, flow, monkeypatch)
    env = starter.config_parser.parse_env_file()
    assert all(env[p.source_var] == "disabled" for p in CLOUD_PROVIDERS)
    assert all(env[k] == initial[k] for k in (*CHAT_KEYS, *EMBEDDING_KEYS))
    assert "reconcile_default_models" not in starter.calls
    assert "preflight_managed_host_processes" not in starter.calls
    assert any("no upstream configured" in e and "Set LLM_PROVIDER_SOURCE" in e
               for e in starter.source_validator.get_validation_errors())


@pytest.mark.parametrize("flow", ["linear", "textual"])
@pytest.mark.parametrize("failure", ["false", "raise"])
def test_source_persistence_failure_stops_before_defaults(tmp_path, monkeypatch, flow, failure):
    starter, initial = _configured(tmp_path, CLOUD_PROVIDERS[0])

    def fail(*args):
        if failure == "raise":
            raise OSError("synthetic source write failure")
        return False

    monkeypatch.setattr(SourceOverrideManager, "update_env_file", fail)
    monkeypatch.setattr(starter.source_validator, "validate_all_sources", _forbidden)
    _run(starter, flow, monkeypatch)
    assert starter.config_parser.parse_env_file() == initial
    assert "reconcile_default_models" not in starter.calls
    assert "preflight_managed_host_processes" not in starter.calls
    assert any("Could not persist cloud-provider auto-disable" in e
               for e in starter.source_validator.get_validation_errors())


@pytest.mark.parametrize("flow", ["linear", "textual"])
@pytest.mark.parametrize("failure", ["false", "raise"])
def test_default_persistence_failure_keeps_normalized_sources_and_halts(tmp_path, monkeypatch, flow, failure):
    starter, initial = _configured(tmp_path, CLOUD_PROVIDERS[0])
    writer = SourceOverrideManager.update_env_file

    def write(manager, updates):
        if any(k in updates for k in CHAT_KEYS):
            if failure == "raise":
                raise OSError("synthetic default write failure")
            return False
        return writer(manager, updates)

    monkeypatch.setattr(SourceOverrideManager, "update_env_file", write)
    if flow == "linear" and failure == "raise":
        with pytest.raises(OSError, match="synthetic default write failure"):
            _run(starter, flow, monkeypatch)
    else:
        _run(starter, flow, monkeypatch)
    env = starter.config_parser.parse_env_file()
    assert env[CLOUD_PROVIDERS[0].source_var] == "disabled"
    assert all(env[k] == initial[k] for k in (*CHAT_KEYS, *EMBEDDING_KEYS))
    assert "preflight_managed_host_processes" not in starter.calls
    monkeypatch.setattr(SourceOverrideManager, "update_env_file", writer)
    retry = _starter(starter.config_parser.env_file_path)
    _run(retry, flow, monkeypatch)
    _assert_fallback(retry)


@pytest.mark.parametrize("flow", ["linear", "textual"])
@pytest.mark.parametrize("embedding", ["custom/saved", "text-embedding-3-large"])
def test_repeat_launch_preserves_persisted_embedding_contract(tmp_path, monkeypatch, flow, embedding):
    starter, initial = _configured(tmp_path, CLOUD_PROVIDERS[0], {
        "LITELLM_EMBEDDING_MODEL": embedding, "LANGMEM_EMBEDDING_MODEL": embedding,
        "LANGMEM_EMBEDDING_DIM": "384" if embedding.startswith("custom/") else "3072",
    })
    for _ in range(2):
        _run(starter, flow, monkeypatch)
        env = _assert_fallback(starter)
        assert all(env[k] == initial[k] for k in EMBEDDING_KEYS)
        starter = _starter(starter.config_parser.env_file_path)


def _fresh_process_repeat(path, flow):
    with pytest.MonkeyPatch.context() as monkeypatch:
        _offline_catalogs(monkeypatch)
        starter = _starter(Path(path))
        _run(starter, flow, monkeypatch)
        _assert_fallback(starter)


@pytest.mark.parametrize("flow", ["linear", "textual"])
def test_fresh_python_process_preserves_disabled_cloud_contract(tmp_path, monkeypatch, flow):
    starter, initial = _configured(tmp_path, CLOUD_PROVIDERS[0], {
        "LITELLM_EMBEDDING_MODEL": "text-embedding-3-large",
        "LANGMEM_EMBEDDING_MODEL": "text-embedding-3-large", "LANGMEM_EMBEDDING_DIM": "3072",
    })
    _run(starter, flow, monkeypatch)
    path = starter.config_parser.env_file_path
    assert starter.config_parser.parse_env_file()["CLOUD_OPENAI_SOURCE"] == "disabled"
    code = ("import runpy,sys; ns=runpy.run_path(sys.argv[1]); "
            "ns['_fresh_process_repeat'](sys.argv[2], sys.argv[3])")
    result = subprocess.run([sys.executable, "-B", "-c", code, __file__, str(path), flow],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    env = starter.config_parser.parse_env_file()
    assert all(env[k] == initial[k] for k in EMBEDDING_KEYS)


@pytest.mark.parametrize("protected_key", EMBEDDING_KEYS[:2])
@pytest.mark.parametrize("dimension", ["not-a-dimension", "384"])
def test_protected_cloud_identity_preserves_both_keys_and_catalog_dimension(tmp_path, protected_key, dimension):
    starter, initial = _configured(tmp_path, CLOUD_PROVIDERS[0], {
        "CLOUD_OPENAI_SOURCE": "disabled", protected_key: "text-embedding-3-large",
        "LANGMEM_EMBEDDING_DIM": dimension, **{k: "ollama/local-chat" for k in CHAT_KEYS},
    })
    assert starter.reconcile_default_models()
    assert starter.reconcile_default_models()
    assert starter.config_parser.parse_env_file() == initial


@pytest.mark.parametrize("flow", ["linear", "textual"])
def test_explicit_operator_embedding_override_applies_before_repair(tmp_path, monkeypatch, flow):
    starter, _ = _configured(tmp_path, CLOUD_PROVIDERS[0], {
        "LITELLM_EMBEDDING_MODEL": "text-embedding-3-large",
        "LANGMEM_EMBEDDING_MODEL": "text-embedding-3-large", "LANGMEM_EMBEDDING_DIM": "3072",
    })
    selection = {"LITELLM_EMBEDDING_MODEL": "custom/intentional",
                 "LANGMEM_EMBEDDING_MODEL": "custom/intentional", "LANGMEM_EMBEDDING_DIM": "1536"}
    _run(starter, flow, monkeypatch, selection)
    env = _assert_fallback(starter)
    assert all(env[k] == v for k, v in selection.items())


@pytest.mark.parametrize("mode", ["keyed-disabled", "keyed-deselected", "inactive-ollama", "catalog"])
def test_existing_embedding_migrations_and_catalog_repair_remain(mode, tmp_path):
    overrides = {**{k: "ollama/local-chat" for k in CHAT_KEYS},
                 "CLOUD_OPENAI_SOURCE": "disabled", "OPENAI_API_KEY": "test-key",
                 "LITELLM_EMBEDDING_MODEL": "text-embedding-3-large",
                 "LANGMEM_EMBEDDING_MODEL": "text-embedding-3-large", "LANGMEM_EMBEDDING_DIM": "3072"}
    if mode == "keyed-deselected":
        overrides["CLOUD_OPENAI_SOURCE"] = "enabled"
    if mode in ("inactive-ollama", "catalog"):
        overrides.update({"LITELLM_EMBEDDING_MODEL": "ollama/local-embed",
                          "LANGMEM_EMBEDDING_MODEL": "ollama/local-embed", "LANGMEM_EMBEDDING_DIM": "384"})
    if mode == "inactive-ollama":
        overrides.update({"LLM_PROVIDER_SOURCE": "none", "CLOUD_OPENAI_SOURCE": "enabled",
                          "OPENAI_USER_MODELS": "openai/chat,text-embedding-3-large"})
    starter, _ = _configured(tmp_path, CLOUD_PROVIDERS[0], overrides)
    assert starter.reconcile_default_models()
    env = starter.config_parser.parse_env_file()
    expected = "text-embedding-3-large" if mode == "inactive-ollama" else "ollama/local-embed"
    assert env["LITELLM_EMBEDDING_MODEL"] == env["LANGMEM_EMBEDDING_MODEL"] == expected
    assert env["LANGMEM_EMBEDDING_DIM"] == ("3072" if mode == "inactive-ollama" else "768")


def test_routed_openai_alias_uses_catalog_dimension_without_openai_key(tmp_path):
    alias = "openai/consumer-embedding"
    llm_catalog.cloud_entries("openai").append(CatalogEntry("openai", alias, embeddings=8, dim=1024))
    starter, _ = _configured(tmp_path, CLOUD_PROVIDERS[0], {
        **{k: "ollama/local-chat" for k in CHAT_KEYS},
        "CLOUD_OPENAI_SOURCE": "disabled", "VLLM_METAL_SOURCE": "managed-localhost",
        "VLLM_METAL_MODEL": alias, "LITELLM_EMBEDDING_MODEL": alias,
        "LANGMEM_EMBEDDING_MODEL": alias, "LANGMEM_EMBEDDING_DIM": "384",
    })
    assert starter.reconcile_default_models()
    env = starter.config_parser.parse_env_file()
    assert env["LITELLM_EMBEDDING_MODEL"] == env["LANGMEM_EMBEDDING_MODEL"] == alias
    assert env["LANGMEM_EMBEDDING_DIM"] == "1024"


@pytest.mark.parametrize("dimension", ["not-a-dimension", "384"])
def test_disabled_keyless_cloud_pair_blocks_no_change_catalog_correction(tmp_path, dimension):
    starter, initial = _configured(tmp_path, CLOUD_PROVIDERS[0], {
        **{k: "ollama/local-chat" for k in CHAT_KEYS}, "CLOUD_OPENAI_SOURCE": "disabled",
        "LITELLM_EMBEDDING_MODEL": "text-embedding-3-large",
        "LANGMEM_EMBEDDING_MODEL": "text-embedding-3-large", "LANGMEM_EMBEDDING_DIM": dimension,
    })
    assert starter.reconcile_default_models()
    assert starter.reconcile_default_models()
    assert starter.config_parser.parse_env_file() == initial
