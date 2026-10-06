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


#: Shell comment lines, stripped from the `.sh` slices so prose is not parsed.
_SHELL_LINE_COMMENT = re.compile(r"^[ \t]*#[^\n]*", re.MULTILINE)
#: Searched, not anchored: a statement can share its `;`-chunk with a
#: preceding `DO $$ BEGIN`, `THEN`, or a shell `<<'SQL'` heredoc opener.
_GRANT_STATEMENT = re.compile(
    r"(?:ALTER\s+DEFAULT\s+PRIVILEGES\b(?P<defaults>.*?))?"
    r"\bGRANT\b(?P<privileges>.*?)\bON\b(?P<target>.*?)\bTO\b(?P<grantees>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_ALL_TABLES_IN_SCHEMA = re.compile(r"^ALL\s+TABLES\s+IN\s+SCHEMA\b(.*)$", re.IGNORECASE)
_DEFAULTS_IN_SCHEMA = re.compile(r"\bIN\s+SCHEMA\b(.*)$", re.IGNORECASE | re.DOTALL)
_GRANTEE_TAIL = re.compile(r"\b(?:WITH\s+GRANT\s+OPTION|GRANTED\s+BY)\b.*$", re.IGNORECASE)


def _identifiers(clause: str) -> list[str]:
    """`a, "B", :"c"` -> `["a", "b", "c"]`; psql `:"var"` and quotes stripped."""
    return [
        part.strip().lstrip(":").replace('"', "").strip().lower()
        for part in clause.split(",")
        if part.strip()
    ]


def _grant_and_seed_sql() -> str:
    """Every seed slice that can issue a GRANT: the `.sql` files and the `.sh`
    ones (`05-scoped-roles.sh` grants on `storage` through psql heredocs)."""
    parts = []
    for path in sorted(SCRIPTS_DIR.iterdir()):
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".sh":
            parts.append(_SHELL_LINE_COMMENT.sub("", text))
        elif path.suffix == ".sql":
            parts.append(text)
    return _SQL_LINE_COMMENT.sub("", "\n".join(parts))


def _storage_table_grants(sql: str, table: str) -> list[tuple[str, list[str]]]:
    """`(statement, grantees)` for every GRANT reaching `storage.<table>`.

    Covers object lists (`ON storage.objects, storage.buckets`, optional
    `TABLE`, quoted `"storage"."objects"`), schema lists
    (`ALL TABLES IN SCHEMA public, storage`) and default privileges on
    `storage`. Schema USAGE, sequences and functions are not table access.
    """
    table_ref = re.compile(rf'^"?storage"?\s*\.\s*"?{table}"?$', re.IGNORECASE)
    found = []
    for raw in sql.split(";"):
        statement = " ".join(raw.split())
        match = _GRANT_STATEMENT.search(statement)
        if not match:
            continue
        target = match.group("target").strip()
        if _grant_reaches_storage_table(target, match.group("defaults"), table_ref):
            grantees = _identifiers(_GRANTEE_TAIL.sub("", match.group("grantees")))
            found.append((statement, grantees))
    return found


def _grant_reaches_storage_table(target: str, defaults, table_ref) -> bool:
    """Whether one GRANT's target covers the storage table `table_ref` names."""
    if defaults is not None:
        schemas = _DEFAULTS_IN_SCHEMA.search(defaults)
        # No IN SCHEMA = every schema, storage included.
        reaches = schemas is None or "storage" in _identifiers(schemas.group(1))
        return reaches and bool(re.fullmatch(r"TABLES", target, re.IGNORECASE))
    all_tables = _ALL_TABLES_IN_SCHEMA.match(target)
    if all_tables:
        return "storage" in _identifiers(all_tables.group(1))
    objects = re.sub(r"^TABLE\s+", "", target, flags=re.IGNORECASE)
    return any(table_ref.match(obj.strip()) for obj in objects.split(","))


@pytest.mark.parametrize(
    "statement",
    [
        "GRANT SELECT ON storage.objects, storage.buckets TO anon;",
        "GRANT SELECT ON TABLE storage.buckets, storage.objects TO anon;",
        'GRANT SELECT ON "storage"."objects", "storage"."buckets" TO anon;',
        "GRANT SELECT ON ALL TABLES IN SCHEMA public, storage TO anon;",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public, storage GRANT SELECT ON TABLES TO anon;",
        "ALTER DEFAULT PRIVILEGES FOR ROLE x IN SCHEMA storage GRANT ALL ON TABLES TO service_role, anon;",
        "DO $$ BEGIN GRANT SELECT ON storage.objects, storage.buckets TO anon; END $$;",
        "psql <<'SQL'\nGRANT SELECT ON storage.objects, storage.buckets TO :\"anon\";\nSQL",
    ],
)
def test_the_storage_grant_scan_sees_every_spelling(statement):
    """A guard that a respelling slips past is worse than no guard."""
    for table in ("objects", "buckets"):
        grants = _storage_table_grants(statement, table)
        assert grants and "anon" in grants[0][1], (table, statement)


def test_the_storage_grant_scan_finds_the_real_service_grants():
    """Positive control: the legitimate storage grants in the seed are seen."""
    grantees = {
        grantee
        for table in ("objects", "buckets")
        for _, names in _storage_table_grants(_grant_and_seed_sql(), table)
        for grantee in names
    }
    assert {"service_role", "storage_role"} <= grantees, grantees


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table", ["objects", "buckets"])
def test_storage_tables_are_not_granted_to_client_roles(table, role):
    """`storage` has RLS DISABLED, so the GRANT is the only control.

    `04-storage.sql` disables RLS on these deliberately ("managing access
    through GRANTs instead"), and `storage` is in PGRST_DB_SCHEMA — so a grant
    to `anon` made every object path, owner and bucket row readable by any
    unauthenticated peer.

    `authenticated` is not the safer half of that pair. GOTRUE_DISABLE_SIGNUP
    is "false", so anyone who can reach the gateway can mint one of these JWTs,
    and with RLS off there is no owner scoping left -- a SELECT grant exposes
    every bucket's contents and a DML grant lets any account rewrite or delete
    other people's object rows. PostgREST reaches these tables only by switching
    into these two roles; the Storage service uses its own credentials.

    Grantee lists are parsed, not substring-matched, and `PUBLIC` counts as
    every role. Service identities (`service_role`, `:"storage_role"`, the
    read-only reader and Studio roles in `05-scoped-roles.sh`) are allowed.
    """
    for statement, grantees in _storage_table_grants(_grant_and_seed_sql(), table):
        assert role not in grantees and "public" not in grantees, (
            f"storage.{table} is granted to {role}: {statement}"
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
def test_public_client_grants_are_conditional_on_row_level_security():
    """`GRANT ... ON ALL TABLES IN SCHEMA public` must not reach client roles.

    This slice re-runs on every boot, and `ALL TABLES` takes every table in the
    schema -- including ones created since by whatever else shares the
    database. Open WebUI and JupyterHub both use SUPABASE_DB_NAME_URI with no
    schema of their own and neither enables RLS, while PGRST_DB_SCHEMA
    publishes `public`; so a blanket grant republished their tables through
    PostgREST after every restart. ALTER DEFAULT PRIVILEGES is creator-scoped
    and never covered them, so it is neither the cause nor the fix.
    """
    sql = (SCRIPTS_DIR / "06-permissions.sql").read_text(encoding="utf-8")

    for match in re.finditer(
        r"GRANT\s+[^;]*?\s+ON\s+ALL\s+TABLES\s+IN\s+SCHEMA\s+public\s+TO\s+([^;]+);",
        sql,
        re.IGNORECASE,
    ):
        grantees = match.group(1)
        for role in ("anon", "authenticated"):
            assert role not in grantees, (
                f"blanket public grant reaches {role}, which republishes every "
                f"co-tenant table on each boot: {match.group(0).strip()}"
            )

    # The replacement must actually be conditional on RLS, not just narrower.
    assert re.search(r"rowsecurity", sql, re.IGNORECASE), (
        "the per-table grant loop is gone; client roles on `public` must stay "
        "gated on the table actually carrying RLS"
    )


def test_function_grants_never_reexpose_security_definer_routines():
    """06 runs on every boot, after the definer functions of slices 10/14
    already exist; a blanket grant to `authenticated` reopened them through
    PostgREST /rpc until those slices revoked them again."""
    sql = (SCRIPTS_DIR / "06-permissions.sql").read_text(encoding="utf-8")
    assert "GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO authenticated" not in sql
    assert "NOT p.prosecdef" in sql
    assert "GRANT ALL ON ROUTINE %s TO authenticated" in sql
