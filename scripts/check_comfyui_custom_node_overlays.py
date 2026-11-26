"""Install each ComfyUI node overlay into the AI-Dock runtime the build uses."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "services" / "comfyui" / "custom-node-locks"
LOCKS = ("instantid.txt", "gguf.txt")
PYTHON = "/opt/environments/python/comfyui/bin/python"


def build_base_image(root: Path = ROOT) -> str:
    """Return the base reference services/comfyui/build/Dockerfile builds from.

    compose.yml passes BASE_IMAGE=${COMFYUI_IMAGE}, whose default lives in the
    comfyui manifest. The gate reads that same default so it checks the image
    the build pulls, not a separately pinned copy.
    """
    manifest = yaml.safe_load(
        (root / "services" / "comfyui" / "service.yml").read_text(encoding="utf-8")
    )
    for image in manifest["images"]:
        if image["var"] == "COMFYUI_IMAGE":
            return str(image["default"])
    raise RuntimeError("services/comfyui/service.yml does not declare COMFYUI_IMAGE")


IMAGE = build_base_image()


def overlay_commands(root: Path = ROOT) -> list[list[str]]:
    """Return exact production-equivalent install + compatibility commands."""
    lock_dir = root / "services" / "comfyui" / "custom-node-locks"
    shell = (
        "set -euo pipefail; "
        f"{PYTHON} -m pip install --no-cache-dir --no-deps --require-hashes "
        '-r "/locks/$1"; '
        f"{PYTHON} -m pip check"
    )
    return [
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--volume",
            f"{lock_dir.resolve()}:/locks:ro",
            "--entrypoint",
            "/bin/bash",
            IMAGE,
            "-lc",
            shell,
            "atlas-overlay-check",
            lock,
        ]
        for lock in LOCKS
    ]


def main() -> int:
    for command in overlay_commands():
        lock = command[-1]
        print(f"Checking {lock} against {IMAGE}", flush=True)
        subprocess.run(command, cwd=ROOT, check=True, timeout=600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
