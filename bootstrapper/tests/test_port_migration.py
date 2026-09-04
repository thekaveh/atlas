"""Port-layout v0 → v1 migration tests."""

from __future__ import annotations

from pathlib import Path
import pytest


def _write_env(tmp_path: Path, contents: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(contents)
    return env


def test_all_defaults_get_rewritten(tmp_path):
    """A .env at all v0 defaults rewrites every port to its v1 slot."""
    from services.migrations.migration_v1 import apply, V0_OFFSETS
    new_defaults = {var: 63000 + i + 100 for i, var in enumerate(V0_OFFSETS)}
    env_path = _write_env(
        tmp_path,
        "\n".join(f"{var}={63000 + off}" for var, off in V0_OFFSETS.items()) + "\n",
    )
    result = apply(env_path, new_defaults, base_port=63000)
    assert set(result.rewritten.keys()) == set(V0_OFFSETS.keys())
    assert result.preserved == []
    assert result.backup_path is not None and result.backup_path.is_file()


def test_customized_port_is_preserved(tmp_path):
    """User-customized port is reported in 'preserved' and not rewritten."""
    from services.migrations.migration_v1 import apply
    env_path = _write_env(tmp_path, "LITELLM_PORT=54321\n")
    new_defaults = {"LITELLM_PORT": 63030}
    result = apply(env_path, new_defaults, base_port=63000)
    assert "LITELLM_PORT" not in result.rewritten
    assert "LITELLM_PORT" in result.preserved
    assert "LITELLM_PORT=54321" in env_path.read_text()


def test_sentinel_already_at_v1_no_migration(tmp_path):
    """needs_migration() is False when the sentinel is already 1."""
    from services.migrations.migration_v1 import needs_migration
    env_path = _write_env(tmp_path, "BOOTSTRAPPER_PORT_LAYOUT_VERSION=1\nLITELLM_PORT=63012\n")
    assert needs_migration(env_path) is False


def test_missing_env_skips(tmp_path):
    """A missing .env returns MigrationResult with no changes and no backup."""
    from services.migrations.migration_v1 import apply, needs_migration
    env_path = tmp_path / ".env"
    assert needs_migration(env_path) is False
    result = apply(env_path, {"LITELLM_PORT": 63030}, base_port=63000)
    assert result.rewritten == {}
    assert result.backup_path is None


def test_idempotency(tmp_path):
    """Running apply() twice in a row is a no-op the second time."""
    from services.migrations.migration_v1 import apply, stamp_version
    env_path = _write_env(tmp_path, "LITELLM_PORT=63012\n")
    new_defaults = {"LITELLM_PORT": 63030}
    apply(env_path, new_defaults, base_port=63000)
    stamp_version(env_path, 1)
    # Second pass with already-rewritten values: nothing matches the V0 expected_old.
    result2 = apply(env_path, new_defaults, base_port=63000)
    assert result2.rewritten == {}


def test_stamp_version_appends_when_missing(tmp_path):
    """stamp_version() adds the sentinel when not present."""
    from services.migrations.migration_v1 import stamp_version
    env_path = _write_env(tmp_path, "LITELLM_PORT=63012\n")
    stamp_version(env_path, 1)
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=1" in env_path.read_text()


# ─── I14 regression — inline comments survive the rewrite ───────────

def test_apply_preserves_inline_comment_on_rewrite(tmp_path):
    """User customization like ``LITELLM_PORT=63012  # my label`` must
    keep its trailing comment after the migration. Previously the
    comment was lost when the value was rewritten."""
    from services.migrations.migration_v1 import apply
    env_path = _write_env(tmp_path, "LITELLM_PORT=63012  # my custom label\n")
    result = apply(env_path, {"LITELLM_PORT": 63030}, base_port=63000)
    rewritten = env_path.read_text()
    assert "LITELLM_PORT=63030" in rewritten
    assert "# my custom label" in rewritten
    assert "LITELLM_PORT" in result.rewritten


def test_apply_preserves_inline_comment_when_value_preserved(tmp_path):
    """If the value isn't rewritten (user customized it), the whole
    line including its comment must come through unchanged."""
    from services.migrations.migration_v1 import apply
    env_path = _write_env(tmp_path, "LITELLM_PORT=54321  # hand-picked\n")
    apply(env_path, {"LITELLM_PORT": 63030}, base_port=63000)
    assert env_path.read_text() == "LITELLM_PORT=54321  # hand-picked\n"


# ─── M12 regression — tolerant sentinel parser ──────────────────────

def test_needs_migration_recognizes_spaces_around_equals(tmp_path):
    """``VAR = 1`` (with spaces) must count as the sentinel — otherwise
    a hand-edited .env re-runs the migration on every invocation."""
    from services.migrations.migration_v1 import needs_migration
    env_path = _write_env(tmp_path, "BOOTSTRAPPER_PORT_LAYOUT_VERSION = 1\n")
    assert needs_migration(env_path) is False


def test_needs_migration_recognizes_quoted_value(tmp_path):
    """Quoted sentinel ``VAR="1"`` also counts."""
    from services.migrations.migration_v1 import needs_migration
    env_path = _write_env(tmp_path, 'BOOTSTRAPPER_PORT_LAYOUT_VERSION="1"\n')
    assert needs_migration(env_path) is False


def test_needs_migration_recognizes_crlf(tmp_path):
    """CRLF line endings (Windows-edited .env) shouldn't confuse the parser."""
    from services.migrations.migration_v1 import needs_migration
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"BOOTSTRAPPER_PORT_LAYOUT_VERSION=1\r\nLITELLM_PORT=63030\r\n")
    assert needs_migration(env_path) is False


def test_stamp_version_updates_tolerant_existing(tmp_path):
    """stamp_version() must find a hand-edited ``VAR = 0`` and update
    it in place rather than appending a duplicate."""
    from services.migrations.migration_v1 import stamp_version
    env_path = _write_env(tmp_path, "BOOTSTRAPPER_PORT_LAYOUT_VERSION = 0\n")
    stamp_version(env_path, 1)
    text = env_path.read_text()
    assert text.count("BOOTSTRAPPER_PORT_LAYOUT_VERSION") == 1
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=1" in text


# ─── I8 regression — --no-port-migrate must NOT stamp the sentinel ──

def test_run_port_migration_skip_does_not_stamp(tmp_path, monkeypatch):
    """C2 + I8: ``run_port_migration(no_port_migrate=True)`` skips the
    rewrite AND does not stamp the sentinel, so the next run still
    sees ``needs_migration() == True`` and re-prompts."""
    from services.migrations.migration_v1 import needs_migration

    # Drop a .env at the v0 defaults so the migration is "pending".
    real_root = Path(__file__).resolve().parent.parent.parent
    env_path = tmp_path / ".env"
    env_path.write_text("BASE_PORT=63000\nLITELLM_PORT=63012\n")

    monkeypatch.setenv("ATLAS_ENV_FILE", str(env_path))
    # Build a starter; tear down anything that hits the network.
    from start import AtlasStarter
    starter = AtlasStarter()
    # Override env path so we don't touch the real repo .env.
    starter.config_parser.env_file_path = env_path

    assert needs_migration(env_path) is True
    starter.run_port_migration(no_port_migrate=True)
    # Sentinel must NOT have been stamped — next run re-prompts.
    assert needs_migration(env_path) is True
    # And the original port value is untouched.
    assert "LITELLM_PORT=63012" in env_path.read_text()


def test_run_port_migration_normal_run_stamps_and_rewrites(tmp_path, monkeypatch):
    """Companion to the I8 test: when not skipped, run_port_migration
    rewrites the ports AND stamps the sentinel so the next call is a
    no-op."""
    from services.migrations.migration_v1 import needs_migration

    env_path = tmp_path / ".env"
    env_path.write_text("BASE_PORT=63000\nLITELLM_PORT=63012\n")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env_path))

    from start import AtlasStarter
    starter = AtlasStarter()
    starter.config_parser.env_file_path = env_path

    assert needs_migration(env_path) is True
    starter.run_port_migration(no_port_migrate=False)
    assert needs_migration(env_path) is False


# ─── M11 — edge cases in the rewrite path ───────────────────────────


def test_apply_handles_leading_whitespace(tmp_path):
    """``  LITELLM_PORT=63012`` (leading spaces on the line) is still
    recognized as the v0 default and rewritten to the v1 slot.

    Documented behavior: the rewritten line is normalized — the leading
    whitespace is dropped because ``apply()`` reconstructs the line from
    ``f"{key}={new_value}{comment_tail}{eol}"``.
    """
    from services.migrations.migration_v1 import apply
    env_path = _write_env(tmp_path, "  LITELLM_PORT=63012\n")
    result = apply(env_path, {"LITELLM_PORT": 63030}, base_port=63000)
    assert "LITELLM_PORT" in result.rewritten
    text = env_path.read_text()
    assert "LITELLM_PORT=63030\n" in text
    # Normalization: no leading spaces survive on the rewritten line.
    assert "  LITELLM_PORT=" not in text


def test_apply_handles_quoted_value(tmp_path):
    """``LITELLM_PORT="63012"`` is treated as user customization (the
    quoted form does not match the bare v0 default) and is preserved.

    Documented behavior: ``apply()`` only rewrites when the stripped
    value EQUALS the v0 default string; the surrounding quotes make
    ``"63012"`` not equal to ``63012``, so the line is left alone and
    the key shows up in ``preserved``.
    """
    from services.migrations.migration_v1 import apply
    env_path = _write_env(tmp_path, 'LITELLM_PORT="63012"\n')
    result = apply(env_path, {"LITELLM_PORT": 63030}, base_port=63000)
    assert "LITELLM_PORT" not in result.rewritten
    assert "LITELLM_PORT" in result.preserved
    assert env_path.read_text() == 'LITELLM_PORT="63012"\n'


def test_apply_handles_crlf_line_endings(tmp_path):
    """CRLF-terminated .env (Windows-edited checkouts) is migrated
    successfully — the v0 default is still recognized and rewritten.

    Documented behavior: Python's ``read_text()`` normalizes ``\\r\\n``
    to ``\\n`` on read, so the resulting file is written back with LF
    endings (effectively a one-shot CRLF→LF normalization on the
    rewritten line). The test verifies the rewrite happened rather
    than asserting CRLF survives.
    """
    from services.migrations.migration_v1 import apply
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"LITELLM_PORT=63012\r\n")
    result = apply(env_path, {"LITELLM_PORT": 63030}, base_port=63000)
    assert "LITELLM_PORT" in result.rewritten
    rewritten = env_path.read_bytes()
    assert b"LITELLM_PORT=63030" in rewritten


def test_apply_writes_backup_before_overwrite(tmp_path):
    """A timestamped backup of the original file must exist after apply(),
    and its contents must match the *pre-migration* .env."""
    from services.migrations.migration_v1 import apply
    original = "LITELLM_PORT=63012\n# some user note\n"
    env_path = _write_env(tmp_path, original)
    result = apply(env_path, {"LITELLM_PORT": 63030}, base_port=63000)
    assert result.backup_path is not None
    assert result.backup_path.is_file()
    assert result.backup_path.read_text() == original
    # The live file has been rewritten — backup ≠ current.
    assert env_path.read_text() != original
    # Backup name is version-stamped so it can't collide with v2/v3 backups in a
    # full migration chain.
    assert ".backup.v1." in result.backup_path.name


def test_apply_backup_inherits_restrictive_mode(tmp_path):
    """Regression: the v1 backup must inherit the source .env's mode. By the
    time migrations run .env holds generated secrets, so a user-chmod'd 0600
    .env must not be backed up at the umask default (0644) — v3 clamped the
    mode but v1 did not."""
    import os
    import stat
    from services.migrations.migration_v1 import apply
    env_path = _write_env(tmp_path, "LITELLM_PORT=63012\n")
    os.chmod(env_path, 0o600)
    result = apply(env_path, {"LITELLM_PORT": 63030}, base_port=63000)
    assert result.backup_path is not None
    mode = stat.S_IMODE(os.stat(result.backup_path).st_mode)
    assert mode == 0o600, f"backup mode {oct(mode)} leaked (expected 0o600)"


# ─── I15 regression — ATLAS_ENV_FILE override honored ───────────────

def test_run_port_migration_honors_atlas_env_file(tmp_path, monkeypatch):
    """The helper must operate on ``self.config_parser.env_file_path``
    (which honors ATLAS_ENV_FILE), not a hardcoded ``../.env``."""
    custom_env = tmp_path / "custom.env"
    custom_env.write_text("BASE_PORT=63000\nLITELLM_PORT=63012\n")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(custom_env))
    repo_env = Path(__file__).resolve().parents[2] / ".env"
    repo_env_snapshot = repo_env.read_bytes() if repo_env.is_file() else b""

    from start import AtlasStarter
    starter = AtlasStarter()
    # Re-resolve since env var was set after construction in some flows.
    starter.config_parser.env_file_path = custom_env

    starter.run_port_migration(no_port_migrate=False)
    text = custom_env.read_text()
    # The current terminal migration completed against the custom file. This
    # test cares about path resolution, so avoid copying its version number.
    from services.migrations.migration_v5 import needs_migration as needs_v5

    assert needs_v5(custom_env) is False
    assert "WEAVIATE_ENABLE_MODULES=backup-filesystem" in text
    # Real repo .env (if present) was not touched.
    repo_env = Path(__file__).resolve().parents[2] / ".env"
    if repo_env.is_file():
        assert repo_env_snapshot == repo_env.read_bytes()
# ─── blank-sentinel tolerance (pass 47) ─────────────────────────────

def test_blank_sentinel_counts_as_unmigrated(tmp_path):
    """``BOOTSTRAPPER_PORT_LAYOUT_VERSION=`` (blank) must behave like a
    missing sentinel — migrations run rather than crash or skip."""
    from services.migrations.migration_v1 import needs_migration
    env_path = _write_env(
        tmp_path, "BOOTSTRAPPER_PORT_LAYOUT_VERSION=\nLITELLM_PORT=63012\n"
    )
    assert needs_migration(env_path) is True


def test_stamp_replaces_blank_sentinel_in_place(tmp_path):
    """Stamping over a blank sentinel rewrites that line — no duplicate
    sentinel lines (previously the blank line failed the digit-only
    regex and a second line was appended)."""
    from services.migrations.migration_v1 import stamp_version
    env_path = _write_env(
        tmp_path, "BOOTSTRAPPER_PORT_LAYOUT_VERSION=\nLITELLM_PORT=63012\n"
    )
    stamp_version(env_path, 1)
    text = env_path.read_text()
    assert text.count("BOOTSTRAPPER_PORT_LAYOUT_VERSION") == 1
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=1" in text
