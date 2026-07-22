"""#800: the legacy `memory_*.user_id` VARCHAR(255) → UUID migration in
`14-backend-memory.sql` must not abort DB init when a pre-existing volume holds
a non-UUID `user_id`. The unguarded `USING user_id::uuid` raised
`invalid input syntax for type uuid`, killing the whole DO block and failing
init on every `docker compose up`. The fix wraps the per-table migration in a
nested BEGIN/EXCEPTION so a bad cast rolls back that table's changes and raises
a WARNING instead.

Docker-gated (boots the pinned supabase/postgres image); SKIPs without docker.
Faithful reproduction: run the real seed scripts (fresh), revert one table to
the legacy shape, seed a value, then re-run the real script 14 and assert init
still succeeds.
"""
from __future__ import annotations

import subprocess
import time
import uuid

import pytest

from tests import seed_harness

pytestmark = pytest.mark.skipif(
    not seed_harness.docker_available(), reason="docker not on PATH"
)

SCRIPT_14 = seed_harness.SCRIPTS_DIR / "14-backend-memory.sql"


def _psql(name: str, sql: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", "-i", name, "psql", "-h", "127.0.0.1",
         "-v", "ON_ERROR_STOP=1", "-U", seed_harness.DB_USER,
         "-d", seed_harness.DB_NAME, "-f", "-"],
        input=sql, text=True, capture_output=True,
    )


def _run_script_14(name: str) -> subprocess.CompletedProcess:
    with SCRIPT_14.open("rb") as fh:
        return subprocess.run(
            ["docker", "exec", "-i", name, "psql", "-h", "127.0.0.1",
             "-v", "ON_ERROR_STOP=1", "-U", seed_harness.DB_USER,
             "-d", seed_harness.DB_NAME, "-f", "-"],
            stdin=fh, text=True, capture_output=True,
        )


def _col_type(name: str) -> str:
    r = subprocess.run(
        ["docker", "exec", name, "psql", "-h", "127.0.0.1", "-A", "-t",
         "-U", seed_harness.DB_USER, "-d", seed_harness.DB_NAME, "-c",
         "SELECT data_type FROM information_schema.columns WHERE "
         "table_schema='public' AND table_name='memory_facts' AND "
         "column_name='user_id'"],
        text=True, capture_output=True, check=True,
    )
    return r.stdout.strip()


def _boot_and_init():
    """Boot the pinned image and apply every seed script (fresh full init)."""
    name = f"atlas-legacy-uidtest-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "run", "-d", "--name", name,
         "-e", f"POSTGRES_USER={seed_harness.DB_USER}",
         "-e", "POSTGRES_PASSWORD=postgres",
         "-e", f"POSTGRES_DB={seed_harness.DB_NAME}",
         "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
         seed_harness.DB_IMAGE],
        check=True, capture_output=True,
    )
    for _ in range(180):
        if subprocess.run(
            ["docker", "exec", name, "pg_isready", "-h", "127.0.0.1",
             "-U", seed_harness.DB_USER, "-d", seed_harness.DB_NAME, "-q"]
        ).returncode == 0:
            break
        time.sleep(1)
    else:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        raise RuntimeError("postgres did not become ready in 180s")
    for sql in sorted(seed_harness.SCRIPTS_DIR.glob("*.sql")):
        with sql.open("rb") as fh:
            subprocess.run(
                ["docker", "exec", "-i", name, "psql", "-h", "127.0.0.1",
                 "-v", "ON_ERROR_STOP=1", "-U", seed_harness.DB_USER,
                 "-d", seed_harness.DB_NAME, "-f", "-"],
                stdin=fh, check=True, capture_output=True,
            )
    return name


# Revert memory_facts to the legacy VARCHAR(255) user_id shape (drop the uuid
# FK, change the type back), then seed a row — mimicking a pre-migration volume.
_TO_LEGACY = """
ALTER TABLE public.memory_facts DROP CONSTRAINT IF EXISTS memory_facts_user_id_fkey;
ALTER TABLE public.memory_facts ALTER COLUMN user_id TYPE varchar(255);
"""


def test_legacy_non_uuid_user_id_does_not_abort_init():
    name = _boot_and_init()
    try:
        # Legacy shape + a NON-UUID value → the pre-#800 abort trigger.
        assert _psql(name, _TO_LEGACY).returncode == 0
        assert _psql(
            name,
            "INSERT INTO public.memory_facts (user_id, content) "
            "VALUES ('not-a-uuid', 'legacy row');",
        ).returncode == 0
        assert _col_type(name) == "character varying"

        proc = _run_script_14(name)

        # The core AC: re-running the migration must NOT abort (exit 0).
        assert proc.returncode == 0, (
            "script 14 aborted on a non-UUID legacy user_id:\n" + proc.stderr
        )
        # Graceful degradation: the bad-valued table is left in its legacy shape,
        # with a WARNING — not migrated, not init-breaking.
        assert _col_type(name) == "character varying"
        assert "skipped" in proc.stderr.lower() or "WARNING" in proc.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def test_legacy_valid_uuid_user_id_still_migrates():
    """The guard must not break the happy path: a legacy VARCHAR user_id holding
    a valid UUID string still converts to uuid."""
    name = _boot_and_init()
    try:
        assert _psql(name, _TO_LEGACY).returncode == 0
        good = str(uuid.UUID(int=0x1234567890abcdef1234567890abcdef))
        assert _psql(
            name,
            f"INSERT INTO public.users (id, name) VALUES ('{good}', 'test user') "
            "ON CONFLICT DO NOTHING;",
        ).returncode == 0
        assert _psql(
            name,
            f"INSERT INTO public.memory_facts (user_id, content) "
            f"VALUES ('{good}', 'valid row');",
        ).returncode == 0

        proc = _run_script_14(name)

        assert proc.returncode == 0, proc.stderr
        assert _col_type(name) == "uuid"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
