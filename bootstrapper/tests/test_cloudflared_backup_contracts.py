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
