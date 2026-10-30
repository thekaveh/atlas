from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml


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

    assert "./db/scripts:/scripts" in init_service["volumes"]
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
