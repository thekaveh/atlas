"""Compile and verify hash-locked ComfyUI custom-node dependency closures."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "services" / "comfyui" / "custom-node-locks"
CATALOG = ROOT / "services" / "comfyui" / "custom-nodes.yaml"
BASE_CONSTRAINTS = LOCK_DIR / "ai-dock-v2-cpu-22.04-v0.2.7.txt"
EXCLUDE_NEWER = "2026-08-30T00:00:00Z"
COMPILE_COMMAND = "uv run python scripts/compile_comfyui_custom_node_locks.py --write"


@dataclass(frozen=True)
class LockSpec:
    stem: str
    base_overrides: frozenset[str] = frozenset()
    universal: bool = True

    @property
    def source(self) -> Path:
        return LOCK_DIR / f"{self.stem}.in"

    @property
    def output(self) -> Path:
        return LOCK_DIR / f"{self.stem}.txt"


LOCKS = (
    LockSpec(
        "instantid",
        frozenset({"typing_extensions"}),
        universal=False,
    ),
    LockSpec("gguf"),
)


def base_package_names(path: Path = BASE_CONSTRAINTS) -> tuple[str, ...]:
    """Return the exact package inventory supplied by the reviewed base."""
    packages = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, _version = line.partition("==")
        if not separator or not name:
            raise ValueError(f"invalid exact base constraint: {raw_line!r}")
        packages.append(name)
    return tuple(packages)


def write_constraints(spec: LockSpec, output: Path) -> None:
    """Write the reviewed base constraints minus explicit overlay upgrades."""
    lines = []
    for raw_line in BASE_CONSTRAINTS.read_text(encoding="utf-8").splitlines(
        keepends=True
    ):
        line = raw_line.strip()
        name = line.partition("==")[0] if line and not line.startswith("#") else ""
        if name in spec.base_overrides:
            continue
        lines.append(raw_line)
    output.write_text("".join(lines), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_catalog() -> list[str]:
    """Return local catalog/lock integrity errors without network access."""
    errors: list[str] = []
    raw = yaml.safe_load(CATALOG.read_text(encoding="utf-8")) or {}
    for node in raw.get("custom_nodes", []):
        if not node.get("install_requirements"):
            continue
        name = str(node.get("name") or "<unnamed>")
        relative = node.get("requirements_lock")
        expected = str(node.get("requirements_lock_sha256") or "")
        if not isinstance(relative, str) or not relative:
            errors.append(f"{name}: requirements_lock is required")
            continue
        candidate = (CATALOG.parent / relative).resolve()
        try:
            candidate.relative_to(LOCK_DIR.resolve())
        except ValueError:
            errors.append(f"{name}: requirements_lock must stay under {LOCK_DIR.relative_to(ROOT)}")
            continue
        if not candidate.is_file():
            errors.append(f"{name}: lock does not exist: {relative}")
            continue
        actual = _sha256(candidate)
        if actual != expected:
            errors.append(f"{name}: lock digest mismatch: expected {expected}, got {actual}")
        text = candidate.read_text(encoding="utf-8")
        if "--hash=sha256:" not in text:
            errors.append(f"{name}: lock has no distribution hashes")
    return errors


def compile_command(
    spec: LockSpec, output: Path, *, constraints: Path = BASE_CONSTRAINTS
) -> list[str]:
    """Build the deterministic uv command for one overlay delta."""
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to compile ComfyUI custom-node locks")
    command = [
        uv,
        "pip",
        "compile",
        str(spec.source.relative_to(ROOT)),
        "--output-file",
        str(output),
        "--python-version",
        "3.10",
        "--constraint",
        str(constraints),
        "--generate-hashes",
        "--exclude-newer",
        EXCLUDE_NEWER,
        "--custom-compile-command",
        COMPILE_COMMAND,
        "--quiet",
    ]
    if spec.universal:
        command.append("--universal")
    else:
        command.extend(("--python-platform", "x86_64-manylinux_2_28"))
    for package in base_package_names():
        if package in spec.base_overrides:
            continue
        command.extend(("--no-emit-package", package))
    return command


def _compile(spec: LockSpec, output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="atlas-comfyui-constraints-") as raw_temp:
        constraints = Path(raw_temp) / "constraints.txt"
        write_constraints(spec, constraints)
        subprocess.run(
            compile_command(spec, output, constraints=constraints),
            cwd=ROOT,
            check=True,
            timeout=600,
        )


def write_locks() -> int:
    for spec in LOCKS:
        _compile(spec, spec.output)
        print(f"{spec.output.relative_to(ROOT)}  sha256:{_sha256(spec.output)}")
    return 0


def check_locks() -> int:
    errors = validate_catalog()
    with tempfile.TemporaryDirectory(prefix="atlas-comfyui-locks-") as raw_temp:
        temp = Path(raw_temp)
        for spec in LOCKS:
            generated = temp / spec.output.name
            _compile(spec, generated)
            if generated.read_bytes() != spec.output.read_bytes():
                errors.append(
                    f"{spec.output.relative_to(ROOT)} is stale; run {COMPILE_COMMAND}"
                )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("ComfyUI custom-node dependency locks are current and hash-verified.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate all compiled locks")
    mode.add_argument("--check", action="store_true", help="recompile and reject drift")
    mode.add_argument("--validate", action="store_true", help="verify committed lock digests only")
    args = parser.parse_args()
    if args.write:
        return write_locks()
    if args.check:
        return check_locks()
    errors = validate_catalog()
    for error in errors:
        print(f"ERROR: {error}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
