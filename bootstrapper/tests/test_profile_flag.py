"""Tests for --profile flag wiring and the prod/default HOST_BIND_IP toggle."""

from __future__ import annotations

from pathlib import Path

import start


def test_cli_declares_profile():
    names = [p.name for p in start.main.params]
    assert "profile" in names


def test_profile_choices():
    opt = next(p for p in start.main.params if p.name == "profile")
    assert set(opt.type.choices) == {"default", "prod"}


def test_profile_cli_default_is_unset_for_interactive_picker():
    """Bare ./start.sh must not preselect default and skip the profile step."""
    opt = next(p for p in start.main.params if p.name == "profile")
    assert opt.default is None


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
    (tmp_path / ".env.example").write_text("HOST_BIND_IP=\n", encoding="utf-8")
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

def test_prod_profile_sets_host_bind_ip(tmp_path):
    """--profile prod must write ALL five prod overrides to .env, not just
    HOST_BIND_IP — localhost binding, observability default-ON, log rotation."""
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
# apply_profile_overrides — default path (the footgun fix)
# ---------------------------------------------------------------------------

def test_default_profile_clears_prod_bind_ip(tmp_path, capsys):
    """Switching back to default profile must clear the prod HOST_BIND_IP."""
    # Simulate a .env left over from a previous --profile prod run.
    env_body = "HOST_BIND_IP=127.0.0.1:\n"
    starter = _make_starter(tmp_path, env_body)
    ok = starter.apply_profile_overrides("default")
    assert ok
    out = (tmp_path / ".env").read_text(encoding="utf-8")
    # Value must be cleared (empty) — not the prod literal.
    assert "HOST_BIND_IP=127.0.0.1:" not in out
    assert "HOST_BIND_IP=" in out
    # A notice must have been printed.
    captured = capsys.readouterr()
    assert "HOST_BIND_IP" in captured.out
    assert "cleared" in captured.out


def test_default_profile_leaves_user_bind_ip_untouched(tmp_path, capsys):
    """A user-set HOST_BIND_IP (not the prod literal) must survive a default run."""
    user_value = "10.0.0.5:"
    env_body = f"HOST_BIND_IP={user_value}\n"
    starter = _make_starter(tmp_path, env_body)
    ok = starter.apply_profile_overrides("default")
    assert ok
    out = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"HOST_BIND_IP={user_value}" in out
    # No notice should be printed for user-set values.
    captured = capsys.readouterr()
    assert "cleared" not in captured.out


def test_default_profile_noop_when_bind_ip_already_empty(tmp_path):
    """Default profile is a no-op when HOST_BIND_IP is already empty."""
    env_body = "HOST_BIND_IP=\n"
    starter = _make_starter(tmp_path, env_body)
    before = (tmp_path / ".env").read_text(encoding="utf-8")
    ok = starter.apply_profile_overrides("default")
    assert ok
    after = (tmp_path / ".env").read_text(encoding="utf-8")
    assert before == after
