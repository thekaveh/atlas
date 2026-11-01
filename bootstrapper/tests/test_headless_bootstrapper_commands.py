from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from tests.three_surface_test_utils import surface_text


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
REUSING_ATLAS = REPO_ROOT / "docs" / "operations" / "reusing-atlas.md"


def _write_env_pair(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        """# ────────────────────────────────────────────────────────
# Core
# ────────────────────────────────────────────────────────
PROJECT_NAME=atlas
EXISTING=value
NEW_FROM_EXAMPLE=default-value
BLANK_SECRET=password
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "PROJECT_NAME=atlas\nEXISTING=user-value\nBLANK_SECRET=\n",
        encoding="utf-8",
    )


def _patch_starter_paths(monkeypatch, tmp_path: Path) -> None:
    import start as start_module

    original_init = start_module.AtlasStarter.__init__

    def init_with_tmp_env(self):
        original_init(self)
        self.config_parser.root_dir = tmp_path
        self.config_parser.env_file_path = tmp_path / ".env"
        self.config_parser.env_example_path = tmp_path / ".env.example"
        self.docker_manager.root_dir = tmp_path
        self.docker_manager.config_parser.root_dir = tmp_path
        self.docker_manager.config_parser.env_file_path = tmp_path / ".env"
        self.docker_manager.config_parser.env_example_path = tmp_path / ".env.example"

    monkeypatch.setattr(start_module.AtlasStarter, "__init__", init_with_tmp_env)


def test_env_backfill_cli_is_headless_idempotent_and_reports_keys(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_env_pair(tmp_path)
    _patch_starter_paths(monkeypatch, tmp_path)

    first = CliRunner().invoke(start_module.main, ["env", "backfill"])

    assert first.exit_code == 0, first.output
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "EXISTING=user-value" in env_text
    assert "NEW_FROM_EXAMPLE=default-value" in env_text
    assert "BLANK_SECRET=password" in env_text
    assert "Core" in first.output
    assert "NEW_FROM_EXAMPLE" in first.output
    assert "BLANK_SECRET" in first.output

    after_first = env_text
    second = CliRunner().invoke(start_module.main, ["env", "backfill"])

    assert second.exit_code == 0, second.output
    assert (tmp_path / ".env").read_text(encoding="utf-8") == after_first
    assert "No env changes needed" in second.output


@pytest.mark.parametrize(
    ("env_text", "written"),
    [
        # Each case triggers exactly one backfill write, since the second
        # write would re-force 0o600 and mask a regression in the first.
        pytest.param(
            "PROJECT_NAME=atlas\nEXISTING=user-value\n"
            "NEW_FROM_EXAMPLE=user-value\nBLANK_SECRET=\n",
            "BLANK_SECRET=password",
            id="blank-fill",
        ),
        pytest.param(
            "PROJECT_NAME=atlas\nEXISTING=user-value\nBLANK_SECRET=password\n",
            "NEW_FROM_EXAMPLE=default-value",
            id="append",
        ),
    ],
)
def test_env_backfill_cli_leaves_env_owner_only(
    tmp_path, monkeypatch, env_text, written,
) -> None:
    """A .env copied from .env.example at 0o644 holds secrets, and
    `env backfill` runs without the start-time chmod."""
    import stat

    import start as start_module

    _write_env_pair(tmp_path)
    env = tmp_path / ".env"
    env.write_text(env_text, encoding="utf-8")
    env.chmod(0o644)
    _patch_starter_paths(monkeypatch, tmp_path)

    result = CliRunner().invoke(start_module.main, ["env", "backfill"])

    assert result.exit_code == 0, result.output
    assert written in env.read_text(encoding="utf-8").splitlines()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("env_text", "message"),
    [
        pytest.param(None, "Failed to fill blank values", id="blank-fill"),
        pytest.param(
            "PROJECT_NAME=atlas\nEXISTING=user-value\nBLANK_SECRET=password\n",
            "Failed to backfill",
            id="append",
        ),
    ],
)
def test_env_backfill_cli_exits_1_when_the_env_write_fails(
    tmp_path, monkeypatch, env_text, message,
) -> None:
    """reusing-atlas.md publishes exit 1 for a write failure; consumer CI
    must not get exit 0 after .env was left unwritten."""
    import start as start_module

    _write_env_pair(tmp_path)
    if env_text is not None:
        # No blank key, so only the append write runs.
        (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    _patch_starter_paths(monkeypatch, tmp_path)

    def refuse_write(*_args, **_kwargs):
        raise OSError("synthetic write refusal")

    monkeypatch.setattr(start_module, "atomic_write_text", refuse_write)
    before = (tmp_path / ".env").read_bytes()

    result = CliRunner().invoke(start_module.main, ["env", "backfill"])

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert (tmp_path / ".env").read_bytes() == before
    assert message in result.output


def test_compose_validate_cli_uses_user_overlays_and_reports_success(
    tmp_path, monkeypatch,
) -> None:
    import start as start_module

    _write_env_pair(tmp_path)
    overlay = tmp_path / "services" / "_user" / "demo"
    overlay.mkdir(parents=True)
    (overlay / "compose.yml").write_text(
        "services:\n  demo:\n    image: alpine:3.20\n",
        encoding="utf-8",
    )

    _patch_starter_paths(monkeypatch, tmp_path)

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(start_module.subprocess, "run", fake_run)
    monkeypatch.setattr("core.docker_manager.run_with_deadline", fake_run)
    monkeypatch.setattr(
        start_module.DockerManager,
        "detect_docker_compose_command",
        lambda self: "docker compose",
    )

    result = CliRunner().invoke(start_module.main, ["compose", "validate"])

    assert result.exit_code == 0, result.output
    assert "Compose config is valid" in result.output
    assert calls, "compose validate should run docker compose config"
    cmd = calls[0]
    assert cmd[-2:] == ["config", "-q"]
    assert "-f" in cmd
    assert "docker-compose.yml" in cmd
    assert "services/_user/demo/compose.yml" in cmd


def test_compose_validate_cli_names_missing_variable_and_service(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_env_pair(tmp_path)
    _patch_starter_paths(monkeypatch, tmp_path)

    class Result:
        returncode = 15
        stdout = ""
        stderr = (
            'time="2026-07-10T00:00:00Z" level=warning msg="The '
            '\\"ICEBERG_REST_INIT_IMAGE\\" variable is not set. '
            'Defaulting to a blank string."\\n'
            'service "iceberg-rest-init" has neither an image nor a build context specified: '
            "invalid compose project\\n"
        )

    fake_run = lambda *_args, **_kwargs: Result()
    monkeypatch.setattr(start_module.subprocess, "run", fake_run)
    monkeypatch.setattr("core.docker_manager.run_with_deadline", fake_run)
    monkeypatch.setattr(
        start_module.DockerManager,
        "detect_docker_compose_command",
        lambda self: "docker compose",
    )

    result = CliRunner().invoke(start_module.main, ["compose", "validate"])

    assert result.exit_code == 15
    assert "Compose config validation failed" in result.output
    assert "ICEBERG_REST_INIT_IMAGE" in result.output
    assert "iceberg-rest-init" in result.output


def test_consumer_upgrade_docs_name_headless_backfill_and_compose_validate() -> None:
    reusing = REUSING_ATLAS.read_text(encoding="utf-8")
    assert "git -C infra fetch" in reusing
    assert "./start.sh env backfill" in reusing
    assert "./start.sh compose validate" in reusing
    assert "Exit codes" in reusing

    for text in (
        surface_text("docs/operations/index.md", "site"),
        surface_text("docs/operations/index.md", "wiki"),
    ):
        assert "./start.sh env backfill" in text
        assert "./start.sh compose validate" in text
        assert "headless" in text.lower()


def test_env_backfill_without_an_env_file_says_so(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_env_pair(tmp_path)
    (tmp_path / ".env").unlink()
    _patch_starter_paths(monkeypatch, tmp_path)

    result = CliRunner().invoke(start_module.main, ["env", "backfill"])

    assert result.exit_code == 0  # fresh-clone CI recipes run this first
    assert "No env file" in result.output and "No env changes needed" not in result.output
    assert not (tmp_path / ".env").exists()
