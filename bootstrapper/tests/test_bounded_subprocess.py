"""Shared subprocess policy keeps audit commands bounded and redacted."""

from __future__ import annotations

import io
import math
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


def test_every_required_services_lint_job_has_a_deadline() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/services-lint.yml").read_text(encoding="utf-8")
    )
    for job in ("lint", "compose-equivalence", "audit-scripts", "build-validation"):
        assert workflow["jobs"][job]["timeout-minutes"] > 0, job
        assert workflow["jobs"][job]["runs-on"] == "ubuntu-24.04", job


def test_every_docs_publication_job_has_a_deadline() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docs-pages.yml").read_text(encoding="utf-8")
    )
    for job in ("build", "deploy", "wiki"):
        assert workflow["jobs"][job]["timeout-minutes"] > 0, job


def test_local_docs_build_and_check_commands_use_bounded_runner() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert makefile.count("\t$(BOUNDED)") == 11
    assert makefile.count("--forward-stderr") == 11


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


def _step_index(steps: list[dict], predicate) -> int:
    matches = [i for i, step in enumerate(steps) if predicate(step)]
    assert matches, "no workflow step matched"
    return matches[0]


def _is_privacy_cache(step: dict) -> bool:
    return str(step.get("uses", "")).startswith("actions/cache") and (
        step.get("with", {}).get("path") == ".cache"
    )


def test_the_docs_job_caches_the_mkdocs_privacy_assets():
    """`mkdocs build --strict` + the Material privacy plugin downloads ~20
    external assets at build time, and `.cache` is gitignored. Without a CI
    cache the docs job refetches all of them every run, so one transient
    failure is a hard --strict error on a diff that touched no external URL
    (#934, #941)."""
    steps = _services_lint_steps()
    cache_at = _step_index(steps, _is_privacy_cache)
    build_at = _step_index(steps, lambda s: "strict build" in s.get("name", ""))
    assert cache_at < build_at, "the cache must be restored before the strict build"


def test_the_privacy_cache_key_hashes_files_that_actually_exist():
    """A `hashFiles()` pattern that matches nothing yields an EMPTY string, so
    the key silently collapses to a constant and the cache never re-primes when
    the fonts or stylesheets change. The first version of this step hashed
    `mkdocs.yml` (generated, absent when the step runs) and
    `docs/stylesheets/**` (wrong path — it is `docs/assets/stylesheets`), and CI
    logged `Cache saved with key: mkdocs-privacy-` with no hash at all.
    """
    import re

    steps = _services_lint_steps()
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

