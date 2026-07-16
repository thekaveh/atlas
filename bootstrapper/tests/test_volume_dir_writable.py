"""Regression: bootstrapper must heal root-owned bind-mount dirs.

Init containers (litellm-init, n8n-init, etc.) often run as root and
write into bind-mounted host directories. On macOS Docker Desktop and
some Linux setups this leaves the host directory root-owned (mode 755),
which blocks the NEXT container — running as root inside but unable to
write because the parent directory mode prevents user-level overlay.

The bootstrapper relaxes permissions before writing the config files
so the subsequent docker compose up doesn't hit ``PermissionError:
/litellm-config/config.yaml.tmp``.
"""

from __future__ import annotations

from pathlib import Path
import os
import shlex


def _make_starter(tmp_path: Path):
    (tmp_path / ".env").write_text("BASE_PORT=63000\n")
    (tmp_path / ".env.example").write_text("BASE_PORT=63000\n")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    from start import AtlasStarter

    starter = AtlasStarter()
    starter.config_parser.root_dir = tmp_path
    starter.config_parser.env_file_path = tmp_path / ".env"
    starter.config_parser.env_example_path = tmp_path / ".env.example"
    starter.root_dir = tmp_path
    return starter


def test_writable_directory_is_left_alone(tmp_path):
    """Already-writable dir: no log entry, no permission change."""
    starter = _make_starter(tmp_path)
    d = tmp_path / "volumes" / "litellm"
    d.mkdir(parents=True)
    original_mode = d.stat().st_mode & 0o777
    starter._ensure_volume_dir_writable(d)
    assert d.stat().st_mode & 0o777 == original_mode


def test_missing_directory_is_created(tmp_path):
    """Non-existent dir: created."""
    starter = _make_starter(tmp_path)
    d = tmp_path / "volumes" / "litellm"
    assert not d.exists()
    starter._ensure_volume_dir_writable(d)
    assert d.is_dir()


def test_unwritable_directory_gets_chmodded(tmp_path):
    """Dir without write perm for current user: chmodded to 777.

    Skip when running as root (root always has write access, regardless
    of mode, so the function takes the no-op branch).
    """
    if os.geteuid() == 0:
        return
    starter = _make_starter(tmp_path)
    d = tmp_path / "volumes" / "litellm"
    d.mkdir(parents=True)
    # Remove user write perm.
    d.chmod(0o555)
    assert not os.access(d, os.W_OK)
    starter._ensure_volume_dir_writable(d)
    assert os.access(d, os.W_OK), "directory should be writable after _ensure_volume_dir_writable"


def test_chmod_failure_never_deletes_existing_contents(tmp_path, monkeypatch):
    starter = _make_starter(tmp_path)
    directory = tmp_path / "Atlas Work" / "volumes" / "litellm"
    directory.mkdir(parents=True)
    payload = directory / "config.yaml"
    payload.write_text("must survive\n", encoding="utf-8")

    monkeypatch.setattr(os, "access", lambda path, mode: False)
    monkeypatch.setattr(Path, "chmod", lambda self, mode: (_ for _ in ()).throw(PermissionError("denied")))
    messages = []
    monkeypatch.setattr(
        starter.banner,
        "show_status_message",
        lambda message, level: messages.append((message, level)),
    )

    starter._ensure_volume_dir_writable(directory)

    assert payload.read_text(encoding="utf-8") == "must survive\n"
    assert shlex.quote(str(directory)) in messages[0][0]
