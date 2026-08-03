"""Shared subprocess policy keeps audit commands bounded and redacted."""

from __future__ import annotations

import subprocess

import pytest

from scripts import bounded_subprocess


def test_run_bounded_translates_timeout_without_command_details(monkeypatch):
    def time_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["tool", "secret-token"], 3)

    monkeypatch.setattr(bounded_subprocess.subprocess, "run", time_out)

    with pytest.raises(bounded_subprocess.CommandTimedOut) as raised:
        bounded_subprocess.run_bounded(["tool", "secret-token"], timeout_seconds=3)

    assert "secret-token" not in str(raised.value)


def test_redacted_failure_omits_captured_output_and_command():
    message = bounded_subprocess.redacted_failure("runtime lock", 7)
    assert message == "runtime lock failed (exit 7; subprocess output redacted)"
    assert "secret" not in message
