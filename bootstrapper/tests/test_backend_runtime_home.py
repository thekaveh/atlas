from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backend_image_provides_appuser_a_writable_home_contract() -> None:
    dockerfile = (ROOT / "services/backend/app/Dockerfile").read_text(encoding="utf-8")

    assert "--create-home" in dockerfile
    assert "--home-dir /home/appuser" in dockerfile
    assert "ENV HOME=/home/appuser" in dockerfile
    assert "XDG_CACHE_HOME=/home/appuser/.cache" in dockerfile
