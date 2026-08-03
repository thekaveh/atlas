"""Shared subprocess policy keeps audit commands bounded and redacted."""

from __future__ import annotations

import os
import signal
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_run_bounded_waits_for_inherited_output_pipes_after_parent_exits(tmp_path):
    marker = tmp_path / "detached-descendant-ran"
    child = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import pathlib,time; time.sleep(0.4); pathlib.Path(r\"%s\").touch()'])"
    ) % marker

    with pytest.raises(bounded_subprocess.CommandTimedOut):
        bounded_subprocess.run_bounded(
            [sys.executable, "-c", child], timeout_seconds=0.05
        )

    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_run_bounded_stops_descendant_after_successful_leader_exits(tmp_path):
    marker = tmp_path / "successful-leader-orphan-ran"
    child = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import pathlib,time; time.sleep(0.4); pathlib.Path(r\"%s\").touch()'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    ) % marker

    result = bounded_subprocess.run_bounded([sys.executable, "-c", child])

    assert result.returncode == 0
    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_run_bounded_handles_sigterm_before_popen_returns(monkeypatch, tmp_path):
    marker = tmp_path / "launch-race-orphan-ran"
    real_popen = subprocess.Popen

    def interrupted_launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGTERM)
        return process

    monkeypatch.setattr(bounded_subprocess.subprocess, "Popen", interrupted_launch)
    command = [
        sys.executable,
        "-c",
        "import pathlib,time; time.sleep(0.4); "
        f"pathlib.Path({str(marker)!r}).touch()",
    ]

    with pytest.raises(SystemExit) as raised:
        bounded_subprocess.run_bounded(command)

    assert raised.value.code == 128 + signal.SIGTERM
    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_run_bounded_terminates_descendants_when_wrapper_is_interrupted(
    tmp_path: Path,
):
    marker = tmp_path / "interrupted-orphan-ran"
    child = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import pathlib,time; time.sleep(0.5); pathlib.Path(r\"%s\").touch()']); "
        "time.sleep(10)"
    ) % marker
    wrapper = (
        "from scripts.bounded_subprocess import run_bounded; "
        f"run_bounded({[sys.executable, '-c', child]!r})"
    )
    process = subprocess.Popen([sys.executable, "-c", wrapper], cwd=ROOT)
    time.sleep(0.1)
    os.kill(process.pid, signal.SIGINT)
    process.wait(timeout=2)

    time.sleep(0.7)
    assert process.returncode != 0
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_cli_sigterm_terminates_command_tree(tmp_path: Path):
    marker = tmp_path / "sigterm-orphan-ran"
    child = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import pathlib,time; time.sleep(0.5); pathlib.Path(r\"%s\").touch()']); "
        "time.sleep(10)"
    ) % marker
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.bounded_subprocess",
            "--label",
            "interrupt test",
            "--",
            sys.executable,
            "-c",
            child,
        ],
        cwd=ROOT,
    )
    time.sleep(0.1)
    os.kill(process.pid, signal.SIGTERM)
    process.wait(timeout=2)

    time.sleep(0.7)
    assert process.returncode == 128 + signal.SIGTERM
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_direct_run_bounded_sigterm_terminates_command_tree(tmp_path: Path):
    marker = tmp_path / "direct-sigterm-orphan-ran"
    child = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import pathlib,time; time.sleep(0.5); pathlib.Path(r\"%s\").touch()']); "
        "time.sleep(10)"
    ) % marker
    wrapper = (
        "from scripts.bounded_subprocess import run_bounded; "
        f"run_bounded({[sys.executable, '-c', child]!r})"
    )
    process = subprocess.Popen([sys.executable, "-c", wrapper], cwd=ROOT)
    time.sleep(0.1)
    os.kill(process.pid, signal.SIGTERM)
    process.wait(timeout=2)

    time.sleep(0.7)
    assert process.returncode == 128 + signal.SIGTERM
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_cli_sigint_is_redacted_and_has_no_traceback():
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.bounded_subprocess",
            "--label",
            "interrupt test",
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(10)",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.1)
    os.kill(process.pid, signal.SIGINT)
    stdout, stderr = process.communicate(timeout=2)

    assert process.returncode == 130
    assert stdout == "interrupt test interrupted (subprocess details redacted)\n"
    assert "Traceback" not in stderr
    assert str(ROOT) not in stderr


def test_run_bounded_rejects_excessive_combined_output():
    with pytest.raises(bounded_subprocess.CommandOutputTooLarge):
        bounded_subprocess.run_bounded(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 700); "
                "sys.stderr.write('y' * 700)",
            ],
            max_output_bytes=1024,
        )


def test_native_windows_fails_closed_before_launch(monkeypatch):
    monkeypatch.setattr(bounded_subprocess.os, "name", "nt")

    def unexpected_launch(*_args, **_kwargs):
        raise AssertionError("native Windows must not launch an unbounded tree")

    monkeypatch.setattr(bounded_subprocess.subprocess, "Popen", unexpected_launch)
    with pytest.raises(bounded_subprocess.CommandLaunchError):
        bounded_subprocess.run_bounded(["tool"])


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


def test_main_suppresses_success_stderr_by_default(monkeypatch, capsys):
    assert _call_main(
        monkeypatch,
        [
            "--label",
            "inventory",
            "--",
            sys.executable,
            "-c",
            "import sys; print('private-registry-token', file=sys.stderr)",
        ],
    ) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_main_can_explicitly_forward_success_stderr(monkeypatch, capsys):
    assert _call_main(
        monkeypatch,
        [
            "--label",
            "docs build",
            "--forward-stderr",
            "--",
            sys.executable,
            "-c",
            "import sys; print('build progress', file=sys.stderr)",
        ],
    ) == 0
    assert capsys.readouterr().err == "build progress\n"


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


def test_local_docs_build_and_check_commands_use_bounded_runner() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert makefile.count("\t$(BOUNDED)") == 11
    assert makefile.count("--forward-stderr") == 11


def test_redacted_failure_omits_captured_output_and_command():
    message = bounded_subprocess.redacted_failure("runtime lock", 7)
    assert message == "runtime lock failed (exit 7; subprocess output redacted)"
    assert "secret" not in message
