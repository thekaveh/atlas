"""Every PostgREST-exposed table must be gated.

Static scan of the seed slices — no docker needed, so it runs in CI on every
PR. The exposure chain that makes this load-bearing:

  * `06-permissions.sql` grants `anon` SELECT on all of `public` and sets the
    same as a DEFAULT PRIVILEGE, and the pinned supabase/postgres image ships
    default privileges granting anon full `arwdDxtm`.
  * `services/supabase/compose.yml` sets `PGRST_DB_SCHEMA: "public,storage"`
    and `PGRST_DB_ANON_ROLE: anon`.
  * `.env.example` ships a loopback `HOST_BIND_IP` default. An operator can
    deliberately publish PostgREST remotely with a non-empty override.

So for `public`, RLS remains the database authorization boundary for every
PostgREST peer, including a deliberately remote publication. Two slices shipped without it: `12-comfyui.sql` (user prompts
and output paths — readable AND deletable) and `03b-gotrue-migration-sync.sql`
(one DELETE returns GoTrue to the PostgreSQL-17 crash-loop the slice exists to
prevent). Both were confirmed against a running stack before being fixed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2] / "services" / "supabase" / "db" / "scripts"
)
SCOPED_ROLES = SCRIPTS_DIR / "05-scoped-roles.sh"

# Internal coordination tables are intentionally invisible to every PostgREST
# JWT role.  A dedicated database login may receive a command-scoped policy in
# 05-scoped-roles.sh; these tables must not acquire a public/client policy here.
DENY_BY_DEFAULT_PUBLIC_TABLES = {"memory_embedding_schema_state"}

#: `public.` is OPTIONAL — an unqualified `CREATE TABLE secrets_vault (...)`
#: lands in `public` too (it is first on the default search_path) and was
#: invisible to a pattern that required the prefix. The `(?!\s*\.)` keeps a
#: table in ANOTHER schema out: without it, `CREATE TABLE storage.buckets`
#: captured `storage` as if it were a public table name.
_CREATE_PUBLIC_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?(\w+)(?!\s*\.)\s*\(",
    re.IGNORECASE,
)

#: `--` line comments, stripped before matching so prose describing a
#: `CREATE TABLE` is not mistaken for one.
_SQL_LINE_COMMENT = re.compile(r"--[^\n]*")
_ENABLE_RLS = re.compile(
    r"ALTER\s+TABLE\s+public\.(\w+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.IGNORECASE
)
_DROPPED = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?public\.(\w+)", re.IGNORECASE
)


def _all_sql() -> str:
    joined = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(SCRIPTS_DIR.glob("*.sql"))
    )
    return _SQL_LINE_COMMENT.sub("", joined)


def test_the_scan_finds_the_tables():
    """A guard that matches nothing is worse than no guard."""
    assert SCRIPTS_DIR.is_dir(), SCRIPTS_DIR
    created = set(_CREATE_PUBLIC_TABLE.findall(_all_sql()))
    assert len(created) >= 8, f"only matched {created} — check the pattern"


def test_every_public_table_enables_row_level_security():
    sql = _all_sql()
    created = set(_CREATE_PUBLIC_TABLE.findall(sql))
    dropped = set(_DROPPED.findall(sql))
    guarded = set(_ENABLE_RLS.findall(sql))

    ungated = sorted((created - dropped) - guarded)
    assert not ungated, (
        "these public tables are exposed through PostgREST with no RLS, so an "
        "unauthenticated peer can read (and with the image's default grants, "
        f"write) them: {ungated}"
    )


def test_no_public_table_is_created_without_a_policy():
    """Client-facing tables have policies; internal tables default-deny."""
    sql = _all_sql()
    for table in sorted(set(_ENABLE_RLS.findall(sql))):
        if table in DENY_BY_DEFAULT_PUBLIC_TABLES:
            assert not re.search(
                rf"CREATE\s+POLICY[^;]+ON\s+public\.{table}\b",
                sql,
                re.IGNORECASE,
            ), f"public.{table} must not expose a PostgREST client policy"
            continue
        assert re.search(
            rf"CREATE\s+POLICY[^;]+ON\s+public\.{table}\b", sql, re.IGNORECASE
        ), f"public.{table} enables RLS but declares no policy"


def test_memory_schema_state_is_private_but_backend_can_read_it():
    """The internal state is not a client API, while Backend needs SELECT."""
    memory_sql = (SCRIPTS_DIR / "14-backend-memory.sql").read_text(encoding="utf-8")
    scoped_roles = SCOPED_ROLES.read_text(encoding="utf-8")

    assert (
        "ALTER TABLE public.memory_embedding_schema_state ENABLE ROW LEVEL SECURITY;"
        in memory_sql
    )
    assert re.search(
        r"REVOKE\s+ALL\s+ON\s+public\.memory_embedding_schema_state\s+"
        r"FROM\s+anon,\s*authenticated,\s*service_role;",
        memory_sql,
        re.IGNORECASE,
    )
    assert "Atlas backend schema-state read" in scoped_roles
    assert (
        "CREATE POLICY %I ON public.memory_embedding_schema_state "
        "FOR SELECT TO %I USING (true)"
        in scoped_roles
    )
    assert not re.search(
        r"CREATE\s+POLICY[^;]+ON\s+public\.memory_embedding_schema_state\s+"
        r"FOR\s+ALL",
        scoped_roles,
        re.IGNORECASE,
    )


@pytest.mark.parametrize("table", ["objects", "buckets"])
def test_storage_tables_are_not_granted_to_anon(table):
    """`storage` has RLS DISABLED, so the GRANT is the only control.

    `04-storage.sql` disables RLS on these deliberately ("managing access
    through GRANTs instead"), and `storage` is in PGRST_DB_SCHEMA — so a grant
    to `anon` made every object path, owner and bucket row readable by any
    unauthenticated peer.
    """
    sql = _all_sql()
    # `ON TABLE storage.x` and `ON storage.x` are both valid; the original
    # required the second spelling exactly, so `GRANT SELECT ON TABLE
    # storage.objects TO anon;` slipped past.
    for match in re.finditer(
        rf"GRANT\s+[^;]*?\s+ON\s+(?:TABLE\s+)?storage\.{table}\s+TO\s+([^;]+);",
        sql,
        re.IGNORECASE,
    ):
        assert "anon" not in match.group(1), (
            f"storage.{table} is granted to anon: {match.group(0).strip()}"
        )
    for match in re.finditer(
        r"GRANT\s+[^;]*?\s+ON\s+ALL\s+TABLES\s+IN\s+SCHEMA\s+storage\s+TO\s+([^;]+);",
        sql,
        re.IGNORECASE,
    ):
        assert "anon" not in match.group(1), (
            f"schema-wide storage grant includes anon: {match.group(0).strip()}"
        )
    # ...and the DEFAULT PRIVILEGE, which grants anon on every FUTURE table
    # and was not checked at all.
    for match in re.finditer(
        r"ALTER\s+DEFAULT\s+PRIVILEGES[^;]*?IN\s+SCHEMA\s+storage\s+GRANT[^;]*?TO\s+([^;]+);",
        sql,
        re.IGNORECASE,
    ):
        assert "anon" not in match.group(1), (
            f"storage default privilege includes anon: {match.group(0).strip()}"
        )


def test_the_user_backfill_does_not_overwrite_a_renamed_profile():
    """The slice re-runs on every `docker compose up`.

    `ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name` therefore reverted
    every profile rename on the next restart — undoing the write the "Users
    can update own profile" policy in the same file explicitly permits.
    """
    users_sql = (SCRIPTS_DIR / "10-users.sql").read_text(encoding="utf-8")
    assert "ON CONFLICT (id) DO NOTHING" in users_sql
    assert "DO UPDATE\nSET name" not in users_sql
