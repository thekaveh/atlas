"""
CLI lint for the per-service modular layout.

Runs the manifest loader + cross-manifest validator on the project's
services/ tree. With --check-env-example, also re-assembles .env.example
from manifests and diffs against the committed file.

Phase A scope: manifest + env-example checks only. Compose-fragment merge
verification (`docker compose -f docker-compose.yml config -q`) lands when
fragments exist (Phase D).

Exit codes:
    0  — clean
    1  — manifest load or cross-manifest validation errors
    2  — .env.example drift (only when --check-env-example is set)
    3  — uncaught internal error
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from services.env_assembler import assemble_env_example
from services.manifest_validator import validate_manifests
from services.manifests import ManifestLoadError, load_manifests


def run(
    project_root: Path,
    *,
    check_env_example: bool = False,
    env_example_path: Path | None = None,
) -> int:
    """Run the lint. Returns a process-style exit code.

    Args:
        project_root: Repository root. The script reads services/ relative to it.
        check_env_example: If True, assemble .env.example from manifests and diff
            against the committed file. Drift → exit 2.
        env_example_path: Override location of the committed .env.example
            (defaults to project_root/.env.example).
    """
    project_root = Path(project_root)
    services_dir = project_root / "services"

    try:
        manifests = load_manifests(services_dir)
    except ManifestLoadError as e:
        _err(str(e))
        return 1

    issues = validate_manifests(manifests, services_root=services_dir)
    if issues:
        _err(f"Found {len(issues)} cross-manifest issue(s):")
        for i in issues:
            _err(f"  [{i.kind}] services/{i.manifest}: {i.message}")
        return 1

    if check_env_example:
        target = env_example_path or (project_root / ".env.example")
        expected = assemble_env_example(manifests, services_root=services_dir)
        actual = target.read_text(encoding="utf-8") if target.is_file() else ""
        if expected != actual:
            _err(f".env.example drift detected ({target})")
            diff = difflib.unified_diff(
                actual.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=str(target),
                tofile="<assembled from manifests>",
                n=3,
            )
            sys.stderr.write("".join(diff))
            return 2

    gen_issues: list[str] = []
    gen_issues.extend(_check_readme_topology_block_is_current(project_root))
    if gen_issues:
        for msg in gen_issues:
            _err(msg)
        return 1

    _info(f"OK — {len(manifests)} manifest(s) validated.")
    return 0


def _check_readme_topology_block_is_current(project_root: Path) -> list[str]:
    """Ensure README.md TOPOLOGY block matches the generator's output."""
    readme = project_root / "README.md"
    services_dir = project_root / "services"
    if not services_dir.is_dir() or not any(services_dir.rglob("service.yml")):
        # Synthetic test tree with no manifests — nothing to validate against.
        return []
    if not readme.is_file():
        return ["README.md missing from real repo"]
    from tools.generate_readme_topology import generate_block
    expected = generate_block(project_root / "services").rstrip()
    text = readme.read_text(encoding="utf-8")
    start = text.find("<!-- TOPOLOGY:BEGIN -->")
    end = text.find("<!-- TOPOLOGY:END -->")
    if start == -1 or end == -1:
        return ["README.md missing TOPOLOGY markers"]
    end += len("<!-- TOPOLOGY:END -->")
    actual = text[start:end].rstrip()
    if expected != actual:
        return ["README.md TOPOLOGY block is stale — run `uv run --project bootstrapper python -m tools.generate_readme_topology`"]
    return []


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _info(msg: str) -> None:
    print(msg)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate-fragments",
        description="Lint the per-service modular layout (manifests + .env.example).",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent.parent,
        help="Project root (defaults to the repo containing this script).",
    )
    parser.add_argument(
        "--check-env-example",
        action="store_true",
        help="Also assert that the committed .env.example matches what the assembler produces.",
    )
    args = parser.parse_args(argv)
    try:
        return run(args.project_root, check_env_example=args.check_env_example)
    except Exception as e:  # pragma: no cover — last-resort guard
        _err(f"Internal error: {e}")
        return 3


if __name__ == "__main__":
    sys.exit(_main())
