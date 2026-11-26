"""Verify compiled runtime constraints still close over their input manifests."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scripts.bounded_subprocess import (
    CommandLaunchError,
    CommandOutputTooLarge,
    CommandTimedOut,
    DEFAULT_TIMEOUT_SECONDS,
    redacted_failure,
    run_bounded,
)


ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class RuntimeLock:
    requirements: str
    lock: str
    python_version: str
    platforms: tuple[str, ...] = ("x86_64-manylinux_2_28",)
    # uv resolves *-apple-darwin as macOS 13 unless MACOSX_DEPLOYMENT_TARGET says
    # otherwise. Set only where a pinned wheel needs a newer macOS; applied to
    # the apple-darwin platforms of this lock and no other.
    macos_target: str | None = None


@dataclass(frozen=True)
class UvRuntimeLock:
    project: str
    lock: str


RUNTIME_LOCKS = (
    RuntimeLock(
        "services/backend/app/app/requirements.txt",
        "services/backend/app/app/requirements-locked.txt",
        "3.12",
    ),
    RuntimeLock(
        "services/airflow/build/requirements.txt",
        "services/airflow/build/requirements-locked.txt",
        "3.13",
        ("x86_64-manylinux_2_28", "aarch64-manylinux_2_28"),
    ),
    RuntimeLock(
        "services/jupyterhub/build/requirements.txt",
        "services/jupyterhub/build/requirements-locked.txt",
        "3.13",
        ("x86_64-manylinux_2_28", "aarch64-manylinux_2_28"),
    ),
    RuntimeLock(
        "services/mlflow/build/requirements.txt",
        "services/mlflow/build/requirements-locked.txt",
        "3.11",  # ghcr.io/mlflow/mlflow:v3.17.2 runs Python 3.11 (was 3.10 before #1323)
        ("x86_64-manylinux_2_28", "aarch64-manylinux_2_28"),
    ),
    RuntimeLock(
        "services/parakeet/provider/gpu/requirements.txt",
        "services/parakeet/provider/gpu/requirements-locked.txt",
        "3.12",
    ),
    RuntimeLock(
        "services/parakeet/provider/mlx/requirements.txt",
        "services/parakeet/provider/mlx/requirements-locked.txt",
        "3.12",
        ("aarch64-apple-darwin",),
        macos_target="14.0",  # mlx 0.29.4 wheels are macosx_14_0 and newer
    ),
    RuntimeLock(
        "services/asset-baker/app/requirements.txt",
        "services/asset-baker/app/requirements-locked.txt",
        "3.12",
    ),
    RuntimeLock(
        "services/asset-baker/app/requirements-blender.txt",
        "services/asset-baker/app/requirements-blender-locked.txt",
        "3.11",
        ("x86_64-manylinux_2_28",),
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
        "services/speech-gate/requirements.txt",
        "services/speech-gate/requirements-locked.txt",
        "3.12",
    ),
    RuntimeLock(
        "services/mcp-servers/runtime/requirements.txt",
        "services/mcp-servers/runtime/requirements-locked.txt",
        "3.12",
        ("x86_64-manylinux_2_28", "aarch64-manylinux_2_28"),
    ),
    RuntimeLock(
        "services/requirements-init-locked.txt",
        "services/requirements-init-locked.txt",
        "3.12",
    ),
)

UV_RUNTIME_LOCKS = (
    UvRuntimeLock("bootstrapper", "bootstrapper/requirements-locked.txt"),
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
                env = None
                if spec.macos_target is not None and platform.endswith("-apple-darwin"):
                    env = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": spec.macos_target}
                try:
                    result = run_bounded(
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
                        env=env,
                        timeout_seconds=COMMAND_TIMEOUT_SECONDS,
                    )
                except CommandTimedOut:
                    failures.append(
                        f"{spec.lock} ({platform}): resolution timed out after "
                        f"{COMMAND_TIMEOUT_SECONDS} seconds"
                    )
                    continue
                except (CommandLaunchError, CommandOutputTooLarge):
                    failures.append(
                        f"{spec.lock} ({platform}): resolution could not "
                        "complete (subprocess details redacted)"
                    )
                    continue
                if result.returncode != 0:
                    failures.append(
                        redacted_failure(
                            f"{spec.lock} ({platform}): resolution",
                            result.returncode,
                        )
                    )
                    continue
                if candidate.read_bytes() != lock.read_bytes():
                    failures.append(
                        f"{spec.lock} ({platform}): stale; re-run uv pip compile "
                        f"for {spec.requirements}"
                    )
                    continue
                print(f"PASS {spec.lock} ({platform})")

        for index, spec in enumerate(UV_RUNTIME_LOCKS, start=len(RUNTIME_LOCKS)):
            candidate = temporary_dir / f"{index}-uv-export.lock"
            try:
                result = run_bounded(
                    [
                        "uv",
                        "export",
                        "--project",
                        str(ROOT / spec.project),
                        "--locked",
                        "--no-hashes",
                        "--no-emit-project",
                        "--no-dev",
                        "--no-header",
                        "--no-annotate",
                        "--output-file",
                        str(candidate),
                    ],
                    cwd=ROOT,
                    timeout_seconds=COMMAND_TIMEOUT_SECONDS,
                )
            except CommandTimedOut:
                failures.append(
                    f"{spec.lock}: uv export timed out after "
                    f"{COMMAND_TIMEOUT_SECONDS} seconds"
                )
                continue
            except (CommandLaunchError, CommandOutputTooLarge):
                failures.append(
                    f"{spec.lock}: uv export could not complete "
                    "(subprocess details redacted)"
                )
                continue
            if result.returncode != 0:
                failures.append(
                    redacted_failure(f"{spec.lock}: uv export", result.returncode)
                )
                continue
            lock = ROOT / spec.lock
            if candidate.read_bytes() != lock.read_bytes():
                failures.append(
                    f"{spec.lock}: stale; re-run uv export for {spec.project}"
                )
                continue
            print(f"PASS {spec.lock} (uv export)")

    if failures:
        print("\n".join(f"FAIL {failure}" for failure in failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
