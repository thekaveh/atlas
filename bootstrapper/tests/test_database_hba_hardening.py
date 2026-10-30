"""Fail-closed upgrade contracts for PostgreSQL host authentication."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]
HBA_GUARD = REPO / "services/supabase/db/scripts/enforce-scram-host-auth.sh"


def _run_guard(tmp_path: Path, hba: str, *args: str):
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    (pgdata / "PG_VERSION").write_text("17\n", encoding="utf-8")
    hba_path = pgdata / "pg_hba.conf"
    hba_path.write_text(hba, encoding="utf-8")
    result = subprocess.run(
        ["/bin/sh", str(HBA_GUARD), *args],
        env={
            **os.environ,
            "PGDATA": str(pgdata),
            "ATLAS_POSTGRES_ENTRYPOINT": "/usr/bin/true",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return result, hba_path


def _active_rules(text: str) -> str:
    """pg_hba rules with comment lines removed (a commented-out weak rule is fine)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_hba_upgrade_preserves_non_host_rules_options_and_address_families(
    tmp_path: Path,
) -> None:
    original = """# Atlas review matrix
local all all trust
host all all 127.0.0.1/32 trust
hostssl all all ::1/128 password clientcert=verify-full
host all all 10.0.0.0/8 scram-sha-256 map=atlas
host all all 0.0.0.0/0 reject
"""
    result, hba = _run_guard(tmp_path, original)
    assert result.returncode == 0, result.stderr
    rewritten = hba.read_text(encoding="utf-8")
    assert "local all all trust" in rewritten
    assert "127.0.0.1/32 scram-sha-256" in rewritten
    assert "::1/128 scram-sha-256 clientcert=verify-full" in rewritten
    assert "10.0.0.0/8 scram-sha-256 map=atlas" in rewritten
    assert "0.0.0.0/0 reject" in rewritten
    # pg_hba takes the first match: the weak host rules must be gone, not
    # merely followed by scram ones.
    active = _active_rules(rewritten)
    assert "127.0.0.1/32 trust" not in active
    assert "::1/128 password" not in active
    assert hba.with_name("pg_hba.conf.atlas.bak").read_text(encoding="utf-8") == original


@pytest.mark.parametrize("directive", ("include", "include_if_exists", "include_dir"))
def test_hba_include_directives_are_rejected_before_modification(
    tmp_path: Path, directive: str
) -> None:
    original = f"{directive} 'hba.d'\nhost all all 127.0.0.1/32 trust\n"
    result, hba = _run_guard(tmp_path, original)
    assert result.returncode != 0
    assert "include" in result.stderr.lower()
    assert hba.read_text(encoding="utf-8") == original


def test_hba_md5_rules_block_safely_for_verifier_compatibility(tmp_path: Path) -> None:
    original = "host all all 127.0.0.1/32 md5 clientcert=verify-ca\n"
    result, hba = _run_guard(tmp_path, original)
    assert result.returncode != 0
    assert "md5" in result.stderr.lower()
    assert "verifier" in result.stderr.lower()
    assert hba.read_text(encoding="utf-8") == original


def test_hba_md5_rules_do_not_block_when_the_data_directory_file_is_unused(
    tmp_path: Path,
) -> None:
    """Under `-D /etc/postgresql` the server reads the image's hba_file; a
    refusal over the inert data-directory file only blocked startup."""
    original = "host all all 127.0.0.1/32 md5\n"
    result, hba = _run_guard(tmp_path, original, "-D", "/etc/postgresql")
    assert result.returncode == 0, result.stderr
    assert "leaving the unused data-directory pg_hba.conf" in result.stderr
    assert hba.read_text(encoding="utf-8") == original
    assert not list((tmp_path / "pgdata").glob(".pg_hba.conf.atlas.*"))


def test_hba_malformed_candidate_keeps_original_and_recovery_backup(
    tmp_path: Path,
) -> None:
    original = "host all all 127.0.0.1/32\n"
    result, hba = _run_guard(tmp_path, original)
    assert result.returncode != 0
    assert "invalid" in result.stderr.lower()
    assert hba.read_text(encoding="utf-8") == original
    assert hba.with_name("pg_hba.conf.atlas.bak").read_text(encoding="utf-8") == original
def test_hba_guard_leaves_no_temporary_candidate_in_pgdata(tmp_path: Path) -> None:
    """The no-rewrite path must not leak its candidate into PGDATA.

    $candidate is created inside PGDATA and is only consumed by the `mv` that
    runs when the rewritten file actually differs. When the rules are already
    scram-sha-256 the compare matches, the `mv` is skipped, and clearing the
    cleanup trap before removing the candidate strands it in the data
    directory -- one file per container start, since the name carries $$.
    """
    already_hardened = "host all all 127.0.0.1/32 scram-sha-256\n"

    result, hba_path = _run_guard(tmp_path, already_hardened)

    assert result.returncode == 0, result.stderr
    assert hba_path.read_text(encoding="utf-8") == already_hardened
    leftovers = sorted(child.name for child in hba_path.parent.glob(".pg_hba.conf.atlas.*"))
    assert leftovers == [], f"temporary candidate stranded in PGDATA: {leftovers}"


def test_hba_recovery_copy_survives_a_second_start(tmp_path: Path) -> None:
    """The backup was rewritten on every start, so the second start replaced
    the pre-upgrade rules with the already converted file."""
    original = "host all all 127.0.0.1/32 trust\n"
    result, hba = _run_guard(tmp_path, original)
    assert result.returncode == 0, result.stderr
    second = subprocess.run(
        ["/bin/sh", str(HBA_GUARD)],
        env={**os.environ, "PGDATA": str(hba.parent), "ATLAS_POSTGRES_ENTRYPOINT": "/usr/bin/true"},
        text=True, capture_output=True, check=False, timeout=10,
    )
    assert second.returncode == 0, second.stderr
    assert hba.with_name("pg_hba.conf.atlas.bak").read_text(encoding="utf-8") == original


def _write_hba_signal_commands(commands):
    import sys
    sender = commands / "awk"
    sender.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, sys\nfrom pathlib import Path\n"
        "counter = Path(os.environ['OWNED_AWK_COUNTER'])\n"
        "count = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(count))\n"
        "if count == int(os.environ['OWNED_INTERRUPT_AT']):\n"
        "    name = os.environ['OWNED_SIGNAL']\n"
        "    if name == 'command-error':\n        sys.exit(42)\n"
        "    pid = int(Path(os.environ['OWNED_PID_FILE']).read_text())\n"
        "    assert os.getppid() == pid\n"
        "    Path(os.environ['OWNED_SIGNAL_MARKER']).write_text(name)\n"
        "    os.kill(pid, getattr(signal, 'SIG' + name))\n"
        "os.execv('/usr/bin/awk', ['/usr/bin/awk', *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    sender.chmod(0o700)
    entry = commands / "entry"
    entry.write_text('#!/bin/sh\nprintf reached > "$OWNED_ENTRY_MARKER"\n', encoding="utf-8")
    entry.chmod(0o700)


def _run_owned_hba_guard(env):
    import signal

    process = subprocess.Popen(
        ["/bin/sh", "-c", 'printf "%s" "$$" > "$OWNED_PID_FILE"; exec /bin/sh "$OWNED_GUARD"'],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=10)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


@pytest.fixture
def interrupted_hba_guard(tmp_path):
    """Run the complete guard against private files and an owned signal sender."""
    commands = tmp_path / "commands"
    commands.mkdir()
    _write_hba_signal_commands(commands)
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    original = "host all all 127.0.0.1/32 trust\n"
    hba = pgdata / "pg_hba.conf"
    hba.write_text(original, encoding="utf-8")
    hba.chmod(0o640)
    (pgdata / "PG_VERSION").write_text("17\n", encoding="utf-8")

    def run(name, interrupt_at):
        env = {
            "PATH": str(commands) + ":/usr/bin:/bin", "HOME": str(tmp_path),
            "PGDATA": str(pgdata), "ATLAS_POSTGRES_ENTRYPOINT": str(commands / "entry"),
            "OWNED_SIGNAL": name, "OWNED_INTERRUPT_AT": str(interrupt_at),
            "OWNED_AWK_COUNTER": str(tmp_path / "counter"),
            "OWNED_PID_FILE": str(tmp_path / "pid"),
            "OWNED_SIGNAL_MARKER": str(tmp_path / "signal"),
            "OWNED_ENTRY_MARKER": str(tmp_path / "entry"),
            "OWNED_GUARD": str(HBA_GUARD),
        }
        return _run_owned_hba_guard(env)

    return run, hba, original


def _assert_interrupted_hba_files(hba, original, interrupt_at):
    assert hba.read_text(encoding="utf-8") == original
    assert hba.stat().st_mode & 0o777 == 0o640
    leftovers = [p.name for p in hba.parent.iterdir()
                 if p.name.startswith((".pg_hba.conf.atlas.", "pg_hba.conf.atlas.bak."))]
    assert leftovers == []
    backup = hba.with_name("pg_hba.conf.atlas.bak")
    assert backup.exists() == (interrupt_at == 4)
    if backup.exists():
        assert backup.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("signal_case", [("HUP", 129), ("INT", 130), ("TERM", 143)])
@pytest.mark.parametrize("interrupt_at", [1, 4], ids=["preflight", "candidate-validation"])
def test_hba_guard_signals_stop_before_database_entry_and_clear_temporaries(
    tmp_path, interrupted_hba_guard, signal_case, interrupt_at,
):
    name, status = signal_case
    run, hba, original = interrupted_hba_guard
    result = run(name, interrupt_at)
    assert (tmp_path / "signal").read_text(encoding="utf-8") == name
    assert result.returncode == status, result.stderr
    assert not (tmp_path / "entry").exists()
    _assert_interrupted_hba_files(hba, original, interrupt_at)


def test_hba_guard_command_failure_keeps_its_status_and_cleans_candidates(interrupted_hba_guard, tmp_path):
    run, hba, original = interrupted_hba_guard
    result = run("command-error", 3)
    assert result.returncode == 42
    assert not (tmp_path / "signal").exists() and not (tmp_path / "entry").exists()
    assert hba.read_text(encoding="utf-8") == original
    assert not list(hba.parent.glob(".pg_hba.conf.atlas.*"))


def test_hba_guard_ordinary_success_still_reaches_entry_with_same_bytes(interrupted_hba_guard, tmp_path):
    run, hba, original = interrupted_hba_guard
    result = run("ordinary", 999)
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout == "supabase-db: upgraded host authentication rules to scram-sha-256\n"
    assert (tmp_path / "entry").read_text(encoding="utf-8") == "reached"
    assert not (tmp_path / "signal").exists()
    assert hba.read_text(encoding="utf-8") == "host all all 127.0.0.1/32 scram-sha-256\n"
    assert hba.stat().st_mode & 0o777 == 0o640
    assert hba.with_name("pg_hba.conf.atlas.bak").read_text(encoding="utf-8") == original
    assert not list(hba.parent.glob(".pg_hba.conf.atlas.*"))
