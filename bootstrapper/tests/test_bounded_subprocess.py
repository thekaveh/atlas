"""Shared subprocess policy keeps audit commands bounded and redacted."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from scripts import bounded_subprocess


ROOT = Path(__file__).resolve().parents[2]


def _call_main(monkeypatch, arguments: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["bounded_subprocess", *arguments])
    return bounded_subprocess.main()


def test_run_bounded_translates_timeout_without_command_details(tmp_path: Path):
    with pytest.raises(bounded_subprocess.CommandTimedOut) as raised:
        bounded_subprocess.run_bounded(
            [sys.executable, "-c", "import time; time.sleep(10)", "secret-token"],
            cwd=tmp_path,
            timeout_seconds=0.05,
        )

    assert "secret-token" not in str(raised.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_run_bounded_terminates_descendants_on_timeout(tmp_path: Path):
    marker = tmp_path / "orphan-ran"
    child = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import pathlib,time; time.sleep(0.4); pathlib.Path(r\"%s\").touch()']); "
        "time.sleep(10)"
    ) % marker

    with pytest.raises(bounded_subprocess.CommandTimedOut):
        bounded_subprocess.run_bounded(
            [sys.executable, "-c", child], timeout_seconds=0.05
        )

    time.sleep(0.6)
    assert not marker.exists()


def test_run_bounded_redacts_launch_failure(tmp_path: Path):
    with pytest.raises(bounded_subprocess.CommandLaunchError) as raised:
        bounded_subprocess.run_bounded(
            ["definitely-not-an-atlas-command", "secret-token"], cwd=tmp_path
        )

    assert "secret-token" not in str(raised.value)


def test_main_reports_launch_failure_without_traceback(monkeypatch, capsys, tmp_path):
    assert _call_main(
        monkeypatch,
        [
            "--label",
            "runtime lock",
            "--cwd",
            str(tmp_path),
            "--",
            "definitely-not-an-atlas-command",
            "secret-token",
        ],
    ) == 126
    output = capsys.readouterr().out
    assert output == "runtime lock could not start (subprocess details redacted)\n"
    assert "Traceback" not in output
    assert "secret-token" not in output


def test_main_preserves_success_output_and_cwd(monkeypatch, capsys, tmp_path):
    assert _call_main(
        monkeypatch,
        [
            "--label",
            "inventory",
            "--cwd",
            str(tmp_path),
            "--",
            sys.executable,
            "-c",
            "import pathlib; print(pathlib.Path.cwd().name)",
        ],
    ) == 0
    assert capsys.readouterr().out == f"{tmp_path.name}\n"


def test_main_redacts_nonzero_failure(monkeypatch, capsys):
    assert _call_main(
        monkeypatch,
        [
            "--label",
            "inventory",
            "--",
            sys.executable,
            "-c",
            "import sys; print('secret-token', file=sys.stderr); sys.exit(7)",
        ],
    ) == 7
    output = capsys.readouterr().out
    assert output == "inventory failed (exit 7; subprocess output redacted)\n"
    assert "secret-token" not in output


def test_main_reports_timeout_without_command_details(monkeypatch, capsys):
    assert _call_main(
        monkeypatch,
        [
            "--label",
            "inventory",
            "--timeout-seconds",
            "1",
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(10)",
            "secret-token",
        ],
    ) == 124
    output = capsys.readouterr().out
    assert output == "inventory timed out after 1 seconds\n"
    assert "secret-token" not in output


def test_required_audit_paths_do_not_call_unbounded_subprocesses() -> None:
    paths = (
        ROOT / "scripts/docs/check_site.py",
        ROOT / "scripts/docs/check_docs.py",
        ROOT / "scripts/docs/push_wiki.py",
        ROOT / "scripts/docs/heading_quality.py",
        ROOT / "scripts/check-compose-source-deps.py",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "subprocess.run(" not in text, path
        assert "subprocess.check_output(" not in text, path


def test_every_required_services_lint_job_has_a_deadline() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/services-lint.yml").read_text(encoding="utf-8")
    )
    for job in ("lint", "compose-equivalence", "audit-scripts", "build-validation"):
        assert workflow["jobs"][job]["timeout-minutes"] > 0, job


def test_redacted_failure_omits_captured_output_and_command():
    message = bounded_subprocess.redacted_failure("runtime lock", 7)
    assert message == "runtime lock failed (exit 7; subprocess output redacted)"
    assert "secret" not in message
