"""Verify compiled runtime constraints still close over their input manifests."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class RuntimeLock:
    requirements: str
    lock: str
    python_version: str
    platforms: tuple[str, ...] = ("x86_64-manylinux_2_28",)


RUNTIME_LOCKS = (
    RuntimeLock(
        "services/backend/app/app/requirements.txt",
        "services/backend/app/app/requirements-locked.txt",
        "3.12",
    ),
    RuntimeLock(
        "services/airflow/build/requirements.txt",
        "services/airflow/build/requirements-locked.txt",
        "3.12",
    ),
    RuntimeLock(
        "services/jupyterhub/build/requirements.txt",
        "services/jupyterhub/build/requirements-locked.txt",
        "3.11",
        ("x86_64-manylinux_2_28", "aarch64-manylinux_2_28"),
    ),
    RuntimeLock(
        "services/parakeet/provider/gpu/requirements.txt",
        "services/parakeet/provider/gpu/requirements-locked.txt",
        "3.12",
    ),
    RuntimeLock(
        "services/asset-baker/app/requirements.txt",
        "services/asset-baker/app/requirements-locked.txt",
        "3.12",
    ),
    RuntimeLock(
        "services/asset-worker/app/requirements.txt",
        "services/asset-worker/app/requirements-locked.txt",
        "3.12",
        ("x86_64-manylinux_2_28", "aarch64-manylinux_2_28"),
    ),
    RuntimeLock(
        "services/docling/provider/gpu/requirements.txt",
        "services/docling/provider/gpu/requirements-locked.txt",
        "3.12",
    ),
    RuntimeLock(
        "services/docling/provider/adapter/requirements.txt",
        "services/docling/provider/adapter/requirements-locked.txt",
        "3.12",
    ),
    RuntimeLock(
        "services/mcp-servers/runtime/requirements.txt",
        "services/mcp-servers/runtime/requirements-locked.txt",
        "3.12",
        ("x86_64-manylinux_2_28", "aarch64-manylinux_2_28"),
    ),
)


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="atlas-runtime-locks-") as raw_tmp:
        temporary_dir = Path(raw_tmp)
        for index, spec in enumerate(RUNTIME_LOCKS):
            requirements = ROOT / spec.requirements
            lock = ROOT / spec.lock
            for platform in spec.platforms:
                candidate = temporary_dir / f"{index}-{platform}.lock"
                try:
                    result = subprocess.run(
                        [
                            "uv",
                            "pip",
                            "compile",
                            str(requirements),
                            "--constraint",
                            str(lock),
                            "--python-version",
                            spec.python_version,
                            "--python-platform",
                            platform,
                            "--output-file",
                            str(candidate),
                            "--no-emit-index-url",
                            "--no-annotate",
                            "--no-header",
                            "--quiet",
                        ],
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=COMMAND_TIMEOUT_SECONDS,
                    )
                except subprocess.TimeoutExpired:
                    failures.append(
                        f"{spec.lock} ({platform}): resolution timed out after "
                        f"{COMMAND_TIMEOUT_SECONDS} seconds"
                    )
                    continue
                if result.returncode != 0:
                    failures.append(
                        f"{spec.lock} ({platform}): resolution failed "
                        f"(uv exited {result.returncode}; subprocess output redacted)"
                    )
                    continue
                if candidate.read_bytes() != lock.read_bytes():
                    failures.append(
                        f"{spec.lock} ({platform}): stale; re-run uv pip compile "
                        f"for {spec.requirements}"
                    )
                    continue
                print(f"PASS {spec.lock} ({platform})")

    if failures:
        print("\n".join(f"FAIL {failure}" for failure in failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
