"""Docker-gated guards that the seed scripts produce a stable schema.

These lock current behavior (Task 1) and must stay green across the Part A
partition (Task 3): same golden, regardless of how the *.sql files are split.
"""
from __future__ import annotations

import pytest

from tests import seed_harness

pytestmark = pytest.mark.skipif(
    seed_harness.docker_unavailable_locally(), reason="docker not on PATH"
)


def test_schema_matches_golden():
    schema, _ = seed_harness.run_scripts_and_dump(seed_harness.SCRIPTS_DIR)
    assert schema == seed_harness.SCHEMA_GOLDEN.read_text(encoding="utf-8")


def test_seed_rows_match_golden():
    _, rows = seed_harness.run_scripts_and_dump(seed_harness.SCRIPTS_DIR)
    assert rows == seed_harness.ROWS_GOLDEN.read_text(encoding="utf-8")


def test_sorted_run_succeeds():
    # A non-zero exit (e.g. an FK reference to a not-yet-created table) raises
    # CalledProcessError inside the harness; reaching here means every script
    # ran cleanly in alphabetical order.
    schema, _ = seed_harness.run_scripts_and_dump(seed_harness.SCRIPTS_DIR)
    assert "CREATE TABLE" in schema


def test_double_run_is_idempotent():
    once, _ = seed_harness.run_scripts_and_dump(seed_harness.SCRIPTS_DIR)
    twice, _ = seed_harness.run_scripts_and_dump(
        seed_harness.SCRIPTS_DIR, run_twice=True
    )
    assert twice == once
