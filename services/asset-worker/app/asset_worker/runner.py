from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .models import PostprocessParams
from .normalizer import normalize_glb


def run_gltf_transform(input_path: Path, output_path: Path, params: PostprocessParams) -> None:
    binary = os.getenv("ASSET_WORKER_GLTF_TRANSFORM_BIN", "gltf-transform")
    normalized = output_path.with_suffix(".normalized.glb")
    normalize_glb(input_path, normalized, params)

    subprocess.run([binary, "inspect", str(normalized)], check=True)
    subprocess.run([binary, "validate", str(normalized)], check=True)

    command = [
        binary,
        "optimize",
        str(normalized),
        str(output_path),
        "--instance",
        "--weld",
    ]
    ratio = params.effective_simplify_ratio
    if ratio is not None:
        command.extend(["--simplify", "--simplify-ratio", str(ratio)])
    if params.draco:
        command.extend(["--compress", "draco"])
    elif params.meshopt:
        command.extend(["--compress", "meshopt"])
    if params.ktx2:
        command.extend(["--texture-compress", "ktx2"])
    else:
        command.extend(["--texture-compress", "webp"])

    subprocess.run(command, check=True)
