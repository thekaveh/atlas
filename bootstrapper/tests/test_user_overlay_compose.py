"""Phase 1 / P1-1: downstream `services/_user/<name>/compose.yml` overlays are
merged into the `docker compose` invocation so they actually launch.

These exercise the pure arg-builder `DockerManager._compose_file_args()` against
a temp root — no Docker required, so they run in CI.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.docker_manager import DockerManager


def _mk(tmp_path: Path) -> DockerManager:
    # A minimal repo root: docker-compose.yml + a services/ dir.
    (tmp_path / "docker-compose.yml").write_text("include: []\n", encoding="utf-8")
    (tmp_path / "services").mkdir()
    return DockerManager(str(tmp_path))


def test_no_overlay_returns_the_base_file(tmp_path):
    # Explicit base file even without overlays (an inherited COMPOSE_FILE is ignored).
    dm = _mk(tmp_path)
    assert dm._compose_file_args() == ['-f', 'docker-compose.yml']


def test_no_user_dir_returns_the_base_file(tmp_path):
    dm = _mk(tmp_path)
    # services/ exists but no _user/ subdir
    assert not (tmp_path / "services" / "_user").exists()
    assert dm._compose_file_args() == ['-f', 'docker-compose.yml']


def test_single_overlay_lists_base_then_overlay(tmp_path):
    dm = _mk(tmp_path)
    demo = tmp_path / "services" / "_user" / "demo"
    demo.mkdir(parents=True)
    (demo / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert dm._compose_file_args() == [
        "-f", "docker-compose.yml",
        "-f", "services/_user/demo/compose.yml",
    ]


def test_multiple_overlays_sorted(tmp_path):
    dm = _mk(tmp_path)
    for name in ("zeta", "alpha"):
        d = tmp_path / "services" / "_user" / name
        d.mkdir(parents=True)
        (d / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert dm._compose_file_args() == [
        "-f", "docker-compose.yml",
        "-f", "services/_user/alpha/compose.yml",
        "-f", "services/_user/zeta/compose.yml",
    ]


def test_user_dir_without_compose_is_ignored(tmp_path):
    # A _user/<name>/ folder with only a service.yml (no compose.yml) contributes nothing.
    dm = _mk(tmp_path)
    d = tmp_path / "services" / "_user" / "manifest-only"
    d.mkdir(parents=True)
    (d / "service.yml").write_text("name: manifest-only\n", encoding="utf-8")
    assert dm._compose_file_args() == ['-f', 'docker-compose.yml']


def test_base_file_is_explicit_so_an_inherited_compose_file_is_ignored(tmp_path, monkeypatch):
    """Without -f, Compose follows COMPOSE_FILE from the shell or from .env
    and loads another project's model under Atlas's project name."""
    monkeypatch.setenv("COMPOSE_FILE", "../parent/docker-compose.yml")
    dm = _mk(tmp_path)
    assert dm._compose_file_args()[:2] == ['-f', 'docker-compose.yml']
