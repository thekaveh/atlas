from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bootstrapper.scripts import generate_logo


def test_chafa_uses_bounded_process_runner(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], float]] = []

    def fake_run(command, *, timeout_seconds):
        calls.append((list(command), timeout_seconds))
        return subprocess.CompletedProcess(command, 0, "rendered", "")

    monkeypatch.setattr(generate_logo, "run_with_deadline", fake_run)

    assert generate_logo._chafa(tmp_path / "logo.png", 80, 24) == "rendered"
    assert calls[0][1] == generate_logo._ARTIFACT_TOOL_TIMEOUT_SECONDS


def test_chafa_reports_a_stable_timeout(monkeypatch, tmp_path: Path) -> None:
    def time_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["secret-command"], 1)

    monkeypatch.setattr(generate_logo, "run_with_deadline", time_out)

    with pytest.raises(RuntimeError, match="chafa timed out") as raised:
        generate_logo._chafa(tmp_path / "logo.png", 80, 24)

    assert "secret-command" not in str(raised.value)


def test_pngquant_uses_bounded_process_runner(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], float]] = []

    monkeypatch.setattr(generate_logo.shutil, "which", lambda _name: "pngquant")

    def fake_run(command, *, timeout_seconds):
        calls.append((list(command), timeout_seconds))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(generate_logo, "run_with_deadline", fake_run)
    generate_logo._optimize_png(tmp_path / "logo.png")

    assert calls[0][0][0] == "pngquant"
    assert calls[0][1] == generate_logo._ARTIFACT_TOOL_TIMEOUT_SECONDS
