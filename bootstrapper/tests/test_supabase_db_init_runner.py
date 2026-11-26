from __future__ import annotations

import os
import re
import secrets
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
import yaml

from tests import seed_harness as h


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "services" / "supabase" / "db" / "scripts" / "db-init-runner.sh"
COMPOSE = REPO_ROOT / "services" / "supabase" / "compose.yml"


@pytest.fixture(autouse=True)
def _private_runner_inventories(tmp_path, monkeypatch):
    copied_runner = tmp_path / "db-init-runner.sh"
    source = RUNNER.read_text(encoding="utf-8")
    # A silent no-op replace would send the copy back to the shared /tmp files.
    assert source.count("/tmp/_db_init_atlas_sql_files") == 1
    assert source.count("/tmp/_db_init_user_sql_files") == 1
    copied_runner.write_text(
        source
        .replace("/tmp/_db_init_atlas_sql_files", str(tmp_path / "atlas-sql.list"))
        .replace("/tmp/_db_init_user_sql_files", str(tmp_path / "user-sql.list")),
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "RUNNER", copied_runner)


def _write_fake_postgres_tools(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "psql.log"

    pg_isready = bin_dir / "pg_isready"
    pg_isready.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    psql = bin_dir / "psql"
    psql.write_text(
        """#!/bin/sh
script=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-f" ]; then
    script="$2"
    break
  fi
  shift
done
# The built-in shadowing guard reads its query from stdin: no shadowing.
if [ -z "$script" ]; then cat >/dev/null; exit 0; fi
echo "$script" >> "$PSQL_LOG"
case "$script" in
  *fail*.sql) echo "fake psql failure for $script" >&2; exit 17 ;;
esac
exit 0
""",
        encoding="utf-8",
    )

    pg_isready.chmod(0o755)
    psql.chmod(0o755)
    return bin_dir, log_path


def _run_runner(
    tmp_path: Path,
    atlas_scripts: dict[str, str],
    user_scripts: dict[str, str] | None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    atlas_dir = tmp_path / "atlas-scripts"
    atlas_dir.mkdir()
    for name, body in atlas_scripts.items():
        (atlas_dir / name).write_text(body, encoding="utf-8")
    (atlas_dir / "05-scoped-roles.sh").write_text(
        '#!/bin/sh\necho "ROLE_PROVISIONER" >> "$PSQL_LOG"\n',
        encoding="utf-8",
    )

    user_dir = tmp_path / "user-scripts"
    if user_scripts is not None:
        user_dir.mkdir()
        for name, body in user_scripts.items():
            (user_dir / name).write_text(body, encoding="utf-8")

    bin_dir, log_path = _write_fake_postgres_tools(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PGHOST": "supabase-db",
        "PGUSER": "supabase_admin",
        "PGPASSWORD": "password",
        "PGDATABASE": "postgres",
        "PSQL_LOG": str(log_path),
        "ATLAS_DB_INIT_SCRIPT_DIR": str(atlas_dir),
        "ATLAS_DB_INIT_USER_SCRIPT_DIR": str(user_dir),
    }
    result = subprocess.run(
        ["/bin/sh", str(RUNNER)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=10,
    )
    return result, log_path, atlas_dir, user_dir


def _psql_log(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    return log_path.read_text(encoding="utf-8").splitlines()


def test_db_init_runner_runs_atlas_only_when_user_slot_missing(tmp_path: Path) -> None:
    result, log_path, atlas_dir, _user_dir = _run_runner(
        tmp_path,
        {
            "02-atlas.sql": "select 2;",
            "01-atlas.sql": "select 1;",
        },
        user_scripts=None,
    )

    assert result.returncode == 0, result.stderr
    assert _psql_log(log_path) == [
        str(atlas_dir / "01-atlas.sql"),
        str(atlas_dir / "02-atlas.sql"),
        "ROLE_PROVISIONER",
    ]
    assert "No user SQL directory found" in result.stdout


def test_db_init_runner_runs_user_sql_after_all_atlas_sql(tmp_path: Path) -> None:
    result, log_path, atlas_dir, user_dir = _run_runner(
        tmp_path,
        {
            "20-atlas.sql": "select 20;",
            "10-atlas.sql": "select 10;",
        },
        {
            "00-user.sql": "select 0;",
            "99-user.sql": "select 99;",
        },
    )

    assert result.returncode == 0, result.stderr
    assert _psql_log(log_path) == [
        str(atlas_dir / "10-atlas.sql"),
        str(atlas_dir / "20-atlas.sql"),
        "ROLE_PROVISIONER",
        str(user_dir / "00-user.sql"),
        str(user_dir / "99-user.sql"),
        # Client grants re-applied after user scripts, so a user table
        # without RLS is not published to anon/authenticated meanwhile.
        str(atlas_dir / "06-permissions.sql"),
    ]


def test_db_init_runner_surfaces_user_sql_failures(tmp_path: Path) -> None:
    result, log_path, atlas_dir, user_dir = _run_runner(
        tmp_path,
        {"01-atlas.sql": "select 1;"},
        {
            "01-user.sql": "select 1;",
            "02-fail.sql": "select broken;",
            "03-user.sql": "select 3;",
        },
    )

    assert result.returncode == 17
    assert _psql_log(log_path) == [
        str(atlas_dir / "01-atlas.sql"),
        "ROLE_PROVISIONER",
        str(user_dir / "01-user.sql"),
        str(user_dir / "02-fail.sql"),
    ]
    assert "Running user SQL script" in result.stdout
    assert "02-fail.sql" in result.stdout
    assert "fake psql failure" in result.stderr


def test_supabase_db_init_mounts_optional_user_sql_slot() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    init_service = compose["services"]["supabase-db-init"]

    assert "./db/scripts:/scripts:ro" in init_service["volumes"]
    assert "./db/_user:/user-scripts:ro" in init_service["volumes"]


def _inject_inventory_failure(monkeypatch, phase, mode):
    import shlex
    import shutil

    real_tools = _write_fake_postgres_tools
    real_find, real_sort = shutil.which("find"), shutil.which("sort")
    assert real_find and real_sort

    def tools(tmp_path):
        bin_dir, log_path = real_tools(tmp_path)
        find = bin_dir / "find"
        find.write_text(
            '#!/bin/sh\n'
            'if [ "$INVENTORY_FAILURE_MODE" != sort ] && '
            '[ "$1" = "$INVENTORY_FAILURE_DIR" ]; then\n'
            '  if [ "$INVENTORY_FAILURE_MODE" = partial ]; then\n'
            '    printf "%s\\n" "$1/10-' + phase + '.sql"\n'
            '  fi\n'
            '  echo "synthetic inventory read failure" >&2\n'
            '  exit 73\nfi\nexec ' + shlex.quote(real_find) + ' "$@"\n',
            encoding="utf-8",
        )
        sort = bin_dir / "sort"
        sort.write_text(
            '#!/bin/sh\nif [ "$INVENTORY_FAILURE_MODE" = sort ]; then\n'
            '  cat >/dev/null\n  exit 74\nfi\nexec '
            + shlex.quote(real_sort) + ' "$@"\n', encoding="utf-8",
        )
        find.chmod(0o755)
        sort.chmod(0o755)
        monkeypatch.setenv("INVENTORY_FAILURE_DIR", str(tmp_path / (phase + "-scripts")))
        return bin_dir, log_path

    monkeypatch.setenv("INVENTORY_FAILURE_MODE", mode)
    monkeypatch.setitem(globals(), "_write_fake_postgres_tools", tools)


@pytest.mark.parametrize("phase", ["atlas", "user"])
@pytest.mark.parametrize("mode", ["empty", "partial"])
def test_inventory_read_error_never_consumes_partial_migrations(tmp_path, monkeypatch, phase, mode):
    _inject_inventory_failure(monkeypatch, phase, mode)
    result, log_path, atlas_dir, _user_dir = _run_runner(
        tmp_path,
        {"20-atlas.sql": "select 20;", "10-atlas.sql": "select 10;"},
        {"20-user.sql": "select 20;", "10-user.sql": "select 10;"},
    )
    assert result.returncode == 73, result.stderr
    assert "synthetic inventory read failure" in result.stderr
    assert "All post-initialization scripts finished successfully" not in result.stdout
    expected = [] if phase == "atlas" else [
        str(atlas_dir / "10-atlas.sql"), str(atlas_dir / "20-atlas.sql"), "ROLE_PROVISIONER",
    ]
    assert _psql_log(log_path) == expected


def test_inventory_sort_error_stops_before_migrations(tmp_path, monkeypatch):
    _inject_inventory_failure(monkeypatch, "atlas", "sort")
    result, log_path, _atlas_dir, _user_dir = _run_runner(
        tmp_path, {"10-atlas.sql": "select 10;"}, {"10-user.sql": "select 10;"},
    )
    assert result.returncode == 74
    assert _psql_log(log_path) == []
    assert "All post-initialization scripts finished successfully" not in result.stdout


def test_atlas_migration_stops_at_sql_error(tmp_path):
    result, log_path, atlas_dir, _user_dir = _run_runner(
        tmp_path,
        {"10-atlas.sql": "select 10;", "20-fail.sql": "invalid", "30-atlas.sql": "select 30;"},
        {"10-user.sql": "select 10;"},
    )
    assert result.returncode == 17
    assert _psql_log(log_path) == [str(atlas_dir / "10-atlas.sql"), str(atlas_dir / "20-fail.sql")]
    assert "All post-initialization scripts finished successfully" not in result.stdout


# ── GoTrue migration tracker (#1825) ──────────────────────────────────────────
# GoTrue's tracker is an unqualified schema_migrations, resolved through the role's
# search_path. As supabase_auth_admin (search_path auth) GoTrue v2.191.0 reads
# auth.schema_migrations. Before #991 it connected as supabase_admin, so the
# history sits in public.schema_migrations, and 03c-gotrue-legacy-tracker.sql copies
# the GoTrue versions public holds. The tests
# below pin the tracked version list to the image and boot GoTrue against a
# legacy-shaped database.

SCRIPTS_DIR = REPO_ROOT / "services" / "supabase" / "db" / "scripts"
GOTRUE_TRACKER_LIST = REPO_ROOT / "services" / "supabase" / "db" / "gotrue-v2.191.0-migrations.txt"
GOTRUE_TRACKER_SLICE = SCRIPTS_DIR / "03c-gotrue-legacy-tracker.sql"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
GOTRUE_LEGACY_CUTOFF = "20250717082212"
# Realtime-style rows that share public.schema_migrations in the legacy drill. They
# are not GoTrue versions, so they must never reach auth.schema_migrations.
REALTIME_STYLE_VERSIONS = (
    "20250718120000", "20250901000000", "20251115000000", "20260422000000",
    *(f"{2021 + k // 12:04d}{k % 12 + 1:02d}15120000" for k in range(27)),
)
MIGRATION_NAME = re.compile(r"^(?:\d{14}|00)_[A-Za-z0-9_]+(?:\.postgres)?\.up\.sql$")
IMAGE_MIGRATION = re.compile(rb"(?<![A-Za-z0-9_])((?:\d{14}|00)_[A-Za-z0-9_.\-]{1,120}?\.up\.sql)")
GOTRUE_TRACKER_LIVE = pytest.mark.skipif(
    os.environ.get("ATLAS_GOTRUE_TRACKER_LIVE") != "1",
    reason="set ATLAS_GOTRUE_TRACKER_LIVE=1 to run the docker GoTrue tracker drill (#1825)",
)


def _tracked_gotrue_names() -> list[str]:
    return GOTRUE_TRACKER_LIST.read_text(encoding="utf-8").split()


def _tracked_gotrue_versions() -> list[str]:
    return [name.split("_", 1)[0] for name in _tracked_gotrue_names()]


def _pinned_gotrue_image() -> str:
    match = re.search(r"^SUPABASE_AUTH_IMAGE=(\S+)$", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M)
    assert match, "SUPABASE_AUTH_IMAGE missing from .env.example"
    return match.group(1)


def test_gotrue_tracked_list_is_unique_and_named_for_the_pin() -> None:
    names = _tracked_gotrue_names()
    versions = _tracked_gotrue_versions()
    assert names and all(MIGRATION_NAME.match(name) for name in names)
    assert len(set(versions)) == len(versions)
    assert versions == sorted(versions, key=lambda v: v.zfill(14))
    # A pin bump must rename the list, so a stale list cannot pass silently.
    assert _pinned_gotrue_image().split(":", 1)[1] in GOTRUE_TRACKER_LIST.name


def test_gotrue_slice_embeds_exactly_the_tracked_versions() -> None:
    sql = GOTRUE_TRACKER_SLICE.read_text(encoding="utf-8")
    embedded = re.findall(r"\('(\d+)'\)", sql[sql.index("WITH gotrue(version)"):sql.index("seeded(version)")])
    assert embedded == _tracked_gotrue_versions()
    seeded = re.findall(r"\('(\d+)'\)", sql[sql.index("seeded(version)"):sql.index("legacy AS")])
    assert seeded and set(seeded) <= set(embedded)


def test_gotrue_slice_runs_after_the_03b_seed_and_before_the_scoped_roles() -> None:
    names = sorted(path.name for path in SCRIPTS_DIR.glob("*.sql"))
    assert names.index("03b-gotrue-migration-sync.sql") < names.index("03c-gotrue-legacy-tracker.sql")
    assert "03c-gotrue-legacy-tracker.sql" < "05-scoped-roles.sh"


def _docker(*args: str, check: bool = True, timeout: int = 120, input_bytes: bytes | None = None):
    result = subprocess.run(["docker", *args], capture_output=True, input=input_bytes, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr.decode(errors='replace')[-600:]}")
    return result


def _psql(pg: str, statement: str) -> str:
    return _docker(
        "exec", pg, "psql", "-h", "127.0.0.1", "-U", h.DB_USER, "-d", h.DB_NAME,
        "-v", "ON_ERROR_STOP=1", "-At", "-c", statement,
    ).stdout.decode().strip()


def _tracker_counts(pg: str) -> dict[str, str]:
    return {
        "auth": _psql(pg, "SELECT count(*)||'|'||coalesce(max(version::text),'') FROM auth.schema_migrations"),
        "public": _psql(pg, "SELECT count(*)||'|'||coalesce(max(version::text),'') FROM public.schema_migrations"),
    }


def _start_postgres(pg: str, net: str) -> None:
    h.ensure_database_image()
    _docker(
        "run", "-d", "--pull=never", "--name", pg, "--network", net, "--network-alias", "supabase-db",
        "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=1536m",
        "-v", f"{SCRIPTS_DIR}:/scripts:ro",
        "-e", f"POSTGRES_USER={h.DB_USER}", "-e", f"POSTGRES_PASSWORD={h.DB_PASSWORD}",
        "-e", f"POSTGRES_DB={h.DB_NAME}", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
        h.DB_IMAGE, timeout=60,
    )
    h.wait_for_postgres(pg)


def _run_init_chain(pg: str) -> None:
    """Mirror db-init-runner.sh: every *.sql in sorted order, then the scoped-roles script."""
    for sql in sorted(SCRIPTS_DIR.glob("*.sql")):
        _docker(
            "exec", "-i", pg, "psql", "-h", "127.0.0.1", "-v", "ON_ERROR_STOP=1",
            "-U", h.DB_USER, "-d", h.DB_NAME, "-f", "-",
            input_bytes=sql.read_bytes(), timeout=120,
        )
    env_args: list[str] = [
        "-e", "PGHOST=127.0.0.1", "-e", f"PGUSER={h.DB_USER}",
        "-e", f"PGPASSWORD={h.DB_PASSWORD}", "-e", f"PGDATABASE={h.DB_NAME}",
    ]
    for key, value in h.SCOPED_ROLE_TEST_ENV.items():
        env_args += ["-e", f"{key}={value}"]
    _docker("exec", *env_args, pg, "sh", "/scripts/05-scoped-roles.sh", timeout=300)


def _start_gotrue(name: str, net: str) -> tuple[bool, str]:
    """Run the pinned GoTrue against supabase-db; return (healthy, container logs)."""
    env = {
        "GOTRUE_API_HOST": "0.0.0.0", "GOTRUE_API_PORT": "9999", "GOTRUE_DB_DRIVER": "postgres",
        "GOTRUE_DB_DATABASE_URL": (
            f"postgres://supabase_auth_admin:{h.SCOPED_ROLE_TEST_ENV['SUPABASE_AUTH_DB_PASSWORD']}"
            f"@supabase-db:5432/{h.DB_NAME}"
        ),
        "DB_NAMESPACE": "auth", "GOTRUE_JWT_SECRET": secrets.token_hex(32), "GOTRUE_JWT_EXP": "3600",
        "GOTRUE_JWT_AUD": "authenticated", "GOTRUE_JWT_DEFAULT_GROUP_NAME": "authenticated",
        "GOTRUE_JWT_ADMIN_ROLES": "service_role", "GOTRUE_DISABLE_SIGNUP": "false",
        "GOTRUE_MAILER_AUTOCONFIRM": "true", "GOTRUE_SITE_URL": "http://localhost:3000",
        "API_EXTERNAL_URL": "http://localhost:9999", "GOTRUE_SMTP_HOST": "localhost",
        "GOTRUE_SMTP_PORT": "1025", "GOTRUE_EXTERNAL_EMAIL_ENABLED": "true",
    }
    args = ["run", "-d", "--pull=never", "--name", name, "--network", net, "-p", "127.0.0.1::9999"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    _docker(*args, _pinned_gotrue_image(), timeout=60)
    healthy, port, deadline = False, None, time.monotonic() + 240
    try:
        while time.monotonic() < deadline:
            state = _docker("inspect", "--format", "{{.State.Status}}", name, check=False).stdout.decode().strip()
            if state in {"exited", "dead"}:
                break
            if port is None:
                mapping = _docker("port", name, "9999/tcp", check=False).stdout.decode().splitlines()
                port = mapping[0].rsplit(":", 1)[-1] if mapping else None
            if port:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as resp:
                        if resp.status == 200:
                            healthy = True
                            break
                except OSError:
                    pass
            time.sleep(2)
    finally:
        logs = _docker("logs", name, check=False)
    return healthy, (logs.stdout + logs.stderr).decode(errors="replace")


def _remove(*names: str) -> None:
    for name in names:
        _docker("rm", "-f", name, check=False)


@pytest.mark.live
@GOTRUE_TRACKER_LIVE
def test_gotrue_tracked_list_matches_the_pinned_image(tmp_path: Path) -> None:
    name = f"atlas-gotrue-tracker-list-{uuid.uuid4().hex[:10]}"
    _docker("create", "--pull=never", "--name", name, _pinned_gotrue_image(), timeout=60)
    try:
        binary = tmp_path / "auth"
        _docker("cp", f"{name}:/usr/local/bin/auth", str(binary), timeout=180)
        found = {match.decode() for match in IMAGE_MIGRATION.findall(binary.read_bytes())}
    finally:
        _remove(name)
    tracked = set(_tracked_gotrue_names())
    assert found == tracked, (
        f"not in list: {sorted(found - tracked)}; not in image: {sorted(tracked - found)}"
    )


@pytest.mark.live
@GOTRUE_TRACKER_LIVE
def test_legacy_tracker_volume_boots_gotrue_only_after_the_slice() -> None:
    token = uuid.uuid4().hex[:10]
    net, pg = f"atlas-gotrue-tracker-net-{token}", f"atlas-gotrue-tracker-pg-{token}"
    first, red, green = (f"atlas-gotrue-tracker-{step}-{token}" for step in ("first", "red", "green"))
    _docker("network", "create", net)
    try:
        _start_postgres(pg, net)
        _run_init_chain(pg)
        # Build a fully migrated auth schema, as a normal first GoTrue start does.
        healthy, logs = _start_gotrue(first, net)
        assert healthy, logs[-2000:]
        _remove(first)

        # Legacy state from #1825: auth ends at the cutoff; public holds GoTrue and Realtime rows.
        _psql(pg, f"DELETE FROM auth.schema_migrations WHERE version > '{GOTRUE_LEGACY_CUTOFF}'")
        assert set(REALTIME_STYLE_VERSIONS).isdisjoint(_tracked_gotrue_versions())
        values = ", ".join(
            f"({version}, now())" for version in [*_tracked_gotrue_versions(), *REALTIME_STYLE_VERSIONS]
        )
        _psql(pg, f"INSERT INTO public.schema_migrations (version, inserted_at) VALUES {values} ON CONFLICT DO NOTHING")
        assert _tracker_counts(pg) == {"auth": f"62|{GOTRUE_LEGACY_CUTOFF}", "public": "100|20260422000000"}

        # Without the slice GoTrue re-runs an applied migration and fails on it (the ticket's error).
        healthy, logs = _start_gotrue(red, net)
        assert not healthy
        assert "20250731150234_add_oauth_clients_table" in logs
        assert "client_id\\\" does not exist (SQLSTATE 42703)" in logs  # JSON-escaped quotes in the log
        _remove(red)

        # With the slice: the init chain copies the GoTrue versions, GoTrue boots and applies nothing.
        _run_init_chain(pg)
        counts = _tracker_counts(pg)
        assert counts == {"auth": "76|20260302000000", "public": "100|20260422000000"}
        realtime = ", ".join(f"'{v}'" for v in REALTIME_STYLE_VERSIONS)
        assert _psql(pg, f"SELECT count(*) FROM auth.schema_migrations WHERE version IN ({realtime})") == "0"
        healthy, logs = _start_gotrue(green, net)
        assert healthy, logs[-2000:]
        assert "Migrations already up to date" in logs
        assert "error executing migrations" not in logs
        _remove(green)

        # The init chain is idempotent: a second run leaves both trackers unchanged.
        _run_init_chain(pg)
        assert _tracker_counts(pg) == counts
    finally:
        _remove(first, red, green, pg)
        _docker("network", "rm", net, check=False)


@pytest.mark.live
@GOTRUE_TRACKER_LIVE
def test_fresh_volume_copies_no_gotrue_versions_and_boots_gotrue() -> None:
    token = uuid.uuid4().hex[:10]
    net, pg, gt = f"atlas-gotrue-fresh-net-{token}", f"atlas-gotrue-fresh-pg-{token}", f"atlas-gotrue-fresh-{token}"
    _docker("network", "create", net)
    try:
        _start_postgres(pg, net)
        _run_init_chain(pg)
        # A fresh volume has no GoTrue history in public, so the slice must copy nothing.
        assert _tracker_counts(pg)["public"] == "4|20221215195900"
        versions = ", ".join(f"'{v}'" for v in _tracked_gotrue_versions())
        assert _psql(pg, f"SELECT count(*) FROM auth.schema_migrations WHERE version IN ({versions})") == "0"
        healthy, logs = _start_gotrue(gt, net)
        assert healthy, logs[-2000:]
        assert f'"count":{len(_tracked_gotrue_names())}' in logs
        assert _psql(pg, f"SELECT count(*) FROM auth.schema_migrations WHERE version IN ({versions})") == str(
            len(_tracked_gotrue_names())
        )
    finally:
        _remove(gt, pg)
        _docker("network", "rm", net, check=False)
