"""Fail-closed upgrade contracts for PostgreSQL host authentication."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]
HBA_GUARD = REPO / "services/supabase/db/scripts/enforce-scram-host-auth.sh"


def _run_guard(tmp_path: Path, hba: str):
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    (pgdata / "PG_VERSION").write_text("17\n", encoding="utf-8")
    hba_path = pgdata / "pg_hba.conf"
    hba_path.write_text(hba, encoding="utf-8")
    result = subprocess.run(
        ["/bin/sh", str(HBA_GUARD)],
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
