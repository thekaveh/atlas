"""The TrueForge compose fragment runs entry points the pinned image has (#1973)."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "services" / "trueforge" / "compose.yml"

# tfy.jfrog.io/tfy-images/trueforge:0.2.0-8b1d98e has WORKDIR /app and keeps the
# package under node_modules: its default command is
# `node node_modules/@truefoundry/trueforge/dist/main.js`, and /app/dist does
# not exist. Checked by listing the image on 2026-11-08.
PACKAGE_DIST = "node_modules/@truefoundry/trueforge/dist"


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_controller_runs_the_package_entry_point_inside_the_image() -> None:
    """The controller reuses the server image, so its entry point sits beside
    the server's main.js. `dist/controller-main.js` from /app crash-looped
    with "Cannot find module '/app/dist/controller-main.js'"."""
    services = _services()

    assert (
        services["trueforge-controller"]["command"],
        services["trueforge-controller"]["image"] == services["trueforge"]["image"],
        "command" in services["trueforge"],
    ) == (["node", f"{PACKAGE_DIST}/controller-main.js"], True, False)
