"""Verify compiled CI test constraints still close over their input manifests."""

from __future__ import annotations

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
class TestLock:
    requirements: str
    lock: str
    python_version: str = "3.12"
    platform: str = "x86_64-manylinux_2_28"


TEST_LOCKS = (
    TestLock(
        "services/backend/app/app/requirements-test.txt",
        "services/backend/app/app/requirements-test-locked.txt",
    ),
    TestLock(
        "services/mcp-servers/runtime/requirements-test.txt",
        "services/mcp-servers/runtime/requirements-test-locked.txt",
    ),
    TestLock(
        "services/asset-worker/app/requirements-test.txt",
        "services/asset-worker/app/requirements-test-locked.txt",
    ),
)


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, version = line.split(";")[0].strip().partition("==")
        if sep and name and not name.startswith("#"):
            pins[name.lower().replace("_", "-")] = version.strip()
    return pins


def runtime_drift(spec: TestLock) -> list[str]:
    """Packages the test lock pins at another version than the shipped
    runtime lock beside it. The tested closure must be the shipped one; the
    backend test lock carried newer openai/typer patches for months
    (2026-10-08 run, cycle 41)."""
    test_lock = ROOT / spec.lock
    runtime_lock = test_lock.with_name("requirements-locked.txt")
    if not runtime_lock.is_file():
        return []
    tested = _pins(test_lock)
    return [
        f"{spec.lock}: {name}=={tested[name]} but {runtime_lock.relative_to(ROOT)} ships {version}"
        for name, version in sorted(_pins(runtime_lock).items())
        if name in tested and tested[name] != version
    ]


def main() -> int:
    failures: list[str] = [drift for spec in TEST_LOCKS for drift in runtime_drift(spec)]
    with tempfile.TemporaryDirectory(prefix="atlas-test-locks-") as raw_tmp:
        temporary_dir = Path(raw_tmp)
        for index, spec in enumerate(TEST_LOCKS):
            requirements = ROOT / spec.requirements
            lock = ROOT / spec.lock
            candidate = temporary_dir / f"{index}.lock"
            try:
                result = run_bounded(
                    [
                        "uv", "pip", "compile", str(requirements),
                        "--constraint", str(lock),
                        "--python-version", spec.python_version,
                        "--python-platform", spec.platform,
                        "--output-file", str(candidate),
                        "--no-emit-index-url", "--no-annotate", "--no-header",
                        "--quiet",
                    ],
                    cwd=ROOT,
                    timeout_seconds=COMMAND_TIMEOUT_SECONDS,
                )
            except CommandTimedOut:
                failures.append(
                    f"{spec.lock}: resolution timed out after "
                    f"{COMMAND_TIMEOUT_SECONDS} seconds"
                )
                continue
            except (CommandLaunchError, CommandOutputTooLarge):
                failures.append(
                    f"{spec.lock}: resolution could not complete "
                    "(subprocess details redacted)"
                )
                continue
            if result.returncode != 0:
                failures.append(
                    redacted_failure(f"{spec.lock}: resolution", result.returncode)
                )
            elif candidate.read_bytes() != lock.read_bytes():
                failures.append(
                    f"{spec.lock}: stale; re-run uv pip compile for "
                    f"{spec.requirements}"
                )
            else:
                print(f"PASS {spec.lock} ({spec.platform})")
    if failures:
        print("\n".join(f"FAIL {failure}" for failure in failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
