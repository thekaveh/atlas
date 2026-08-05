"""`bootstrapper/pyproject.toml` must declare its dev dependencies under
the PEP 735 `[dependency-groups].dev` table — not the deprecated
`[tool.uv].dev-dependencies` table.

uv 0.5+ emits a deprecation warning on every invocation against the old
table, and the table will be removed in a future major release. CI invokes
`uv sync --group dev --locked` (workflow file). These tests guard the contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "bootstrapper" / "pyproject.toml"
SERVICES_LINT = REPO_ROOT / ".github/workflows/services-lint.yml"
BACKEND_DEV_REQUIREMENTS = (
    REPO_ROOT / "services" / "backend" / "app" / "app" / "requirements-dev.txt"
)


def _load() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_dependency_groups_dev_exists() -> None:
    data = _load()
    assert "dependency-groups" in data, (
        "[dependency-groups] table missing from pyproject.toml. "
        "CI relies on `uv sync --group dev`."
    )
    assert "dev" in data["dependency-groups"], (
        "[dependency-groups].dev list missing from pyproject.toml."
    )
    dev = data["dependency-groups"]["dev"]
    assert isinstance(dev, list) and dev, (
        f"[dependency-groups].dev must be a non-empty list (got {dev!r})."
    )


def test_dev_group_contains_pytest() -> None:
    data = _load()
    dev = data["dependency-groups"]["dev"]
    assert any(entry.startswith("pytest") for entry in dev), (
        "[dependency-groups].dev must list pytest (it's the only dev dep)."
    )


def test_ci_enforces_measured_coverage_floors() -> None:
    data = _load()
    dev = data["dependency-groups"]["dev"]
    workflow = SERVICES_LINT.read_text(encoding="utf-8")
    backend_dev = BACKEND_DEV_REQUIREMENTS.read_text(encoding="utf-8")

    assert any(entry.startswith("pytest-cov") for entry in dev)
    assert "pytest-cov" in backend_dev
    assert workflow.count("--cov-fail-under=") >= 2
    assert "--cov-branch" in workflow


def test_deprecated_tool_uv_dev_dependencies_absent() -> None:
    """Belt-and-suspenders: the old table must NOT be re-added."""
    data = _load()
    tool_uv = data.get("tool", {}).get("uv", {})
    assert "dev-dependencies" not in tool_uv, (
        "[tool.uv].dev-dependencies is deprecated. Use [dependency-groups].dev."
    )


def test_services_lint_syncs_committed_lock_without_mutation() -> None:
    workflow = SERVICES_LINT.read_text(encoding="utf-8")

    assert workflow.count("uv sync --group dev --locked") == 3
    assert "run: uv sync --group dev\n" not in workflow
