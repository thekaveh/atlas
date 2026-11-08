"""Leaving a launch before it starts leaves .env as it found it (#1044).

Every entry path writes .env before it validates or confirms anything.
``core.env_snapshot.EnvSnapshot`` captures the file before the first write.
A declined --no-tui summary, an interrupt before launch, the wizard's "No"
and a Ctrl+C before launch put it back. A failed stage keeps its writes:
#1820 persists source normalization across a halted launch on purpose.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import launch_plan, linear_startup
from core.env_snapshot import CHANGE_LIST_LIMIT, EnvSnapshot

PRIOR = b"# operator notes\nBASE_PORT=63000\nGRAFANA_SOURCE=container\nREDIS_PASSWORD=old-secret-1\n"


class _Starter:
    """Every step writes .env the way the real ones do, then succeeds,
    except ``fail_at``, which returns False."""

    def __init__(self, env: Path, *, fail_at=None, confirm=True):
        self.env = env
        self.fail_at, self.confirm = fail_at, confirm
        self.calls: list[str] = []
        self.skipped_builds: list[str] = []
        self.stopped_previous_instance = False
        self.config_parser = SimpleNamespace(root_dir=str(env.parent), env_file_path=env)
        self.source_validator = SimpleNamespace(validation_errors=[])
        self.key_generator = SimpleNamespace(assert_no_placeholders_remaining=lambda: None)
        self.banner = SimpleNamespace(console=SimpleNamespace(print=lambda *a, **k: None))

    def __getattr__(self, name):
        def step(*_args, **_kwargs):
            self.calls.append(name)
            with self.env.open("a", encoding="utf-8") as handle:
                handle.write(f"WRITTEN_BY_{name.upper()}=1\n")
            return name != self.fail_at

        return step

    def show_banner(self):
        pass

    def show_pre_launch_summary(self, **_kwargs):
        self.calls.append("show_pre_launch_summary")
        return self.confirm


def _options(**overrides):
    values = dict(
        cold=False, base_port=63000, project_name="atlas", source_args={},
        profile="default", cloud_api_keys={}, user_model_selections={},
        no_port_migrate=False, setup_hosts=False, skip_hosts=True, track=None,
        detach=False, json_output=False, no_splash=True,
    )
    values.update(overrides)
    return linear_startup.LinearStartupOptions(**values)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)
    path = tmp_path / ".env"
    path.write_bytes(PRIOR)
    path.chmod(0o600)
    return path


def test_a_failed_stage_keeps_what_it_wrote(env):
    """The #1820 contract: a halted launch keeps its source normalization."""
    starter = _Starter(env, fail_at="validate_source_configurations")
    assert linear_startup.run_linear_startup(starter, _options()) == 1
    assert b"WRITTEN_BY_APPLY_SOURCE_OVERRIDES=1" in env.read_bytes()


def test_declining_a_fresh_install_leaves_no_env(tmp_path, monkeypatch):
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)
    env = tmp_path / ".env"
    starter = _Starter(env, confirm=False)
    assert linear_startup.run_linear_startup(starter, _options()) == 0
    assert "show_pre_launch_summary" in starter.calls
    assert not env.exists()
    assert "start_docker_services" not in starter.calls


def test_declining_an_existing_install_leaves_env_byte_identical(env):
    starter = _Starter(env, confirm=False)
    assert linear_startup.run_linear_startup(starter, _options()) == 0
    assert env.read_bytes() == PRIOR


_WRITE_POINTS = ["prepare_environment", "backfill_missing_env_vars", "apply_source_overrides",
                 "apply_profile_overrides", "generate_encryption_keys",
                 "handle_port_configuration", "backfill_missing_env_vars"]


@pytest.mark.parametrize("method", sorted(set(_WRITE_POINTS)))
def test_an_interrupt_at_any_write_point_restores_env(env, method):
    starter = _Starter(env)
    real = starter.__getattr__(method)

    def interrupted(*args, **kwargs):
        real(*args, **kwargs)
        raise KeyboardInterrupt

    starter.__dict__[method] = interrupted
    with pytest.raises(KeyboardInterrupt):
        linear_startup.run_linear_startup(starter, _options())
    assert env.read_bytes() == PRIOR
    assert "start_docker_services" not in starter.calls


def test_a_confirmed_launch_keeps_the_new_env(env):
    starter = _Starter(env)
    assert linear_startup.run_linear_startup(starter, _options(detach=True)) == 0
    assert env.read_bytes() != PRIOR
    assert "start_docker_services" in starter.calls


# ─── The reviewable change list ───────────────────────────────────────


def test_the_change_list_names_keys_and_hides_secret_values(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(PRIOR)
    snapshot = EnvSnapshot.capture(env)
    env.write_text(
        "BASE_PORT=64000\nGRAFANA_SOURCE=disabled\nREDIS_PASSWORD=new-secret-2\n"
        "LITELLM_MASTER_KEY=sk-new-master-key-123\nN8N_API_TOKEN=tok-abc-123456\n"
        "SUPABASE_JWT_SECRET=jwt-secret-value\n"
        "LIGHTRAG_REDIS_URI=redis://:new-secret-2@redis:6379/2\n"
    )
    text = "\n".join(snapshot.changes())
    assert "~ BASE_PORT: 63000 -> 64000" in text
    assert "~ GRAFANA_SOURCE: container -> disabled" in text
    for key in ("REDIS_PASSWORD", "LITELLM_MASTER_KEY", "N8N_API_TOKEN",
                "SUPABASE_JWT_SECRET", "LIGHTRAG_REDIS_URI"):
        assert key in text
    for secret in ("old-secret-1", "new-secret-2", "sk-new-master-key-123",
                   "tok-abc-123456", "jwt-secret-value"):
        assert secret not in text


def test_the_summary_counts_a_long_change_list(tmp_path):
    env = tmp_path / ".env"
    assert EnvSnapshot.capture(env).summary_lines() == [".env will be created (0 keys)."]
    env.write_text("A=1\n")
    snapshot = EnvSnapshot.capture(env)
    assert snapshot.summary_lines() == [".env is unchanged."]
    env.write_text("".join(f"K{i}=1\n" for i in range(CHANGE_LIST_LIMIT + 1)))
    assert "not listed" in snapshot.summary_lines()[0]


def test_restore_does_not_rewrite_an_unchanged_env(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(PRIOR)
    before = os.stat(env).st_mtime_ns
    assert EnvSnapshot.capture(env).restore() is False
    assert os.stat(env).st_mtime_ns == before


# ─── The Textual entry paths ──────────────────────────────────────────


def _tui_starter(env: Path, **state):
    snapshot = EnvSnapshot.capture(env)
    env.write_text("WRITTEN_BY_PREPARE=1\n")
    return SimpleNamespace(launch_env_snapshot=snapshot, stopped_previous_instance=False,
                           tui_cold_cleanup_ran=False, **state)


def test_the_wizards_no_restores_env(env, capsys):
    from start import _report_tui_exit

    starter = _tui_starter(env, wizard_declined=True, tui_launch_started=False)
    assert _report_tui_exit(0, starter) == 0
    assert env.read_bytes() == PRIOR
    assert ".env restored to its state before this run" in capsys.readouterr().out


def test_ctrl_c_before_launch_restores_env(env, capsys):
    from start import _report_tui_exit

    starter = _tui_starter(env, tui_launch_started=False)
    assert _report_tui_exit(130, starter) == 130
    assert env.read_bytes() == PRIOR


def test_ctrl_c_after_launch_keeps_the_written_env(env, capsys):
    from start import _report_tui_exit

    starter = _tui_starter(env, tui_launch_started=True)
    assert _report_tui_exit(130, starter) == 130
    assert env.read_bytes() == b"WRITTEN_BY_PREPARE=1\n"
    assert "configuration written so far kept" in capsys.readouterr().out


def test_the_no_option_says_what_it_does():
    source = (Path(__file__).resolve().parents[1] / "ui" / "textual" / "integration.py").read_text()
    assert "nothing written or launched" not in source
    assert "discards wizard selections; restores .env; launches nothing" in source
