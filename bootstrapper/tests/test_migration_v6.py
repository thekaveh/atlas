"""migration_v6: rewrite .env MINIO_* Atlas names to SILO_* (storage rename)."""

from __future__ import annotations

from pathlib import Path

from services.migrations.migration_v6 import _rewrite, apply, needs_migration, stamp_version


def _backups(directory: Path, original: bytes) -> list[Path]:
    return [p for p in directory.iterdir() if p.name != ".env" and p.is_file() and p.read_bytes() == original]


def test_minio_keys_are_renamed_in_place_with_values_and_comments_kept(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "# storage\n"
        "MINIO_SOURCE=container  # the storage container\n"
        "\n"
        "export MINIO_PORT=63020\n"
        "MINIO_EXTRA_CONSUMERS=\"daydreams:MINIO_BUCKET_DAYDREAMS:MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY\"  # see MINIO_PORT\n",
        encoding="utf-8",
    )
    assert apply(env) is True
    text = env.read_text(encoding="utf-8")
    assert text.startswith("# storage\nSILO_SOURCE=container")
    assert "\n\nexport SILO_PORT=63020\n" in text  # the blank line survives
    assert "SILO_SOURCE=container  # the storage container\n" in text
    assert "export SILO_PORT=63020\n" in text
    # The value names other storage variables, so those names move too.
    assert (
        'SILO_EXTRA_CONSUMERS="daydreams:SILO_BUCKET_DAYDREAMS:SILO_DAYDREAMS_ACCESS_KEY:'
        'SILO_DAYDREAMS_SECRET_KEY"  # see MINIO_PORT\n'
    ) in text
    assert "MINIO_SOURCE" not in text and "MINIO_EXTRA" not in text


def test_rewrite_reports_each_renamed_pair():
    updated, renamed = _rewrite("MINIO_SOURCE=container\nSILO_PORT=63020\n")
    assert updated == "SILO_SOURCE=container\nSILO_PORT=63020\n"
    assert renamed == [("MINIO_SOURCE", "SILO_SOURCE")]


def test_silo_server_keys_and_unrelated_lines_are_left_alone(tmp_path: Path):
    env = tmp_path / ".env"
    original = (
        "MINIO_BROWSER_REDIRECT_URL=http://localhost:63021\n"
        "MINIO_PROMETHEUS_AUTH_TYPE=public\n"
        "PROJECT_NAME=atlas\n"
    )
    env.write_text(original, encoding="utf-8")
    apply(env)
    assert env.read_text(encoding="utf-8") == original
    assert _backups(tmp_path, original.encode()) == []  # nothing renamed, no backup


def test_a_present_silo_key_wins_and_the_minio_line_is_commented_out(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("SILO_SOURCE=disabled\nMINIO_SOURCE=container\n", encoding="utf-8")
    apply(env)
    text = env.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "SILO_SOURCE=disabled"
    assert text.splitlines()[1] == (
        "# MINIO_SOURCE=container  # superseded by SILO_SOURCE; migrated by migration_v6"
    )


def test_for_a_repeated_minio_key_only_the_last_assignment_is_renamed():
    updated, renamed = _rewrite("MINIO_SOURCE=disabled\nMINIO_SOURCE=container\n")
    lines = updated.splitlines()
    assert lines[1] == "SILO_SOURCE=container"
    assert lines[0].startswith("# MINIO_SOURCE=disabled  # superseded by SILO_SOURCE")
    assert renamed == [("MINIO_SOURCE", "SILO_SOURCE")]


def test_quoted_and_crlf_lines_keep_their_bytes(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_bytes(b"MINIO_ROOT_PASSWORD='p#ss'\r\nMINIO_REGION=us-east-1\r\n")
    apply(env)
    assert env.read_bytes() == b"SILO_ROOT_PASSWORD='p#ss'\r\nSILO_REGION=us-east-1\r\n"


def test_the_migration_is_idempotent(tmp_path: Path):
    env = tmp_path / ".env"
    original = b"BOOTSTRAPPER_PORT_LAYOUT_VERSION=5\nMINIO_SOURCE=container\n"
    env.write_bytes(original)
    apply(env)
    first = env.read_bytes()
    assert first == b"BOOTSTRAPPER_PORT_LAYOUT_VERSION=5\nSILO_SOURCE=container\n"
    assert len(_backups(tmp_path, original)) == 1

    # A second pass on the same file (sentinel not yet stamped) changes nothing
    # and writes no second backup.
    apply(env)
    assert env.read_bytes() == first
    assert len(_backups(tmp_path, original)) == 1


def test_the_sentinel_gates_the_migration(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=5\nMINIO_SOURCE=container\n", encoding="utf-8")
    assert needs_migration(env) is True

    apply(env)
    stamp_version(env)
    assert needs_migration(env) is False
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=6\n" in env.read_text(encoding="utf-8")


def test_a_file_already_at_version_six_is_not_rewritten(tmp_path: Path):
    env = tmp_path / ".env"
    original = "BOOTSTRAPPER_PORT_LAYOUT_VERSION=6\nMINIO_SOURCE=container\n"
    env.write_text(original, encoding="utf-8")
    assert needs_migration(env) is False
    apply(env)
    assert env.read_text(encoding="utf-8") == original


def test_stamp_appends_the_sentinel_when_it_is_missing(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("SILO_SOURCE=container", encoding="utf-8")
    stamp_version(env)
    assert env.read_text(encoding="utf-8") == "SILO_SOURCE=container\nBOOTSTRAPPER_PORT_LAYOUT_VERSION=6\n"


def test_a_missing_env_file_is_a_noop(tmp_path: Path):
    env = tmp_path / ".env"
    assert apply(env) is True
    assert needs_migration(env) is False
    stamp_version(env)
    assert not env.exists()
