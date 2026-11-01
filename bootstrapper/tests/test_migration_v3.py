"""Migration v3: COMFYUI_MODEL_SET → COMFYUI_USER_MODELS + sidecar/cache vars."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.migrations.migration_v3 import (
    apply as apply_v3,
    needs_migration as needs_v3,
    stamp_version as stamp_v3,
    _translate_model_set,
)


# ── Translation table ──────────────────────────────────────────────────

@pytest.mark.parametrize("old,expected", [
    ("minimal", "v1-5-pruned-emaonly,vae-ft-mse-840000-ema-pruned"),
    ("sd15",    "v1-5-pruned-emaonly,vae-ft-mse-840000-ema-pruned"),
    ("sdxl",    "sd_xl_base_1.0,sdxl-vae"),
    ("full",    "v1-5-pruned-emaonly,vae-ft-mse-840000-ema-pruned,sd_xl_base_1.0,sdxl-vae"),
    ("",        ""),
])
def test_translation_table(old, expected):
    assert _translate_model_set(old) == expected


def test_unknown_value_returns_empty_with_warning(capsys):
    assert _translate_model_set("does-not-exist") == ""
    captured = capsys.readouterr()
    assert "does-not-exist" in (captured.err + captured.out)


# ── needs_migration predicate ─────────────────────────────────────────

def test_needs_migration_true_when_sentinel_at_2(tmp_path):
    p = tmp_path / ".env"
    p.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=2\n")
    assert needs_v3(p) is True


def test_needs_migration_false_when_sentinel_at_3(tmp_path):
    p = tmp_path / ".env"
    p.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=3\n")
    assert needs_v3(p) is False


def test_needs_migration_false_when_sentinel_above_3(tmp_path):
    p = tmp_path / ".env"
    p.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=4\n")
    assert needs_v3(p) is False


def test_needs_migration_true_when_sentinel_absent(tmp_path):
    p = tmp_path / ".env"
    p.write_text("FOO=bar\n")
    assert needs_v3(p) is True


def test_needs_migration_false_when_file_missing(tmp_path):
    p = tmp_path / "nonexistent.env"
    assert needs_v3(p) is False


# ── End-to-end apply ──────────────────────────────────────────────────

def test_migrates_sdxl(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=2\n"
        "# choose minimal / sd15 / sdxl / full\n"
        "COMFYUI_MODEL_SET=sdxl\n"
        "COMFYUI_SOURCE=container-cpu\n"
    )
    apply_v3(env)
    text = env.read_text()
    assert "COMFYUI_MODEL_SET" not in text
    assert "# choose minimal" not in text   # preceding comment block is stripped
    assert "COMFYUI_USER_MODELS=sd_xl_base_1.0,sdxl-vae" in text
    assert "COMFYUI_CUSTOM_MODELS_FILE=/custom-models.yaml" in text
    # COMFYUI_CATALOG_CACHE_DIR was retired in the DB-backed pivot — the
    # migration must not (re-)inject it.
    assert "COMFYUI_CATALOG_CACHE_DIR" not in text


def test_creates_backup(tmp_path):
    env = tmp_path / ".env"
    env.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=2\nCOMFYUI_MODEL_SET=sd15\n")
    apply_v3(env)
    backups = list(tmp_path.glob(".env.backup.*"))
    assert len(backups) == 1


def test_idempotent_when_sentinel_at_or_above_3(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=3\n"
        "COMFYUI_MODEL_SET=sdxl\n"
    )
    apply_v3(env)
    # apply is a no-op when sentinel >= 3
    assert "COMFYUI_MODEL_SET=sdxl" in env.read_text()
    assert not list(tmp_path.glob(".env.backup.*"))


def test_preserves_existing_user_models_via_union(tmp_path):
    """If user already set COMFYUI_USER_MODELS, union with old translation."""
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=2\n"
        "COMFYUI_MODEL_SET=sd15\n"
        "COMFYUI_USER_MODELS=epiCRealism-XL\n"
    )
    apply_v3(env)
    text = env.read_text()
    assert "epiCRealism-XL" in text
    assert "v1-5-pruned-emaonly" in text


def test_keeps_custom_models_file_and_user_models_first(tmp_path):
    """A customized sidecar path survives; user entries lead, without duplicates."""
    env = tmp_path / ".env"
    env.write_text(
        "BOOTSTRAPPER_PORT_LAYOUT_VERSION=2\n"
        "COMFYUI_MODEL_SET=sd15\n"
        "COMFYUI_USER_MODELS=epiCRealism-XL,v1-5-pruned-emaonly\n"
        "COMFYUI_CUSTOM_MODELS_FILE=/my/models.yaml\n"
    )
    apply_v3(env)
    lines = env.read_text().splitlines()
    assert lines.count("COMFYUI_CUSTOM_MODELS_FILE=/my/models.yaml") == 1
    assert not [line for line in lines if "/custom-models.yaml" in line]
    user_models = [line for line in lines if line.startswith("COMFYUI_USER_MODELS=")]
    assert user_models == [
        "COMFYUI_USER_MODELS="
        "epiCRealism-XL,v1-5-pruned-emaonly,vae-ft-mse-840000-ema-pruned"
    ]


def test_handles_crlf_line_endings(tmp_path):
    """Windows line endings — reuses pattern from migration_v2's test fixtures."""
    env = tmp_path / ".env"
    env.write_bytes(b"BOOTSTRAPPER_PORT_LAYOUT_VERSION=2\r\nCOMFYUI_MODEL_SET=minimal\r\n")
    apply_v3(env)
    text = env.read_text()
    assert "COMFYUI_USER_MODELS=v1-5-pruned-emaonly,vae-ft-mse-840000-ema-pruned" in text


def test_handles_fresh_env_no_old_var(tmp_path):
    """Cold start: no COMFYUI_MODEL_SET present; new vars added empty."""
    env = tmp_path / ".env"
    env.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=2\n")
    apply_v3(env)
    text = env.read_text()
    assert "COMFYUI_USER_MODELS=" in text


def test_no_op_on_missing_env_file(tmp_path):
    """Defense: function should not crash if .env doesn't exist."""
    env = tmp_path / "nonexistent.env"
    apply_v3(env)  # should not raise
    assert not env.exists()


# ── stamp_version ─────────────────────────────────────────────────────

def test_stamp_version_writes_3(tmp_path):
    p = tmp_path / ".env"
    p.write_text("BOOTSTRAPPER_PORT_LAYOUT_VERSION=2\n")
    stamp_v3(p)
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=3" in p.read_text()


def test_stamp_version_appends_when_absent(tmp_path):
    p = tmp_path / ".env"
    p.write_text("FOO=bar\n")
    stamp_v3(p)
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=3" in p.read_text()
