"""Shared subprocess policy keeps audit commands bounded and redacted."""

from __future__ import annotations

import io
import json
import math
import os
import re
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


def test_run_bounded_publishes_ready_after_signal_guard_installation(tmp_path: Path):
    ready_file = tmp_path / "registered.ready"

    result = bounded_subprocess.run_bounded(
        [sys.executable, "-c", "print('started')"],
        ready_file=ready_file,
    )

    assert result.stdout == "started\n"
    assert ready_file.read_text(encoding="ascii") == "ready\n"


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
def test_run_bounded_preserves_sigterm_when_launch_also_fails(monkeypatch):
    def interrupted_launch(*_args, **_kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        raise OSError("simulated launch failure")

    monkeypatch.setattr(bounded_subprocess.subprocess, "Popen", interrupted_launch)

    with pytest.raises(SystemExit) as raised:
        bounded_subprocess.run_bounded(["tool"])

    assert raised.value.code == 128 + signal.SIGTERM


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_run_bounded_honors_ignored_sigterm(monkeypatch):
    real_popen = subprocess.Popen

    def interrupted_launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGTERM)
        return process

    monkeypatch.setattr(bounded_subprocess.subprocess, "Popen", interrupted_launch)
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        result = bounded_subprocess.run_bounded([sys.executable, "-c", "pass"])
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert result.returncode == 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_run_bounded_replays_callable_sigterm_handler(monkeypatch):
    received: list[int] = []
    real_popen = subprocess.Popen

    def interrupted_launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGTERM)
        return process

    monkeypatch.setattr(bounded_subprocess.subprocess, "Popen", interrupted_launch)
    previous = signal.signal(
        signal.SIGTERM,
        lambda signum, _frame: received.append(signum),
    )
    try:
        result = bounded_subprocess.run_bounded([sys.executable, "-c", "pass"])
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert result.returncode == 0
    assert received == [signal.SIGTERM]


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal-mask contract")
def test_run_bounded_does_not_block_sigterm_in_child():
    result = bounded_subprocess.run_bounded(
        [
            sys.executable,
            "-c",
            "import signal; "
            "blocked = signal.pthread_sigmask(signal.SIG_BLOCK, set()); "
            "print(signal.SIGTERM in blocked)",
        ]
    )

    assert result.stdout == "False\n"


def _wait_until_ready(marker: Path, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        assert process.poll() is None, "signal-test wrapper exited before readiness"
        time.sleep(0.01)
    assert marker.exists(), "signal-test wrapper did not become ready"


def _process_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return bool(status) and not status.startswith("Z")


def _wait_until_process_stops(pid: int) -> None:
    deadline = time.monotonic() + 2
    while _process_is_live(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _process_is_live(pid), f"descendant process {pid} survived"


def _read_pid(path: Path) -> int | None:
    return int(path.read_text()) if path.exists() else None


def _stop_test_process(
    process: subprocess.Popen, command_group: int | None = None
) -> None:
    groups = [process.pid, command_group]
    for process_group in groups:
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    for process_group in groups:
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if process.poll() is None:
        process.wait(timeout=1)


def _descendant_command(
    marker: Path, ready: Path, command_group: Path, descendant_pid: Path
) -> str:
    descendant = (
        "import pathlib,time; time.sleep(0.5); "
        f"pathlib.Path({str(marker)!r}).touch()"
    )
    return (
        "import os,pathlib,subprocess,sys,time; "
        f"pathlib.Path({str(command_group)!r}).write_text(str(os.getpid())); "
        f"child=subprocess.Popen({[sys.executable, '-c', descendant]!r}); "
        f"pathlib.Path({str(descendant_pid)!r}).write_text(str(child.pid)); "
        f"pathlib.Path({str(ready)!r}).touch(); "
        "time.sleep(10)"
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_run_bounded_terminates_descendants_when_wrapper_is_interrupted(
    tmp_path: Path,
):
    marker = tmp_path / "interrupted-orphan-ran"
    ready = tmp_path / "interrupted-ready"
    command_group = tmp_path / "interrupted-command-group"
    descendant_pid = tmp_path / "interrupted-descendant-pid"
    child = _descendant_command(marker, ready, command_group, descendant_pid)
    wrapper = (
        "from scripts.bounded_subprocess import run_bounded; "
        f"run_bounded({[sys.executable, '-c', child]!r})"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", wrapper], cwd=ROOT, start_new_session=True
    )
    try:
        _wait_until_ready(ready, process)
        os.kill(process.pid, signal.SIGINT)
        process.wait(timeout=2)
        _wait_until_process_stops(int(descendant_pid.read_text()))
        assert process.returncode != 0
        assert not marker.exists()
    finally:
        _stop_test_process(process, _read_pid(command_group))


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_cli_sigterm_terminates_command_tree(tmp_path: Path):
    marker = tmp_path / "sigterm-orphan-ran"
    ready = tmp_path / "sigterm-ready"
    command_group = tmp_path / "sigterm-command-group"
    descendant_pid = tmp_path / "sigterm-descendant-pid"
    child = _descendant_command(marker, ready, command_group, descendant_pid)
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
        start_new_session=True,
    )
    try:
        _wait_until_ready(ready, process)
        os.kill(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
        _wait_until_process_stops(int(descendant_pid.read_text()))
        assert process.returncode == 128 + signal.SIGTERM
        assert not marker.exists()
    finally:
        _stop_test_process(process, _read_pid(command_group))


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_direct_run_bounded_sigterm_terminates_command_tree(tmp_path: Path):
    marker = tmp_path / "direct-sigterm-orphan-ran"
    ready = tmp_path / "direct-sigterm-ready"
    command_group = tmp_path / "direct-sigterm-command-group"
    descendant_pid = tmp_path / "direct-sigterm-descendant-pid"
    child = _descendant_command(marker, ready, command_group, descendant_pid)
    wrapper = (
        "from scripts.bounded_subprocess import run_bounded; "
        f"run_bounded({[sys.executable, '-c', child]!r})"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", wrapper], cwd=ROOT, start_new_session=True
    )
    try:
        _wait_until_ready(ready, process)
        os.kill(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
        _wait_until_process_stops(int(descendant_pid.read_text()))
        assert process.returncode == 128 + signal.SIGTERM
        assert not marker.exists()
    finally:
        _stop_test_process(process, _read_pid(command_group))


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_cli_sigint_is_redacted_and_has_no_traceback(tmp_path: Path):
    ready = tmp_path / "cli-sigint-ready"
    command_group = tmp_path / "cli-sigint-command-group"
    command = (
        "import os,pathlib,time; "
        f"pathlib.Path({str(command_group)!r}).write_text(str(os.getpid())); "
        f"pathlib.Path({str(ready)!r}).touch(); "
        "time.sleep(10)"
    )
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
            command,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _wait_until_ready(ready, process)
        os.kill(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=2)
        assert process.returncode == 130
        assert stdout == "interrupt test interrupted (subprocess details redacted)\n"
        assert "Traceback" not in stderr
        assert str(ROOT) not in stderr
    finally:
        _stop_test_process(process, _read_pid(command_group))


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


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout_seconds": math.nan}, "timeout_seconds"),
        ({"timeout_seconds": math.inf}, "timeout_seconds"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"timeout_seconds": "1"}, "timeout_seconds"),
        ({"max_output_bytes": 1.5}, "max_output_bytes"),
        ({"max_output_bytes": True}, "max_output_bytes"),
        ({"max_output_bytes": "1"}, "max_output_bytes"),
    ],
)
def test_run_bounded_rejects_invalid_bounds_before_launch(
    monkeypatch, kwargs, message
):
    monkeypatch.setattr(
        bounded_subprocess.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("invalid bounds launched a process"),
    )

    with pytest.raises(ValueError, match=message):
        bounded_subprocess.run_bounded(["unused"], **kwargs)


def test_run_bounded_propagates_reader_failure(monkeypatch):
    class FailingStream:
        def read(self, _size):
            raise OSError("reader boom")

    class FakeProcess:
        pid = 12345
        stdout = FailingStream()
        stderr = io.BytesIO()
        returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, **_kwargs):
            return self.returncode

    process = FakeProcess()
    monkeypatch.setattr(
        bounded_subprocess.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(bounded_subprocess.os, "killpg", lambda *_args: None)

    with pytest.raises(OSError, match="reader boom"):
        bounded_subprocess.run_bounded(["unused"])


def test_run_bounded_preserves_output_error_when_kill_is_denied(monkeypatch):
    def denied(*_args):
        raise PermissionError("simulated signaling race")

    monkeypatch.setattr(bounded_subprocess.os, "killpg", denied)
    with pytest.raises(bounded_subprocess.CommandOutputTooLarge):
        bounded_subprocess.run_bounded(
            [sys.executable, "-c", "print('x' * 2048)"],
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


def test_main_redacts_unexpected_runner_failure(monkeypatch, capsys):
    def fail_safely(*_args, **_kwargs):
        raise OSError("secret-token")

    monkeypatch.setattr(bounded_subprocess, "run_bounded", fail_safely)

    assert _call_main(
        monkeypatch,
        ["--label", "inventory", "--", "unused"],
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == (
        "inventory failed (internal subprocess error; details redacted)\n"
    )
    assert captured.err == ""
    assert "secret-token" not in captured.out


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_main_normalizes_signal_terminated_child_status(monkeypatch, capsys):
    assert _call_main(
        monkeypatch,
        [
            "--label",
            "killed",
            "--",
            sys.executable,
            "-c",
            "import os,signal; os.kill(os.getpid(), signal.SIGKILL)",
        ],
    ) == 128 + signal.SIGKILL

    assert capsys.readouterr().out == (
        "killed failed (exit 137; subprocess output redacted)\n"
    )


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


def _services_lint() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/services-lint.yml").read_text(encoding="utf-8"))


def test_every_services_lint_job_has_a_deadline() -> None:
    workflow = _services_lint()
    for job, spec in workflow["jobs"].items():
        assert spec["timeout-minutes"] > 0, job
        assert spec["runs-on"] == "ubuntu-24.04", job
    assert workflow["jobs"]["build-validation"]["timeout-minutes"] == 360


# ── services-lint layout (#1176) ────────────────────────────────────────

REQUIRED_CHECKS = (
    "Manifest lint + unit tests",
    "Compose merge + byte-equivalence + source-permutation matrix",
    "Docs drift + audit scripts",
    "Build-validation (Dockerfile + requirements.txt installability)",
)
SPLIT_SUITES = {"unit-fast", "lint", "python-floor", "component-tests"}


def _gate_script(workflow: dict) -> str:
    step = workflow["jobs"]["required-lint"]["steps"][-1]
    return step["run"]


def _run_gate(workflow: dict, results: dict[str, str]) -> int:
    needs = {job: {"result": result, "outputs": {}} for job, result in results.items()}
    return subprocess.run(
        ["bash", "-c", _gate_script(workflow)], env={**os.environ, "NEEDS": json.dumps(needs)},
        capture_output=True, text=True, check=False, timeout=30,
    ).returncode


def test_the_four_required_checks_keep_their_names() -> None:
    """AC3: each ruleset-required name is exactly one job's name."""
    names = [spec["name"] for spec in _services_lint()["jobs"].values()]
    assert [names.count(check) for check in REQUIRED_CHECKS] == [1, 1, 1, 1]


def test_required_lint_gate_passes_only_when_every_split_suite_succeeds() -> None:
    """AC3/AC4: the suites split out of the former single job stay required
    through the gate that carries its name, which runs, and fails, even when
    a dependency failed, was skipped or was cancelled."""
    workflow = _services_lint()
    gate = workflow["jobs"]["required-lint"]
    everything = dict.fromkeys(SPLIT_SUITES, "success")

    assert (gate["name"], set(gate["needs"]), gate["if"]) == (
        "Manifest lint + unit tests", SPLIT_SUITES, "always()")
    assert [_run_gate(workflow, {**everything, "python-floor": outcome}) != 0
            for outcome in ("success", "failure", "cancelled", "skipped")] == [False, True, True, True]


def test_superseded_pull_request_runs_are_cancelled_and_pushes_never_are() -> None:
    """AC2: one group per pull request, cancelling its older run; every push
    run is a group of its own and never cancels."""
    concurrency = _services_lint()["concurrency"]

    assert "format('pr-{0}', github.event.pull_request.number)" in concurrency["group"]
    assert "format('push-{0}', github.run_id)" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def _pytest_commands(workflow: dict) -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {}
    for job, spec in workflow["jobs"].items():
        for step in spec["steps"]:
            run = " ".join(step.get("run", "").replace("\\\n", " ").split())
            if "pytest" in run or "validate_fragments" in run or "shellcheck -x" in run:
                commands.setdefault(job, []).append(run)
    return commands


#: Each suite the former single job ran, and the jobs that run it now.
SUITE_INVOCATIONS = {
    "--cov-fail-under=69": ["lint"],  # the bootstrapper coverage floor, once
    "--cov-fail-under=79": ["lint"],  # the Backend suite
    "uv run --python 3.10 --isolated --locked --group dev pytest tests/": ["python-floor"],
    "test_mcp_servers_framework.py": ["component-tests"],
    "-m pytest tests/ -q -W error": ["lint"],  # the Backend suite
    "cd services/asset-worker/app": ["component-tests"],
    "cd services/asset-baker/app": ["component-tests"],
    # The bootstrapper suite on 3.12: with containers, and the no-Docker signal.
    "uv run pytest tests/ -q --ignore=tests/test_fragment_equivalence.py "
    "--ignore=tests/test_source_permutations.py": ["lint", "unit-fast"],
    "tools.validate_fragments": ["lint"],
    "shellcheck -x": ["lint"],
}


def test_every_suite_invocation_survives_the_split() -> None:
    """AC4: each suite the single job used to run still runs, once, on the
    same events and Python version; the bootstrapper coverage floor is
    enforced in exactly one place."""
    workflow = _services_lint()
    flat = [(job, command) for job, runs in _pytest_commands(workflow).items() for command in runs]
    events = workflow.get("on", workflow.get(True))
    pythons = {
        job: [step["with"]["python-version"] for step in spec["steps"]
              if str(step.get("uses", "")).startswith("actions/setup-python@")]
        for job, spec in workflow["jobs"].items() if job in SPLIT_SUITES
    }

    assert {fragment: [job for job, command in flat if fragment in command]
            for fragment in SUITE_INVOCATIONS} == SUITE_INVOCATIONS
    assert pythons == {"lint": ["3.12"], "unit-fast": ["3.12"], "python-floor": ["3.10"],
                       "component-tests": ["3.12"]}
    assert (set(events), events["pull_request"]["branches"], events["push"]["branches"]) == (
        {"pull_request", "push"}, ["main", "develop"], ["main", "develop"])


def test_python_floor_runs_the_full_suite_with_the_images_lint_pulls() -> None:
    """AC5: the 3.10 floor is the whole suite, not a subset, and its
    Docker-backed tests find the same pinned images locally."""
    jobs = _services_lint()["jobs"]
    pull = "Pull exact backup integration images"
    floor = {step.get("name"): step for step in jobs["python-floor"]["steps"]}
    lint = {step.get("name"): step for step in jobs["lint"]["steps"]}
    command = " ".join(floor["Run bootstrapper tests on Python 3.10"]["run"].split())

    assert floor[pull] == lint[pull]
    assert command == (
        "uv run --python 3.10 --isolated --locked --group dev pytest tests/ -q "
        "--ignore=tests/test_fragment_equivalence.py --ignore=tests/test_source_permutations.py"
    )


def test_integration_cleanup_is_owned_by_one_job_and_always_runs() -> None:
    """AC6: the job that opts into the backup integration tests alone owns
    their cleanup, which runs even after a failure."""
    jobs = _services_lint()["jobs"]
    owners = [
        job for job, spec in jobs.items()
        if any(step.get("env", {}).get("ATLAS_BACKUP_PRODUCTION_IMAGE_INTEGRATION") for step in spec["steps"])
    ]
    cleanups = [
        (job, step) for job, spec in jobs.items() for step in spec["steps"]
        if step.get("name") == "Remove integration test containers"
    ]

    assert (owners, [job for job, _ in cleanups]) == (["lint"], ["lint"])
    assert (cleanups[0][1]["if"], cleanups[0][1]["continue-on-error"],
            jobs["lint"]["steps"][-1] is cleanups[0][1]) == ("always()", True, True)
    assert "grep -E '^atlas[-_]'" in cleanups[0][1]["run"]


def test_fast_job_runs_the_suite_as_a_contributor_without_docker() -> None:
    """AC1: the early signal pulls no image and reaches no daemon, so
    container-backed tests skip themselves and it finishes first."""
    jobs = _services_lint()["jobs"]
    fast = jobs["unit-fast"]
    step = fast["steps"][-1]

    assert not any("docker pull" in s.get("run", "") for s in fast["steps"])
    assert (step["env"]["CI"], step["env"]["DOCKER_HOST"].startswith("unix:///tmp/")) == ("", True)
    assert fast["timeout-minutes"] < jobs["lint"]["timeout-minutes"]


def test_every_docs_publication_job_has_a_deadline() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docs-pages.yml").read_text(encoding="utf-8")
    )
    for job in ("build", "deploy", "wiki"):
        assert workflow["jobs"][job]["timeout-minutes"] > 0, job


def test_local_docs_build_and_check_commands_use_bounded_runner() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    # 11 before #1053 added the external-asset preflight to docs-build,
    # docs-check and docs-serve, and the docs-assets-verify target.
    assert makefile.count("\t$(BOUNDED)") == 15
    assert makefile.count("--forward-stderr") == 15


def test_redacted_failure_omits_captured_output_and_command():
    message = bounded_subprocess.redacted_failure("runtime lock", 7)
    assert message == "runtime lock failed (exit 7; subprocess output redacted)"
    assert "secret" not in message


def test_main_forwards_failure_output_when_explicitly_opted_in(monkeypatch, capsys):
    """--forward-stderr is the caller asserting the output is non-secret build
    logs. Honouring it only on success made it useless in the one case anyone
    needs it: a strict MkDocs build that fails on a transient asset fetch
    printed the redaction line and nothing else, so CI could never say WHICH
    fetch died (#934, #941)."""
    assert _call_main(
        monkeypatch,
        [
            "--label",
            "strict MkDocs build",
            "--forward-stderr",
            "--",
            sys.executable,
            "-c",
            "import sys; print('to stdout'); "
            "print('Could not fetch fonts.gstatic.com', file=sys.stderr); "
            "sys.exit(1)",
        ],
    ) == 1
    captured = capsys.readouterr()
    assert "strict MkDocs build failed (exit 1); output follows:" in captured.out
    assert "redacted" not in captured.out, (
        "saying 'output redacted' while printing the output teaches readers "
        "the detail below is not there"
    )
    assert "to stdout" in captured.out
    assert "Could not fetch fonts.gstatic.com" in captured.err


def test_a_step_that_does_not_opt_in_stays_fully_redacted_on_failure(monkeypatch, capsys):
    """The opt-in is the whole safety boundary — without it, nothing leaks."""
    assert _call_main(
        monkeypatch,
        [
            "--label",
            "inventory",
            "--",
            sys.executable,
            "-c",
            "import sys; print('secret-token'); "
            "print('secret-token', file=sys.stderr); sys.exit(2)",
        ],
    ) == 2
    captured = capsys.readouterr()
    assert "secret-token" not in captured.out
    assert "secret-token" not in captured.err
    assert "subprocess output redacted" in captured.out


def _services_lint_steps() -> list[dict]:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/services-lint.yml").read_text(encoding="utf-8")
    )
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def _docs_pages_steps() -> list[dict]:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docs-pages.yml").read_text(encoding="utf-8")
    )
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def _step_index(steps: list[dict], predicate) -> int:
    matches = [i for i, step in enumerate(steps) if predicate(step)]
    assert matches, "no workflow step matched"
    return matches[0]


def _is_privacy_cache(step: dict) -> bool:
    return str(step.get("uses", "")).startswith("actions/cache") and (
        step.get("with", {}).get("path") == ".cache"
    )


@pytest.mark.parametrize("steps_loader", [_services_lint_steps, _docs_pages_steps])
def test_every_docs_job_caches_the_mkdocs_privacy_assets(steps_loader):
    """`mkdocs build --strict` + the Material privacy plugin downloads ~20
    external assets at build time, and `.cache` is gitignored. Without a CI
    cache the docs job refetches all of them every run, so one transient
    failure is a hard --strict error on a diff that touched no external URL
    (#934, #941)."""
    steps = steps_loader()
    cache_at = _step_index(steps, _is_privacy_cache)
    build_at = _step_index(steps, lambda step: step.get("run") == "make docs-check")
    assert cache_at < build_at, "the cache must be restored before the strict build"


@pytest.mark.parametrize("steps_loader", [_services_lint_steps, _docs_pages_steps])
def test_every_privacy_cache_key_hashes_files_that_actually_exist(steps_loader):
    """A `hashFiles()` pattern that matches nothing yields an EMPTY string, so
    the key silently collapses to a constant and the cache never re-primes when
    the fonts or stylesheets change. The first version of this step hashed
    `mkdocs.yml` (generated, absent when the step runs) and
    `docs/stylesheets/**` (wrong path — it is `docs/assets/stylesheets`), and CI
    logged `Cache saved with key: mkdocs-privacy-` with no hash at all.
    """
    import re

    steps = steps_loader()
    cache = steps[_step_index(steps, _is_privacy_cache)]
    key = cache["with"]["key"]
    patterns = re.findall(r"hashFiles\(([^)]*)\)", key)
    assert patterns, f"the cache key must hash something: {key!r}"

    globs = re.findall(r"'([^']+)'", patterns[0])
    assert globs, f"no glob literals found in {patterns[0]!r}"
    for pattern in globs:
        # Actions' hashFiles treats a trailing `**` as "every file below";
        # pathlib's `**` matches DIRECTORIES, so translate before comparing.
        translated = pattern[:-1] + "*/*" if pattern.endswith("**") else pattern
        matches = [path for path in ROOT.glob(translated) if path.is_file()]
        assert matches, (
            f"cache-key pattern {pattern!r} matches no file — hashFiles would "
            f"return an empty string and the key would never change"
        )
        # …and they must be TRACKED. A generated file (mkdocs.yml) exists in a
        # local checkout but not in CI at the point the cache step runs, so
        # hashing it is empty there and green here — the worst combination.
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", *[str(m) for m in matches]],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert tracked.returncode == 0, (
            f"cache-key pattern {pattern!r} matches untracked/generated files; "
            f"they may not exist when the step runs: {tracked.stderr.strip()}"
        )


_FAKE_TRIVY = """#!/usr/bin/env bash
tag="${!#}"
echo "trivy $tag" >> "$CALLS"
for rule in $TRIVY_STATUS; do
  case "$tag" in *"/${rule%%=*}:"*) exit "${rule##*=}" ;; esac
done
exit 0
"""


def _run_final_image_scan(
    tmp_path: Path, trivy_status: str
) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    """Run the required scan step against stub docker, uv and trivy.

    `trivy_status` maps image tags to the stub's exit code, for example
    "runtime-0-amd64=3". Returns the step result, the images Trivy was asked
    to scan, and the step summary.
    """
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/services-lint.yml").read_text(encoding="utf-8")
    )
    script = next(
        step["run"]
        for step in workflow["jobs"]["final-image-scan"]["steps"]
        if step.get("name") == "Build and scan local Compose and init images"
    )
    workspace, stubs = tmp_path / "workspace", tmp_path / "bin"
    for context, dockerfile in re.findall(r'"(services/[^"|]+)\|([^"]+)"', script):
        (workspace / context).mkdir(parents=True, exist_ok=True)
        (workspace / context / dockerfile).parent.mkdir(parents=True, exist_ok=True)
    stubs.mkdir()
    (workspace / "scripts").mkdir()
    for path, body in (
        (stubs / "docker", "#!/bin/sh\nexit 0\n"),
        (stubs / "uv", "#!/bin/sh\nexit 0\n"),
        (stubs / "trivy", _FAKE_TRIVY),
        (workspace / "scripts/smoke_spark_s3a.sh", "#!/bin/sh\nexit 0\n"),
    ):
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    calls, summary = tmp_path / "calls.log", tmp_path / "summary.md"
    calls.touch()
    summary.touch()
    env = {
        **os.environ,
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls),
        "TRIVY_STATUS": trivy_status,
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_SHA": "0123abcd",
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    result = subprocess.run(
        ["bash", "-c", script], cwd=workspace, env=env,
        capture_output=True, text=True, timeout=120, check=False,
    )
    scanned = [line.split()[1] for line in calls.read_text().splitlines()]
    return result, scanned, summary.read_text(encoding="utf-8")


def test_final_image_scan_reports_every_failing_image_before_failing(
    tmp_path: Path,
) -> None:
    """#1308: a finding no longer hides the images after it."""
    result, scanned, summary = _run_final_image_scan(
        tmp_path, "runtime-0-amd64=3 runtime-4-arm64=3"
    )

    assert result.returncode == 1, result.stderr
    # 25 contexts on amd64 and arm64, except asset-baker (amd64 only).
    assert len(scanned) == 49
    assert summary.splitlines() == [
        "### Final-image findings in 2 image(s)",
        "- services/airflow/build (amd64)",
        "- services/backend/app (arm64)",
    ]
    assert result.stdout.count("::error title=Final-image findings::") == 2


def test_final_image_scan_stops_at_once_when_trivy_itself_fails(
    tmp_path: Path,
) -> None:
    """A Trivy error (exit 1) is not a finding and must not be deferred."""
    result, scanned, summary = _run_final_image_scan(tmp_path, "runtime-0-amd64=1")

    assert (result.returncode, len(scanned), summary) == (1, 1, "")
    assert "::error title=Trivy failed::services/airflow/build (amd64)" in result.stdout


def test_final_image_scan_passes_when_every_image_is_clean(tmp_path: Path) -> None:
    result, scanned, summary = _run_final_image_scan(tmp_path, "")

    assert (result.returncode, len(scanned), summary) == (0, 49, ""), result.stderr
