"""Migration v5 adds the required native Weaviate filesystem backup module."""

from pathlib import Path

from services.migrations.migration_v5 import apply, needs_migration, stamp_version
from services.migrations.migration_v5 import MigrationV5Error
import pytest


def test_existing_custom_modules_gain_backup_filesystem(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=4\n"
        "WEAVIATE_ENABLE_MODULES=text2vec-openai,generative-openai\n"
    )
    assert needs_migration(env)
    assert apply(env)
    stamp_version(env)
    text = env.read_text()
    assert "WEAVIATE_ENABLE_MODULES=text2vec-openai,generative-openai,backup-filesystem\n" in text
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=5\n" in text
    assert len(list(tmp_path.glob(".env.backup.v5.*"))) == 1


def test_existing_backup_module_is_not_duplicated(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=4\n"
        "WEAVIATE_ENABLE_MODULES=backup-filesystem,text2vec-openai\n"
    )
    assert apply(env)
    stamp_version(env)
    assert env.read_text().count("backup-filesystem") == 1


def test_blank_modules_gain_minimum_safe_module(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=4\nWEAVIATE_ENABLE_MODULES=\n")
    assert apply(env)
    stamp_version(env)
    assert "WEAVIATE_ENABLE_MODULES=backup-filesystem\n" in env.read_text()


def test_v5_is_idempotent(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=5\n"
        "WEAVIATE_ENABLE_MODULES=backup-filesystem\n"
    )
    original = env.read_text()
    assert not needs_migration(env)
    assert apply(env)
    stamp_version(env)
    assert env.read_text() == original


def test_spaced_quoted_assignment_and_comment_are_preserved(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION = 4 # keep sentinel comment\n"
        "WEAVIATE_ENABLE_MODULES = 'text2vec-openai' # keep module comment\n"
    )
    assert apply(env)
    assert (
        "WEAVIATE_ENABLE_MODULES = 'text2vec-openai,backup-filesystem' # keep module comment\n"
        in env.read_text()
    )
    stamp_version(env)
    migrated = env.read_text()
    assert apply(env)
    stamp_version(env)
    assert env.read_text() == migrated


def test_sentinel_format_quote_and_comment_are_preserved(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "  BOOTSTRAPPER_PORT_LAYOUT_VERSION = '4' # keep sentinel\n"
        "WEAVIATE_ENABLE_MODULES=backup-filesystem\n",
        encoding="utf-8",
    )
    stamp_version(env)
    assert env.read_text(encoding="utf-8").startswith(
        "  BOOTSTRAPPER_PORT_LAYOUT_VERSION = '5' # keep sentinel\n"
    )


@pytest.mark.parametrize(
    "assignment",
    [
        'WEAVIATE_ENABLE_MODULES="text2vec-openai',
        'WEAVIATE_ENABLE_MODULES="text2vec-openai" junk "',
        "WEAVIATE_ENABLE_MODULES='text2vec-openai' junk '",
    ],
)
def test_malformed_module_assignment_fails_closed(tmp_path: Path, assignment: str):
    env = tmp_path / ".env"
    env.write_text(
        f"BOOTSTRAPPER_PORT_LAYOUT_VERSION=4\n{assignment}\n",
        encoding="utf-8",
    )
    with pytest.raises(MigrationV5Error, match="malformed"):
        apply(env)
