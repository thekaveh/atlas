"""Tests for --profile flag wiring and stable loopback HOST_BIND_IP defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

import start


def test_cli_declares_profile():
    names = [p.name for p in start.main.params]
    assert "profile" in names


def test_profile_choices():
    opt = next(p for p in start.main.params if p.name == "profile")
    # `dev` is the #755 alias for `default` (services/profiles.py).
    assert set(opt.type.choices) == {"default", "dev", "prod"}


def test_profile_cli_default_is_unset_for_interactive_picker():
    """Bare ./start.sh must not preselect default and skip the profile step."""
    ctx = start.main.make_context("start", [], resilient_parsing=True)
    assert ctx.params["profile"] is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_starter(tmp_path: Path, env_body: str) -> "start.AtlasStarter":
    """Build an AtlasStarter pointing at a synthetic tmp_path repo.

    Mirrors the pattern in test_backfill_blank_values.py: construct the
    starter, then redirect its config_parser paths to tmp_path so tests
    never touch the real .env at repo root. .env.example just needs
    HOST_BIND_IP present for update_env_file's regex to find it.
    """
    (tmp_path / ".env").write_text(env_body, encoding="utf-8")
    (tmp_path / ".env.example").write_text(
        "HOST_BIND_IP=127.0.0.1:\n", encoding="utf-8"
    )
    starter = start.AtlasStarter()
    starter.config_parser.root_dir = tmp_path
    starter.config_parser.env_file_path = tmp_path / ".env"
    starter.config_parser.env_example_path = tmp_path / ".env.example"
    # Redirect SourceOverrideManager's config_parser reference so
    # update_env_file writes to the same tmp .env.
    starter.source_override_manager.config_parser = starter.config_parser
    return starter


# ---------------------------------------------------------------------------
# apply_profile_overrides — prod path
# ---------------------------------------------------------------------------

def test_prod_profile_sets_host_bind_ip_for_blank_env(tmp_path):
    """--profile prod must write ALL five prod overrides to .env, not just
    HOST_BIND_IP — the safe loopback default, observability default-ON, log rotation."""
    env_body = "HOST_BIND_IP=\nLOG_MAX_SIZE=\nLOG_MAX_FILE=\nPROMETHEUS_SOURCE=disabled\nGRAFANA_SOURCE=disabled\n"
    starter = _make_starter(tmp_path, env_body)
    ok = starter.apply_profile_overrides("prod")
    assert ok
    out = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "HOST_BIND_IP=127.0.0.1:" in out
    assert "PROMETHEUS_SOURCE=container" in out
    assert "GRAFANA_SOURCE=container" in out
    assert "LOG_MAX_SIZE=10m" in out
    assert "LOG_MAX_FILE=3" in out


def test_prod_profile_preserves_operator_log_max(tmp_path):
    """A hand-set LOG_MAX_SIZE/LOG_MAX_FILE must SURVIVE --profile prod (only
    unset/empty values get the prod default). Locks the Pass-2 preserve
    contract so a future refactor can't silently re-introduce the clobber.
    """
    env_body = (
        "HOST_BIND_IP=\nLOG_MAX_SIZE=50m\nLOG_MAX_FILE=7\n"
        "PROMETHEUS_SOURCE=disabled\nGRAFANA_SOURCE=disabled\n"
    )
    starter = _make_starter(tmp_path, env_body)
    ok = starter.apply_profile_overrides("prod")
    assert ok
    out = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LOG_MAX_SIZE=50m" in out
    assert "LOG_MAX_FILE=7" in out
    assert "LOG_MAX_SIZE=10m" not in out
    assert "LOG_MAX_FILE=3" not in out
    # The defining prod property still applies.
    assert "HOST_BIND_IP=127.0.0.1:" in out


def test_prod_profile_respects_explicit_observability_source(tmp_path):
    """When --prometheus-source was passed explicitly, prod must NOT override
    it to container (the explicit flag wins; default-ON is skipped). Grafana,
    with no explicit flag, is still defaulted ON."""
    env_body = "HOST_BIND_IP=\nLOG_MAX_SIZE=\nLOG_MAX_FILE=\nPROMETHEUS_SOURCE=disabled\nGRAFANA_SOURCE=disabled\n"
    starter = _make_starter(tmp_path, env_body)
    ok = starter.apply_profile_overrides("prod", explicit_prometheus="disabled")
    assert ok
    out = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "PROMETHEUS_SOURCE=disabled" in out
    assert "PROMETHEUS_SOURCE=container" not in out
    assert "GRAFANA_SOURCE=container" in out


# ---------------------------------------------------------------------------
# apply_profile_overrides — default/dev loopback path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile", ("default", "dev"))
def test_default_profiles_keep_loopback_after_legacy_blank_backfill(tmp_path, profile):
    """Normal default/dev launches retain the loopback value backfill repaired."""
    env_body = "HOST_BIND_IP=\n"
    starter = _make_starter(tmp_path, env_body)
    assert starter.backfill_missing_env_vars()
    assert starter.apply_profile_overrides(profile)
    out = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "HOST_BIND_IP=127.0.0.1:" in out


@pytest.mark.parametrize("profile", ("default", "dev", "prod"))
@pytest.mark.parametrize("user_value", ("0.0.0.0:", "10.0.0.5:"))
def test_profiles_preserve_explicit_operator_bind(tmp_path, profile, user_value):
    """Profile defaults must not overwrite deliberate remote/custom binds."""
    env_body = f"HOST_BIND_IP={user_value}\n"
    starter = _make_starter(tmp_path, env_body)
    assert starter.apply_profile_overrides(profile)
    out = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"HOST_BIND_IP={user_value}" in out
