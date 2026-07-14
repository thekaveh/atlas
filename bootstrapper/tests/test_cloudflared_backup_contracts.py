"""Credential and source-mode contracts for edge and backup runners."""

from __future__ import annotations

from pathlib import Path
import subprocess

import yaml

from core.config_parser import ConfigParser
from services.source_validator import SourceValidator


REPO = Path(__file__).resolve().parents[2]


def _validator(env_path: Path) -> SourceValidator:
    parser = ConfigParser(str(REPO))
    parser.env_file_path = env_path
    return SourceValidator(config_parser=parser)


def test_cloudflared_container_source_requires_token(env_with_overrides) -> None:
    missing = _validator(
        env_with_overrides(
            {"CLOUDFLARED_SOURCE": "container", "CLOUDFLARE_TUNNEL_TOKEN": ""}
        )
    )
    assert missing.validate_all_sources() is False
    assert any(
        "CLOUDFLARE_TUNNEL_TOKEN" in error
        for error in missing.get_validation_errors()
    )

    disabled = _validator(
        env_with_overrides(
            {"CLOUDFLARED_SOURCE": "disabled", "CLOUDFLARE_TUNNEL_TOKEN": ""}
        )
    )
    assert disabled.validate_all_sources() is True

    configured = _validator(
        env_with_overrides(
            {
                "CLOUDFLARED_SOURCE": "container",
                "CLOUDFLARE_TUNNEL_TOKEN": "configured-token",
            }
        )
    )
    assert configured.validate_all_sources() is True


def test_backup_compose_passes_source_to_runner() -> None:
    compose = yaml.safe_load(
        (REPO / "services/backup/compose.yml").read_text(encoding="utf-8")
    )
    assert compose["services"]["backup"]["environment"]["BACKUP_SOURCE"] == (
        "${BACKUP_SOURCE:-disabled}"
    )
    assert compose["services"]["backup"]["environment"][
        "BACKUP_COMMAND_TIMEOUT_SECONDS"
    ] == "${BACKUP_COMMAND_TIMEOUT_SECONDS:-900}"


def test_disabled_backup_runner_fails_before_bootstrap() -> None:
    entrypoint = REPO / "services/backup/init/scripts/entrypoint.sh"
    result = subprocess.run(
        ["sh", str(entrypoint), "/bin/true"],
        env={"PATH": "/usr/bin:/bin", "BACKUP_SOURCE": "disabled"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "BACKUP_SOURCE=container" in result.stderr

    script = entrypoint.read_text(encoding="utf-8")
    assert script.index('BACKUP_SOURCE:-disabled') < script.index("apk add")


def test_enabled_backup_runner_executes_requested_script(tmp_path) -> None:
    entrypoint = REPO / "services/backup/init/scripts/entrypoint.sh"
    fake_mc = tmp_path / "mc"
    fake_mc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_mc.chmod(0o755)
    command = tmp_path / "command.sh"
    command.write_text("exit 0\n", encoding="utf-8")

    result = subprocess.run(
        ["sh", str(entrypoint), str(command)],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "BACKUP_SOURCE": "container"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_restore_commands_use_manifest_owned_deadline(tmp_path) -> None:
    restore = REPO / "services/backup/init/scripts/restore-postgres.sh"
    trace = tmp_path / "trace"

    timeout = tmp_path / "timeout"
    timeout.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TRACE"\nshift 5\nexec "$@"\n',
        encoding="utf-8",
    )
    timeout.chmod(0o755)

    mc = tmp_path / "mc"
    mc.write_text(
        """#!/bin/sh
case "$1" in
  alias) exit 0 ;;
  ls) echo "[2026-07-14 00:00:00 EDT] 0B STANDARD 20260714_000000/" ;;
  cp) mkdir -p "$(dirname "$3")"; : > "$3" ;;
esac
""",
        encoding="utf-8",
    )
    mc.chmod(0o755)

    pg_restore = tmp_path / "pg_restore"
    pg_restore.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pg_restore.chmod(0o755)

    result = subprocess.run(
        ["sh", str(restore)],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "TRACE": str(trace),
            "BACKUP_COMMAND_TIMEOUT_SECONDS": "17",
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "secret",
            "SUPABASE_DB_NAME": "postgres",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = trace.read_text(encoding="utf-8")
    assert "17 mc alias set" in calls
    assert "17 mc ls" in calls
    assert "17 mc cp" in calls
    assert "17 env PGPASSWORD=secret pg_restore" in calls


def test_backup_timeout_must_be_a_positive_integer() -> None:
    restore = REPO / "services/backup/init/scripts/restore-postgres.sh"
    result = subprocess.run(
        ["sh", str(restore)],
        env={
            "PATH": "/usr/bin:/bin",
            "BACKUP_COMMAND_TIMEOUT_SECONDS": "0",
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "secret",
            "SUPABASE_DB_NAME": "postgres",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "positive integer" in result.stderr
