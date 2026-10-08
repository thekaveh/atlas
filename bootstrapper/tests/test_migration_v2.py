"""migration_v2: rewrite <SVC>_LOCALHOST_URL → <SVC>_LOCALHOST_PORT in .env.

Triggered when BOOTSTRAPPER_PORT_LAYOUT_VERSION is < 2. For each of
the 7 legacy URL vars, extract the port via regex and append a
matching PORT entry; comment out the old URL line (don't delete) so a
mis-extraction is recoverable by hand."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.migrations.migration_v2 import (
    apply as apply_v2,
    needs_migration as needs_v2,
    stamp_version as stamp_v2,
    URL_VAR_TO_PORT_VAR,
)


LEGACY_URL_VARS = list(URL_VAR_TO_PORT_VAR.keys())


def _write_env(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(content, encoding="utf-8")
    return p


def test_default_port_url_rewrites_to_port_var(tmp_path):
    """A user with the default URL like COMFYUI_LOCALHOST_URL=http://...:8000
    gets COMFYUI_LOCALHOST_PORT=8000 appended; old URL is commented out."""
    p = _write_env(tmp_path,
        "COMFYUI_LOCALHOST_URL=http://host.docker.internal:8000\n"
        "OTHER=unrelated\n"
    )
    apply_v2(p)
    out = p.read_text()
    assert "COMFYUI_LOCALHOST_PORT=8000" in out
    assert "# COMFYUI_LOCALHOST_URL=" in out, (
        f"old URL line should be commented out, not deleted. .env:\n{out}"
    )


def test_quoted_url_value_is_migrated(tmp_path):
    """Quoted URL values (parse_env_file is quote-aware) must migrate too."""
    p = _write_env(tmp_path,
        'COMFYUI_LOCALHOST_URL="http://host.docker.internal:9999"\n'
    )
    apply_v2(p)
    out = p.read_text()
    assert "COMFYUI_LOCALHOST_PORT=9999" in out
    assert out.lstrip().startswith("# COMFYUI_LOCALHOST_URL=")


def test_custom_port_preserved(tmp_path):
    """User's customized port survives end-to-end."""
    p = _write_env(tmp_path,
        "DOCLING_LOCALHOST_URL=http://host.docker.internal:9876\n"
    )
    apply_v2(p)
    assert "DOCLING_LOCALHOST_PORT=9876" in p.read_text()


def test_custom_hostname_dropped_with_warning(tmp_path, capsys):
    """Non-default hostname is dropped; the port is still extracted;
    a warning is printed so the user sees it."""
    p = _write_env(tmp_path,
        "OPENCLAW_LOCALHOST_URL=http://192.168.1.10:9000\n"
    )
    apply_v2(p)
    out = p.read_text()
    assert "OPENCLAW_LOCALHOST_PORT=9000" in out
    captured = capsys.readouterr()
    assert "OPENCLAW_LOCALHOST_URL" in (captured.out + captured.err)
    assert "192.168.1.10" in (captured.out + captured.err)


def test_empty_url_no_port_emitted(tmp_path):
    """Empty URL value: comment the line, do NOT emit a PORT entry —
    service will use the manifest default at compose-render time."""
    p = _write_env(tmp_path, "HERMES_LOCALHOST_URL=\n")
    apply_v2(p)
    out = p.read_text()
    assert "HERMES_LOCALHOST_PORT" not in out
    assert "# HERMES_LOCALHOST_URL=" in out


def test_url_without_port_skipped(tmp_path):
    """A malformed URL with no :port: skip cleanly, comment out, warn."""
    p = _write_env(tmp_path,
        "HERMES_LOCALHOST_URL=http://host.docker.internal\n"
    )
    apply_v2(p)
    out = p.read_text()
    assert "HERMES_LOCALHOST_PORT" not in out
    assert "# HERMES_LOCALHOST_URL=" in out


def test_url_var_absent_no_change(tmp_path):
    """When the URL var is missing entirely, no PORT var added; .env
    rest unchanged."""
    p = _write_env(tmp_path, "OTHER=unrelated\n")
    before = p.read_text()
    apply_v2(p)
    after = p.read_text()
    assert "LOCALHOST_PORT" not in after
    assert "OTHER=unrelated" in after


def test_idempotent_when_already_migrated(tmp_path):
    """Re-running on a .env that already has both URL (commented) and
    PORT is a no-op."""
    p = _write_env(tmp_path,
        "# COMFYUI_LOCALHOST_URL=http://host.docker.internal:8000\n"
        "COMFYUI_LOCALHOST_PORT=8000\n"
    )
    before = p.read_text()
    apply_v2(p)
    assert p.read_text() == before


def test_needs_migration_false_when_sentinel_at_2(tmp_path):
    p = _write_env(tmp_path, "BOOTSTRAPPER_PORT_LAYOUT_VERSION=2\n")
    assert needs_v2(p) is False


def test_needs_migration_true_when_sentinel_at_1(tmp_path):
    p = _write_env(tmp_path, "BOOTSTRAPPER_PORT_LAYOUT_VERSION=1\n")
    assert needs_v2(p) is True


def test_needs_migration_true_when_sentinel_absent(tmp_path):
    p = _write_env(tmp_path, "FOO=bar\n")
    assert needs_v2(p) is True


def test_stamp_version_writes_2(tmp_path):
    p = _write_env(tmp_path, "BOOTSTRAPPER_PORT_LAYOUT_VERSION=1\n")
    stamp_v2(p)
    assert "BOOTSTRAPPER_PORT_LAYOUT_VERSION=2" in p.read_text()


def test_url_var_to_port_var_covers_seven_legacy_services():
    """Coverage check: the mapping has exactly the 7 services that had
    LOCALHOST_URL vars before T6."""
    expected_urls = {
        "COMFYUI_LOCALHOST_URL", "DOCLING_LOCALHOST_URL", "HERMES_LOCALHOST_URL",
        "OPENCLAW_LOCALHOST_URL", "PARAKEET_LOCALHOST_URL",
        "WHISPER_CPP_LOCALHOST_URL", "CHATTERBOX_LOCALHOST_URL",
    }
    assert set(URL_VAR_TO_PORT_VAR.keys()) == expected_urls


def test_url_var_to_port_var_pairs_consistently():
    """Each URL var maps to the same name with URL→PORT replacement."""
    for url_var, port_var in URL_VAR_TO_PORT_VAR.items():
        expected = url_var.replace("_LOCALHOST_URL", "_LOCALHOST_PORT")
        assert port_var == expected, f"{url_var} → {port_var} (expected {expected})"


def test_apply_backs_up_and_preserves_mode(tmp_path):
    """Regression: v2 must back up the pre-migration .env (version-stamped,
    mode-clamped) and write atomically, preserving the source mode. It was
    previously the only migration that rewrote .env in place with no backup and
    no mode clamp — an interrupted write could truncate it, and the standalone
    sentinel=1 case had no rollback."""
    import os
    import stat

    p = _write_env(
        tmp_path,
        "COMFYUI_LOCALHOST_URL=http://host.docker.internal:11434\n",
    )
    os.chmod(p, 0o600)
    apply_v2(p)

    # Rewrite happened and mode survived the atomic tmp+replace.
    assert "COMFYUI_LOCALHOST_PORT=11434" in p.read_text()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600

    # A version-stamped backup with the pre-v2 content + clamped mode exists.
    backups = list(tmp_path.glob(".env.backup.v2.*"))
    assert len(backups) == 1
    assert "COMFYUI_LOCALHOST_URL=http://host.docker.internal:11434" in backups[0].read_text()
    assert stat.S_IMODE(os.stat(backups[0]).st_mode) == 0o600

    # No leftover tmp file.
    assert not list(tmp_path.glob(".env.v2.tmp"))


def test_apply_noop_creates_no_backup(tmp_path):
    """With no legacy URL vars, v2 must not rewrite or back up."""
    p = _write_env(tmp_path, "SOME_PORT=63000\n")
    apply_v2(p)
    assert not list(tmp_path.glob(".env.backup.v2.*"))


def test_exported_lines_are_migrated_and_respected(tmp_path):
    """`export KEY=` is KEY to the reader: an exported URL was never migrated
    (its port lost), and an exported PORT was shadowed by an appended one."""
    from core.config_parser import ConfigParser

    p = _write_env(tmp_path,
        "export HERMES_LOCALHOST_URL=http://host.docker.internal:9999\n"
        "OPENCLAW_LOCALHOST_URL=http://host.docker.internal:7777\n"
        "export OPENCLAW_LOCALHOST_PORT=5555\n"
    )
    apply_v2(p)
    parser = ConfigParser(str(tmp_path))
    parser.env_file_path = p
    env = parser.parse_env_file()
    assert env.get("HERMES_LOCALHOST_PORT") == "9999"
    assert "HERMES_LOCALHOST_URL" not in env  # commented out
    assert env.get("OPENCLAW_LOCALHOST_PORT") == "5555"
