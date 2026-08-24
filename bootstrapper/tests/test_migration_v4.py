"""Migration v4: stale curated Ollama model reference cleanup.

The maintainer's real .env carried:
    LITELLM_DEFAULT_MODEL=ollama/qwen3.6:latest
    LITELLM_VISION_MODEL=ollama/qwen3.6:latest
    OLLAMA_USER_MODELS=nomic-embed-text,nomic-embed-text:latest,qwen3-embedding:0.6b,qwen3.6:latest
after services/ollama/models.yaml retired ``qwen3.6`` in favor of
``qwen3.8`` — the wizard kept re-offering the dead model as the chat
default. This migration rewrites the known-stale references once.

These tests assert against the REAL curated catalog (``qwen3.8:latest``
is the actual current content+vision default) — same convention as
``tests/test_model_resolver.py``, which already hardcodes that name.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.migrations.migration_v4 import (
    apply as apply_v4,
    needs_migration as needs_v4,
    stamp_version as stamp_v4,
    _family_root,
    _resolve_stale_litellm_value,
    _resolve_stale_ollama_user_models,
    _RENAMED_OLLAMA_MODELS,
)
from utils.llm_catalog import ollama_entries


CATALOG = ollama_entries()


# ── _family_root ────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("ollama/qwen3.6:latest", "qwen3.6"),
    ("ollama/qwen3.8", "qwen3.8"),
    ("qwen3.6:latest", "qwen3.6"),
    ("nomic-embed-text", "nomic-embed-text"),
])
def test_family_root(value, expected):
    assert _family_root(value) == expected


# ── the current catalog actually has the rename target ─────────────

def test_rename_table_targets_are_all_curated():
    """Guard the table itself: every "new" name in
    _RENAMED_OLLAMA_MODELS must exist in the live catalog, or the
    migration would silently no-op instead of repairing anything."""
    for new_root in _RENAMED_OLLAMA_MODELS.values():
        assert any(
            e.name.split(":", 1)[0] == new_root for e in CATALOG
        ), f"rename target {new_root!r} is not in the curated catalog"


def test_qwen3_6_is_a_known_rename_to_qwen3_8():
    assert _RENAMED_OLLAMA_MODELS.get("qwen3.6") == "qwen3.8"


# ── _resolve_stale_litellm_value ────────────────────────────────────

def test_known_rename_resolves_to_current_catalog_name():
    result = _resolve_stale_litellm_value(
        "ollama/qwen3.6:latest", role="content", catalog=CATALOG,
    )
    assert result == "ollama/qwen3.8:latest"


def test_already_curated_value_is_untouched():
    result = _resolve_stale_litellm_value(
        "ollama/qwen3.8:latest", role="content", catalog=CATALOG,
    )
    assert result is None


def test_uncurated_but_not_a_known_rename_is_left_alone():
    """A deliberately-picked live-library model (not in the small
    curated set, and not a known retirement) must NOT be silently
    overridden — see the module docstring's false-positive rationale."""
    result = _resolve_stale_litellm_value(
        "ollama/llama3.1:latest", role="content", catalog=CATALOG,
    )
    assert result is None


def test_cloud_provider_value_is_out_of_scope():
    result = _resolve_stale_litellm_value(
        "openai/gpt-5", role="content", catalog=CATALOG,
    )
    assert result is None


def test_empty_value_is_a_no_op():
    assert _resolve_stale_litellm_value("", role="content", catalog=CATALOG) is None


# ── _resolve_stale_ollama_user_models ───────────────────────────────

def test_user_models_rewrites_only_the_known_stale_entry():
    csv, changes = _resolve_stale_ollama_user_models(
        "nomic-embed-text,nomic-embed-text:latest,qwen3-embedding:0.6b,qwen3.6:latest",
        CATALOG,
    )
    names = csv.split(",")
    assert "qwen3.6:latest" not in names
    assert "qwen3.8:latest" in names
    assert "nomic-embed-text" in names
    assert "nomic-embed-text:latest" in names
    assert "qwen3-embedding:0.6b" in names
    assert changes == ["qwen3.6:latest -> qwen3.8:latest"]


def test_user_models_preserves_non_curated_live_library_pick():
    csv, changes = _resolve_stale_ollama_user_models(
        "llama3.1:latest,qwen3.8:latest", CATALOG,
    )
    assert csv == "llama3.1:latest,qwen3.8:latest"
    assert changes == []


def test_user_models_dedupes_when_rename_collides_with_existing():
    csv, changes = _resolve_stale_ollama_user_models(
        "qwen3.6:latest,qwen3.8:latest", CATALOG,
    )
    assert csv == "qwen3.8:latest"  # de-duped, first-seen order
    assert changes == ["qwen3.6:latest -> qwen3.8:latest"]


# ── needs_migration predicate ────────────────────────────────────────

def test_needs_migration_true_when_sentinel_at_3(tmp_path):
    p = tmp_path / ".env"
    p.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=3\n")
    assert needs_v4(p) is True


def test_needs_migration_false_when_sentinel_at_4(tmp_path):
    p = tmp_path / ".env"
    p.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=4\n")
    assert needs_v4(p) is False


def test_needs_migration_false_when_sentinel_above_4(tmp_path):
    p = tmp_path / ".env"
    p.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=5\n")
    assert needs_v4(p) is False


def test_needs_migration_true_when_sentinel_absent(tmp_path):
    p = tmp_path / ".env"
    p.write_text("FOO=bar\n")
    assert needs_v4(p) is True


def test_needs_migration_false_when_file_missing(tmp_path):
    p = tmp_path / "nonexistent.env"
    assert needs_v4(p) is False


# ── End-to-end apply (the exact maintainer repro) ────────────────────

def _maintainer_repro_env(tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=3\n"
        "LITELLM_DEFAULT_MODEL=ollama/qwen3.6:latest\n"
        "LITELLM_VISION_MODEL=ollama/qwen3.6:latest\n"
        "OLLAMA_USER_MODELS=nomic-embed-text,nomic-embed-text:latest,"
        "qwen3-embedding:0.6b,qwen3.6:latest\n"
    )
    return env


def test_maintainer_repro_end_to_end(tmp_path):
    env = _maintainer_repro_env(tmp_path)
    apply_v4(env)
    text = env.read_text()
    assert "LITELLM_DEFAULT_MODEL=ollama/qwen3.8:latest" in text
    assert "LITELLM_VISION_MODEL=ollama/qwen3.8:latest" in text
    assert "OLLAMA_USER_MODELS=" in text
    assert "qwen3.6" not in text.split("BOOTSTRAPPER", 1)[0]  # sanity: no stray leftover
    user_models_line = next(
        l for l in text.splitlines() if l.startswith("OLLAMA_USER_MODELS=")
    )
    names = user_models_line.split("=", 1)[1].split(",")
    assert "qwen3.6:latest" not in names
    assert "qwen3.8:latest" in names


def test_creates_backup(tmp_path):
    env = _maintainer_repro_env(tmp_path)
    apply_v4(env)
    backups = list(tmp_path.glob(".env.backup.v4.*"))
    assert len(backups) == 1


def test_no_backup_when_nothing_stale(tmp_path):
    """Defense: a clean .env shouldn't get a pointless backup/rewrite."""
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=3\n"
        "LITELLM_DEFAULT_MODEL=ollama/qwen3.8:latest\n"
    )
    apply_v4(env)
    assert not list(tmp_path.glob(".env.backup.*"))


def test_idempotent_when_sentinel_at_or_above_4(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=4\n"
        "LITELLM_DEFAULT_MODEL=ollama/qwen3.6:latest\n"
    )
    apply_v4(env)
    # apply is a no-op when sentinel >= 4 — stale value untouched because
    # this .env claims to already be past this migration.
    assert "LITELLM_DEFAULT_MODEL=ollama/qwen3.6:latest" in env.read_text()
    assert not list(tmp_path.glob(".env.backup.*"))


def test_handles_fresh_env_no_target_vars(tmp_path):
    """Cold start: none of the target vars present — no-op, no crash."""
    env = tmp_path / ".env"
    env.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=3\n")
    apply_v4(env)  # must not raise
    assert not list(tmp_path.glob(".env.backup.*"))


def test_no_op_on_missing_env_file(tmp_path):
    env = tmp_path / "nonexistent.env"
    apply_v4(env)  # should not raise
    assert not env.exists()


def test_handles_crlf_line_endings(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(
        b"BOOTSTRAPPER_PORT_LAYOUT_VERSION=3\r\n"
        b"LITELLM_DEFAULT_MODEL=ollama/qwen3.6:latest\r\n"
    )
    apply_v4(env)
    text = env.read_text()
    assert "LITELLM_DEFAULT_MODEL=ollama/qwen3.8:latest" in text


# ── stamp_version ─────────────────────────────────────────────────────

def test_stamp_version_writes_4(tmp_path):
    p = tmp_path / ".env"
    p.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=3\n")
    stamp_v4(p)
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=4" in p.read_text()


def test_stamp_version_appends_when_absent(tmp_path):
    p = tmp_path / ".env"
    p.write_text("FOO=bar\n")
    stamp_v4(p)
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=4" in p.read_text()
