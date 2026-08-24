"""Backfill must never pre-seed keys the migration chain owns.

Regression (pass-46 HIGH): ``backfill_missing_env_vars()`` runs BEFORE
``run_port_migration()`` in every flow. If it splices
``BOOTSTRAPPER_PORT_LAYOUT_VERSION=4`` from ``.env.example`` into a
legacy ``.env``, all four migrations silently skip — dropping the
user's ``COMFYUI_LOCALHOST_URL`` custom port and ``COMFYUI_MODEL_SET``
selection. Same family: seeding a ``*_LOCALHOST_PORT`` while the legacy
``*_LOCALHOST_URL`` is still present makes migration v2 keep the seeded
default and discard the URL's port; seeding the COMFYUI model vars
pre-empts v3's translation.
"""

from __future__ import annotations

from pathlib import Path


EXAMPLE = (
    "BASE_PORT=63000\n"
    "BOOTSTRAPPER_PORT_LAYOUT_VERSION=4\n"
    "COMFYUI_LOCALHOST_PORT=8188\n"
    "COMFYUI_USER_MODELS=\n"
    "COMFYUI_CUSTOM_MODELS_FILE=/custom-models.yaml\n"
)


def _make_starter(tmp_path: Path, env_body: str, example_body: str = EXAMPLE):
    (tmp_path / ".env").write_text(env_body)
    (tmp_path / ".env.example").write_text(example_body)
    from start import AtlasStarter

    starter = AtlasStarter()
    # root_dir stays at the real repo so migration v1 can load topology;
    # only the env paths point at tmp_path.
    starter.config_parser.env_file_path = tmp_path / ".env"
    starter.config_parser.env_example_path = tmp_path / ".env.example"
    return starter


def test_backfill_never_seeds_sentinel(tmp_path):
    starter = _make_starter(tmp_path, "BASE_PORT=63000\n")
    assert starter.backfill_missing_env_vars()
    out = (tmp_path / ".env").read_text()
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION" not in out


def test_backfill_leaves_blank_sentinel_blank(tmp_path):
    """Blank-value fill must also skip the sentinel."""
    starter = _make_starter(
        tmp_path, "BASE_PORT=63000\nBOOTSTRAPPER_PORT_LAYOUT_VERSION=\n"
    )
    assert starter.backfill_missing_env_vars()
    out = (tmp_path / ".env").read_text()
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=4" not in out


def test_backfill_defers_port_var_to_v2_when_legacy_url_present(tmp_path):
    starter = _make_starter(
        tmp_path,
        "BASE_PORT=63000\n"
        "COMFYUI_LOCALHOST_URL=http://host.docker.internal:9999\n",
    )
    assert starter.backfill_missing_env_vars()
    out = (tmp_path / ".env").read_text()
    assert "COMFYUI_LOCALHOST_PORT" not in out  # v2 will emit it with :9999


def test_backfill_still_seeds_port_var_without_legacy_url(tmp_path):
    """No over-exclusion: a modern .env still gets the PORT var."""
    starter = _make_starter(tmp_path, "BASE_PORT=63000\n")
    assert starter.backfill_missing_env_vars()
    out = (tmp_path / ".env").read_text()
    assert "COMFYUI_LOCALHOST_PORT=8188" in out


def test_backfill_defers_model_vars_to_v3_when_model_set_present(tmp_path):
    starter = _make_starter(
        tmp_path, "BASE_PORT=63000\nCOMFYUI_MODEL_SET=sdxl\n"
    )
    assert starter.backfill_missing_env_vars()
    out = (tmp_path / ".env").read_text()
    assert "COMFYUI_USER_MODELS" not in out
    assert "COMFYUI_CUSTOM_MODELS_FILE" not in out


def test_legacy_env_backfill_then_migrations_still_run(tmp_path):
    """End-to-end: backfill must not defeat the migration chain."""
    starter = _make_starter(
        tmp_path,
        "BASE_PORT=63000\n"
        "COMFYUI_LOCALHOST_URL=http://host.docker.internal:9999\n"
        "COMFYUI_MODEL_SET=sdxl\n",
    )
    assert starter.backfill_missing_env_vars()
    starter.run_port_migration(no_port_migrate=False)
    out = (tmp_path / ".env").read_text()
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=4" in out
    # v2 preserved the user's custom port instead of a seeded default.
    assert "COMFYUI_LOCALHOST_PORT=9999" in out
    # v3 removed the old enum (the commented v2 audit line is fine).
    active_lines = [
        line for line in out.splitlines() if not line.lstrip().startswith("#")
    ]
    assert not any(l.startswith("COMFYUI_MODEL_SET=") for l in active_lines)
    assert any(l.startswith("COMFYUI_USER_MODELS=") for l in active_lines)
