from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .models import PostprocessParams
from .normalizer import normalize_glb


class GltfTransformError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "failed") -> None:
        super().__init__(message)
        self.kind = kind


def run_gltf_transform(input_path: Path, output_path: Path, params: PostprocessParams) -> None:
    binary = os.getenv("ASSET_WORKER_GLTF_TRANSFORM_BIN", "gltf-transform")
    timeout = float(os.getenv("ASSET_WORKER_TIMEOUT_SECONDS", "300"))
    normalized = output_path.with_suffix(".normalized.glb")
    if params.ktx2 and shutil.which("ktx") is None:
        # gltf-transform's KTX2 encoder shells out to KTX-Software, which the
        # image does not ship; fail with a clear 422 instead of "command failed".
        raise GltfTransformError(
            "ktx2 texture compression needs KTX-Software (`ktx`), which this "
            "image does not include; use ktx2=false (WebP) instead"
        )
    normalize_glb(input_path, normalized, params)

    try:
        subprocess.run(
            [binary, "inspect", str(normalized)], check=True, timeout=timeout
        )
        subprocess.run(
            [binary, "validate", str(normalized)], check=True, timeout=timeout
        )
        command = [
            binary,
            "optimize",
            str(normalized),
            str(output_path),
            "--instance",
            "--weld",
        ]
        ratio = params.effective_simplify_ratio
        # gltf-transform 4.5 defaults --simplify to true and --compress to
        # meshopt, so "not requested" must be spelled out as false.
        if ratio is not None:
            command.extend(["--simplify", "true", "--simplify-ratio", str(ratio)])
        else:
            command.extend(["--simplify", "false"])
        if params.draco:
            command.extend(["--compress", "draco"])
        elif params.meshopt:
            command.extend(["--compress", "meshopt"])
        else:
            command.extend(["--compress", "false"])
        if params.ktx2:
            command.extend(["--texture-compress", "ktx2"])
        else:
            command.extend(["--texture-compress", "webp"])
        subprocess.run(command, check=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise GltfTransformError(
            f"glTF transform timed out after {timeout:.0f}s", kind="timeout"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise GltfTransformError(
            f"glTF transform command failed with exit code {exc.returncode}"
        ) from exc
