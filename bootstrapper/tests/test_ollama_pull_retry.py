"""Regression: ollama-pull retries a transient model-pull failure.

A default-active model (e.g. `qwen3-embedding:0.6b`) that hits a transient
registry/network blip must not be left unpulled after a single attempt.
`services/ollama/pull/scripts/pull.sh` wraps each model pull in a bounded retry
loop and only logs the terminal ERROR after all attempts fail — staying
non-fatal so the rest of the set still pulls. The suite combines structural
assertions with a fake-command behavioral run that verifies the configured
stall bound and retry count.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.process_runner import run_with_deadline

REPO_ROOT = Path(__file__).resolve().parents[2]
PULL_SH = REPO_ROOT / "services" / "ollama" / "pull" / "scripts" / "pull.sh"


def _src() -> str:
    return PULL_SH.read_text(encoding="utf-8")


def test_pull_has_bounded_retry_loop():
    src = _src()
    assert "max_attempts=3" in src, "pull.sh must cap the number of pull attempts"
    assert 'while [ "$attempt" -le "$max_attempts" ]' in src, "missing bounded retry loop"
    assert "/api/pull" in src
    assert "sleep" in src, "retries should back off between attempts"


def test_pull_retry_checks_both_exit_code_and_error_body():
    src = _src()
    # /api/pull can report failure in the NDJSON body with HTTP 200, so a
    # success must require BOTH a clean exit code AND no "error" line.
    assert '"$curl_exit_code" -eq 0' in src
    assert "grep -q '\"error\"'" in src
    assert "pulled=1" in src


def test_pull_failure_is_non_fatal_after_retries():
    src = _src()
    assert "after $max_attempts attempts" in src, "terminal ERROR must follow the retries"
    # The per-model failure branch logs and continues — it must NOT abort the
    # whole pull set, and the script always reaches its completion line.
    fail_branch = src.split('if [ "$pulled" -ne 1 ]; then', 1)[1].split("fi", 1)[0]
    assert "exit" not in fail_branch, "a failed pull must not exit the script"
    assert "Finished model pulling process" in src


@pytest.mark.parametrize("stall_timeout", ["7", "13"])
def test_stalled_pull_attempt_is_bounded_and_retried(tmp_path, stall_timeout):
    attempts = tmp_path / "attempts"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "apk").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "curl").write_text(
        "#!/bin/sh\n"
        "case \" $* \" in\n"
        "  *\"/api/pull\"*)\n"
        f"    printf 'attempt\\n' >> {str(attempts)!r}\n"
        "    case \" $* \" in\n"
        f"      *\" --connect-timeout 20 --speed-time {stall_timeout} "
        "--speed-limit 1024 \"*) exit 28 ;;\n"
        "      *) /bin/sleep 30 ;;\n"
        "    esac ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    for executable in fake_bin.iterdir():
        executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "OLLAMA_HOST_URL": "http://ollama:11434",
        "OLLAMA_USER_MODELS": "example:latest",
        "OLLAMA_CUSTOM_MODELS": "",
        "OLLAMA_PULL_STALL_TIMEOUT_SECONDS": stall_timeout,
    }

    completed = run_with_deadline(
        ["sh", str(PULL_SH)],
        env=env,
        timeout_seconds=10,
    )

    assert completed.returncode == 0
    assert attempts.read_text(encoding="utf-8").splitlines() == [
        "attempt", "attempt", "attempt"
    ]
    assert "after 3 attempts" in completed.stderr


def test_progressing_large_pull_has_no_artificial_wall_clock_cutoff():
    src = PULL_SH.read_text(encoding="utf-8")

    pull_command = src.split("curl_output=$(curl", 1)[1].split("2>&1)", 1)[0]
    assert "--connect-timeout" in pull_command
    assert '--speed-time "$pull_stall_timeout"' in pull_command
    assert "--speed-limit" in pull_command
    assert "--max-time" not in pull_command
