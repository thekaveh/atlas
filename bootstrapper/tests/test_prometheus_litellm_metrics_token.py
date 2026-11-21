"""#1486: LiteLLM requires a key on /metrics, and Prometheus presents one.

The token file is the credential Prometheus reads through a read-only secret.
Its contents and permissions are the contract between start.py and Compose.
"""

from __future__ import annotations

import stat

import pytest


def _starter(tmp_path, env):
    import start as start_module

    starter = start_module.AtlasStarter()
    starter.root_dir = tmp_path

    class _Parser:
        def parse_env_file(self):
            return dict(env)

    starter.config_parser = _Parser()
    return starter


def test_metrics_token_holds_the_master_key_readable_by_the_container(tmp_path):
    starter = _starter(tmp_path, {"LITELLM_MASTER_KEY": "sk-test-master"})

    starter._write_prometheus_litellm_token()

    token = tmp_path / "volumes/prometheus/litellm-metrics-token"
    assert token.read_text(encoding="utf-8") == "sk-test-master"
    # The Prometheus image runs as nobody, so the group and other read bits must be set.
    assert stat.S_IMODE(token.stat().st_mode) & 0o044 == 0o044
    assert not (tmp_path / "volumes/prometheus/litellm-metrics-token.tmp").exists()


def test_metrics_token_without_a_master_key_fails_closed(tmp_path):
    starter = _starter(tmp_path, {})

    with pytest.raises(RuntimeError, match="LITELLM_MASTER_KEY"):
        starter._write_prometheus_litellm_token()
    assert not (tmp_path / "volumes/prometheus/litellm-metrics-token").exists()


def test_litellm_settings_require_a_key_on_the_metrics_endpoint():
    from utils.litellm_settings import base_settings

    settings = base_settings({})

    assert settings["litellm_settings"]["require_auth_for_metrics_endpoint"] is True
