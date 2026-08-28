"""Static (no-DB) lints for the per-service seed partition (Part A).

Enforces: each app table's CREATE lives in exactly one owned slice; every
slice carries an OWNER banner; every slice object is idempotently guarded;
the full set of app tables is present across the slices.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "services" / "supabase" / "db" / "scripts"

# Expected owning slice for each app table.
EXPECTED_OWNER = {
    "users": "10-users.sql",
    "comfyui_workflows": "12-comfyui.sql",
    "comfyui_generations": "12-comfyui.sql",
    "research_sessions": "13-backend-research.sql",
    "research_results": "13-backend-research.sql",
    "research_sources": "13-backend-research.sql",
    "research_logs": "13-backend-research.sql",
    "memory_facts": "14-backend-memory.sql",
    "memory_sessions": "14-backend-memory.sql",
    "memory_consolidation_log": "14-backend-memory.sql",
    "media_spend_ledger": "17-backend-media-ledger.sql",
}
SLICE_FILES = sorted({v for v in EXPECTED_OWNER.values()})

_CREATE_TABLE = re.compile(
    r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+public\.(\w+)", re.IGNORECASE
)


def _read(name: str) -> str:
    path = SCRIPTS_DIR / name
    if not path.exists():
        raise AssertionError(f"{name} does not exist yet — Task 3 creates it")
    return path.read_text(encoding="utf-8")


def test_each_app_table_created_in_exactly_one_slice():
    location: dict[str, list[str]] = {}
    for sql in SCRIPTS_DIR.glob("*.sql"):
        for table in _CREATE_TABLE.findall(sql.read_text(encoding="utf-8")):
            location.setdefault(table, []).append(sql.name)
    for table, owner in EXPECTED_OWNER.items():
        assert location.get(table) == [owner], (
            f"public.{table} should be created only in {owner}, "
            f"found in {location.get(table)}"
        )


def test_every_slice_has_owner_banner():
    for name in SLICE_FILES:
        first = _read(name).splitlines()[0:3]
        assert any(line.startswith("-- OWNER:") for line in first), (
            f"{name} missing '-- OWNER:' banner in its first lines"
        )


def test_slice_tables_are_guarded():
    unguarded_table = re.compile(
        r"CREATE TABLE(?!\s+IF NOT EXISTS)\s+public\.", re.IGNORECASE
    )
    for name in SLICE_FILES:
        text = _read(name)
        assert not unguarded_table.search(text), (
            f"{name} has a CREATE TABLE without IF NOT EXISTS"
        )
        assert not re.search(r"ADD COLUMN\s+(?!IF NOT EXISTS)", text, re.IGNORECASE), (
            f"{name} has an ADD COLUMN without IF NOT EXISTS"
        )
        assert not re.search(r"CREATE INDEX\s+(?!IF NOT EXISTS)", text, re.IGNORECASE), (
            f"{name} has a CREATE INDEX without IF NOT EXISTS"
        )


def test_all_expected_tables_present():
    found = set()
    for name in SLICE_FILES:
        found.update(_CREATE_TABLE.findall(_read(name)))
    missing = set(EXPECTED_OWNER) - found
    assert not missing, f"app tables missing from slices: {sorted(missing)}"


def test_auth_users_are_synchronized_to_public_users():
    users_slice = _read("10-users.sql")
    assert "INSERT INTO public.users" in users_slice
    assert "FROM auth.users" in users_slice
    assert "CREATE OR REPLACE FUNCTION public.handle_auth_user_sync" in users_slice
    assert "CREATE TRIGGER on_auth_user_sync" in users_slice
    assert "IF TG_OP = 'DELETE'" in users_slice
    assert "AFTER INSERT OR DELETE OR UPDATE" in users_slice
    assert "REVOKE ALL ON FUNCTION public.handle_auth_user_sync()" in users_slice
    assert "ALTER TABLE public.users ENABLE ROW LEVEL SECURITY" in users_slice
    assert "auth.uid() = id" in users_slice
    assert "auth.role() = 'service_role'" in users_slice


def test_media_ledger_enforces_status_and_cost_invariants():
    media_slice = _read("17-backend-media-ledger.sql")
    for constraint in (
        "media_spend_ledger_status_check",
        "media_spend_ledger_estimated_cost_check",
        "media_spend_ledger_final_cost_check",
    ):
        assert constraint in media_slice
    assert "status IN ('reserved', 'submitted', 'committed', 'released', 'denied')" in media_slice
    assert "estimated_cost_usd IS NULL OR estimated_cost_usd >= 0" in media_slice
    assert "final_cost_usd IS NULL OR final_cost_usd >= 0" in media_slice


def test_old_mixed_files_are_gone():
    for stale in ("05-public-tables.sql", "05a-public-tables-migrations.sql",
                  "08-seed-data.sql", "09-research-tables.sql",
                  "10-langmem-tables.sql", "10a-langmem-migrations.sql",
                  "12-extend-comfyui-models.sql"):
        assert not (SCRIPTS_DIR / stale).exists(), f"{stale} should be removed"
