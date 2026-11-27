"""Supabase Realtime's database grants: its tracker in public, nothing else.

The static checks read the scoped-roles script. The live check runs it against
the disposable supabase/postgres drill database, first restoring the bulk
SELECT that earlier installs hold, then probing what the scoped Realtime login
can read. The live check skips locally without docker or the pinned image, and
fails in CI, as the other role drills do.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_database_role_boundaries import (
    TEST_SECRETS,
    DisposablePostgres,
    disposable_postgres,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_SCRIPT = REPO_ROOT / "services/supabase/db/scripts/05-scoped-roles.sh"
# Realtime's application-repository tracker is the only public table it names.
REALTIME_PUBLIC_TABLES = {"public.schema_migrations"}
REALTIME_LOGIN = TEST_SECRETS["SUPABASE_REALTIME_DB_USER"]
REALTIME_PASSWORD = TEST_SECRETS["SUPABASE_REALTIME_DB_PASSWORD"]
UNLISTED_TABLE = "realtime_drill_unlisted"
# Tables outside Realtime's ownership that the Realtime login reads by a direct
# grant. PUBLIC-wide grants (for example PostGIS metadata) are excluded.
READABLE_PUBLIC_TABLES_SQL = (
    "SELECT coalesce(string_agg(c.relname, ',' ORDER BY c.relname), '') "
    "FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a "
    "WHERE n.nspname = 'public' "
    "AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
    "AND a.grantee = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'atlas_realtime') "
    "AND a.privilege_type = 'SELECT' "
    "AND a.grantee <> c.relowner"
)
SQL_COMMENT_LINE = re.compile(r"^\s*(?:--|#).*$", re.MULTILINE)


def _statements(script: str) -> list[str]:
    text = SQL_COMMENT_LINE.sub("", script)
    return [part.strip() for part in text.split(";") if part.strip()]


def _realtime_grants(script: str) -> list[str]:
    return [
        statement
        for statement in _statements(script)
        if re.match(r"GRANT\b", statement, re.IGNORECASE)
        and re.search(r"\bTO\b.*realtime_role", statement, re.IGNORECASE | re.DOTALL)
    ]


def _realtime_public_objects(script: str) -> set[str]:
    named: set[str] = set()
    for statement in _realtime_grants(script):
        target = re.search(
            r"\bON\s+(?:TABLE\s+)?(?P<objects>.+?)\s+TO\b",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if target is None:
            continue
        for item in target.group("objects").split(","):
            if item.strip().startswith("public."):
                named.add(item.strip())
    return named


def test_realtime_role_has_no_all_tables_grant() -> None:
    script = ROLE_SCRIPT.read_text(encoding="utf-8")
    bulk = [
        statement
        for statement in _realtime_grants(script)
        if re.search(r"\bON\s+ALL\s+TABLES\b", statement, re.IGNORECASE)
    ]
    assert bulk == []


def test_realtime_role_names_only_its_tracker_in_public() -> None:
    script = ROLE_SCRIPT.read_text(encoding="utf-8")
    assert _realtime_public_objects(script) == REALTIME_PUBLIC_TABLES


def test_scoped_realtime_login_reads_only_its_tracker_in_public(
    disposable_postgres: DisposablePostgres,
) -> None:
    admin = disposable_postgres.admin_password
    disposable_postgres.sql(
        f"CREATE TABLE public.{UNLISTED_TABLE}(value text); "
        f"INSERT INTO public.{UNLISTED_TABLE} VALUES ('private')",
        password=admin,
    )
    # Installs that ran the former script hold the bulk SELECT in their ACLs.
    # A re-run has to take it back, so recreate that state before running it.
    disposable_postgres.sql(
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO atlas_realtime",
        password=admin,
    )
    disposable_postgres.run_init()

    readable = disposable_postgres.sql(
        READABLE_PUBLIC_TABLES_SQL, password=admin,
    ).stdout.strip()
    assert readable == "schema_migrations"

    denied = disposable_postgres.network_sql(
        f"SELECT count(*) FROM public.{UNLISTED_TABLE}",
        user=REALTIME_LOGIN, password=REALTIME_PASSWORD, check=False,
    )
    assert denied.returncode != 0
    assert f"permission denied for table {UNLISTED_TABLE}" in denied.stderr

    tracker = disposable_postgres.network_sql(
        "SELECT count(*) FROM public.schema_migrations",
        user=REALTIME_LOGIN, password=REALTIME_PASSWORD,
    )
    assert tracker.stdout.strip().isdigit()
