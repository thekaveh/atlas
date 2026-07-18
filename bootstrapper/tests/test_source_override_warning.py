"""CLI source-flag override warning (#645).

A consumer wrapper that passes a defaulted `--<svc>-source` flag used to
silently revert an operator's hand-configured `.env` source on every start,
with no error anywhere (the Tableau `managed-localhost-mps → container-cpu`
incident). `SourceOverrideManager.apply_overrides` now prints an old→new
warning when — and only when — a flag would CHANGE a non-empty existing
value. These tests pin both directions of that guard.
"""
from __future__ import annotations

import pytest

from core.config_parser import ConfigParser
from utils.source_override_manager import SourceOverrideManager


def _mgr(tmp_path, monkeypatch, env_text: str) -> SourceOverrideManager:
    env = tmp_path / ".env"
    env.write_text(env_text, encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    return SourceOverrideManager(ConfigParser())


def test_no_warning_when_value_unchanged(tmp_path, monkeypatch, capsys):
    """AC#1: a flag equal to the existing `.env` value is silent."""
    mgr = _mgr(tmp_path, monkeypatch, "COMFYUI_SOURCE=container-cpu\n")
    assert mgr.apply_overrides({"COMFYUI_SOURCE": "container-cpu"}) is True
    assert "overridden by" not in capsys.readouterr().out


def test_no_warning_when_existing_value_empty(tmp_path, monkeypatch, capsys):
    """An empty `VAR=` assignment is establishing a value, not overriding a
    configured one — so no warning fires."""
    mgr = _mgr(tmp_path, monkeypatch, "COMFYUI_SOURCE=\n")
    assert mgr.apply_overrides({"COMFYUI_SOURCE": "container-cpu"}) is True
    assert "overridden by" not in capsys.readouterr().out


def test_warning_when_value_changes(tmp_path, monkeypatch, capsys):
    """AC#2: a flag that CHANGES a non-empty value prints an old→new warning
    naming the derived CLI flag, and still persists the new value."""
    env = tmp_path / ".env"
    env.write_text("COMFYUI_SOURCE=managed-localhost-mps\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))
    mgr = SourceOverrideManager(ConfigParser())

    assert mgr.apply_overrides({"COMFYUI_SOURCE": "container-cpu"}) is True

    out = capsys.readouterr().out
    assert "COMFYUI_SOURCE" in out
    assert "managed-localhost-mps" in out
    assert "container-cpu" in out
    assert "--comfyui-source" in out
    # The override is still persisted (behavior preserved).
    assert "COMFYUI_SOURCE=container-cpu\n" in env.read_text(encoding="utf-8")


def test_warning_only_for_changed_vars_in_batch(tmp_path, monkeypatch, capsys):
    """A batch touching several vars warns only for the ones that change."""
    mgr = _mgr(
        tmp_path,
        monkeypatch,
        "COMFYUI_SOURCE=managed-localhost-mps\nRAY_SOURCE=ray-container-cpu\n",
    )
    assert mgr.apply_overrides(
        {"COMFYUI_SOURCE": "container-cpu", "RAY_SOURCE": "ray-container-cpu"}
    ) is True
    out = capsys.readouterr().out
    assert "COMFYUI_SOURCE" in out and "--comfyui-source" in out
    # RAY_SOURCE was unchanged → must not appear in a warning line.
    assert "RAY_SOURCE" not in out


def test_no_warning_when_var_absent_from_env(tmp_path, monkeypatch, capsys):
    """A var not present in `.env` is appended by `update_env_file`; there is
    no prior value to override, so no warning fires."""
    mgr = _mgr(tmp_path, monkeypatch, "OTHER=1\n")
    assert mgr.apply_overrides({"COMFYUI_SOURCE": "container-gpu"}) is True
    assert "overridden by" not in capsys.readouterr().out


def test_no_warning_for_empty_override_set(tmp_path, monkeypatch, capsys):
    """apply_overrides({}) short-circuits and prints nothing."""
    mgr = _mgr(tmp_path, monkeypatch, "COMFYUI_SOURCE=container-cpu\n")
    assert mgr.apply_overrides({}) is True
    assert capsys.readouterr().out == ""
