"""Verify compiled runtime constraints still close over their input manifests."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RuntimeLock:
    requirements: str
    lock: str
    python_version: str


RUNTIME_LOCKS = (
    RuntimeLock(
        "services/backend/app/app/requirements.txt",
        "services/backend/app/app/requirements.lock",
        "3.12",
    ),
    RuntimeLock(
        "services/airflow/build/requirements.txt",
        "services/airflow/build/requirements.lock",
        "3.12",
    ),
    RuntimeLock(
        "services/jupyterhub/build/requirements.txt",
        "services/jupyterhub/build/requirements.lock",
        "3.11",
    ),
    RuntimeLock(
        "services/parakeet/provider/gpu/requirements.txt",
        "services/parakeet/provider/gpu/requirements.lock",
        "3.12",
    ),
)


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="atlas-runtime-locks-") as raw_tmp:
        temporary_dir = Path(raw_tmp)
        for index, spec in enumerate(RUNTIME_LOCKS):
            requirements = ROOT / spec.requirements
            lock = ROOT / spec.lock
            candidate = temporary_dir / f"{index}.lock"
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
                    "x86_64-manylinux_2_28",
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
            )
            if result.returncode != 0:
                failures.append(
                    f"{spec.lock}: resolution failed\n{result.stderr.strip()}"
                )
                continue
            if candidate.read_bytes() != lock.read_bytes():
                failures.append(
                    f"{spec.lock}: stale; re-run uv pip compile for {spec.requirements}"
                )
                continue
            print(f"PASS {spec.lock}")

    if failures:
        print("\n".join(f"FAIL {failure}" for failure in failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
