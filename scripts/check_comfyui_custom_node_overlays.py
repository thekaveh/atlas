"""Install each ComfyUI node overlay into the reviewed AI-Dock runtime."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "services" / "comfyui" / "custom-node-locks"
IMAGE = (
    "ghcr.io/ai-dock/comfyui:v2-cpu-22.04-v0.2.7"
    "@sha256:b47cd16007c309ebbb78b85f87bbc69ac9f7f3fc7596607e81940eeb7dca2421"
)
LOCKS = ("instantid.txt", "gguf.txt")
PYTHON = "/opt/environments/python/comfyui/bin/python"


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
