from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backend_image_provides_appuser_a_writable_home_contract() -> None:
    dockerfile = (ROOT / "services/backend/app/Dockerfile").read_text(encoding="utf-8")

    assert "--create-home" in dockerfile
    assert "--home-dir /home/appuser" in dockerfile
    assert "ENV HOME=/home/appuser" in dockerfile
    assert "XDG_CACHE_HOME=/home/appuser/.cache" in dockerfile


def test_backend_dev_reload_is_opt_in_and_gated() -> None:
    """#679: the production/consumer image runs plain uvicorn — the dev
    auto-reloader is opt-in via BACKEND_DEV_RELOAD, so host-side git churn in a
    bind-mounted plugin dir cannot restart or crash-loop the backend."""
    dockerfile = (ROOT / "services/backend/app/Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "services/backend/app/configure-backend.sh").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "services/backend/compose.yml").read_text(encoding="utf-8")

    # The default CMD must NOT hardcode the dev auto-reloader.
    cmd_line = next(
        line for line in dockerfile.splitlines() if line.strip().startswith("CMD [")
    )
    assert "uvicorn" in cmd_line
    assert "--reload" not in cmd_line

    # The entrypoint appends --reload only when BACKEND_DEV_RELOAD=true.
    assert '"${BACKEND_DEV_RELOAD:-false}" = "true"' in entrypoint
    assert 'exec "$@" --reload' in entrypoint

    # The knob is wired into the container environment (default false).
    assert "BACKEND_DEV_RELOAD: ${BACKEND_DEV_RELOAD:-false}" in compose
