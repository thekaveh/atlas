"""Enforced MVVM layer boundaries for the wizard (#535).

Folder names guarantee nothing; this test does. The direction is:

    view  ->  viewmodel  ->  model

- wizard/model/**      may not import vmx or textual
- wizard/viewmodel/**  may not import textual
- wizard/view/**       may not import wizard.model (must go through a VM)
- core/linear_startup  may not import vmx

The last rule is what makes the --no-tui guarantee structural rather
than asserted: the non-TTY path CANNOT acquire a VMx dependency,
because this test fails if it does.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # bootstrapper/


def _imported_modules(path: Path) -> set[str]:
    """Every absolute module name imported by one Python file.

    Relative imports (``from .x import y``) are skipped: they cannot
    cross a package boundary, so they can never violate a layer rule.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.add(node.module)
    return found


def layer_violations(root: Path, banned: tuple[str, ...]) -> list[tuple[str, str]]:
    """(relative_path, offending_module) for every banned import under root."""
    if not root.exists():
        return []
    violations: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for module in sorted(_imported_modules(path)):
            for bad in banned:
                if module == bad or module.startswith(bad + "."):
                    violations.append((str(path.relative_to(root)), module))
    return violations


def test_model_layer_imports_no_framework():
    """The Model layer is the one both the TUI and --no-tui consume."""
    found = layer_violations(ROOT / "wizard" / "model", ("vmx", "textual"))
    assert found == [], f"wizard/model must not import vmx or textual: {found}"


def test_viewmodel_layer_imports_no_textual():
    found = layer_violations(ROOT / "wizard" / "viewmodel", ("textual",))
    assert found == [], f"wizard/viewmodel must not import textual: {found}"


def test_view_layer_does_not_reach_past_the_viewmodel():
    found = layer_violations(ROOT / "wizard" / "view", ("wizard.model",))
    assert found == [], (
        f"wizard/view must read ViewModel state, not Model directly: {found}"
    )


def test_linear_startup_is_vmx_free():
    """--no-tui must never depend on VMx. Structural, not asserted."""
    target = ROOT / "core" / "linear_startup.py"
    assert target.exists(), "core/linear_startup.py moved; update this test"
    offending = sorted(
        m for m in _imported_modules(target)
        if m == "vmx" or m.startswith("vmx.")
    )
    assert offending == [], f"linear_startup must not import vmx: {offending}"


def test_model_package_exists():
    """Fails until Task 3 creates the package. Guards against this whole
    file silently passing on empty directories."""
    assert (ROOT / "wizard" / "model" / "__init__.py").exists()


# ── the lint must be proven to fail ─────────────────────────────────

def test_lint_catches_a_banned_import(tmp_path: Path):
    """Mutation proof. Without this, every assertion above could be
    passing because the checker never returns anything."""
    (tmp_path / "offender.py").write_text(
        "import vmx\nfrom textual.app import App\n", encoding="utf-8"
    )
    found = layer_violations(tmp_path, ("vmx", "textual"))
    assert ("offender.py", "vmx") in found
    assert ("offender.py", "textual.app") in found


def test_lint_allows_unrelated_imports(tmp_path: Path):
    (tmp_path / "clean.py").write_text(
        "import json\nfrom pathlib import Path\n", encoding="utf-8"
    )
    assert layer_violations(tmp_path, ("vmx", "textual")) == []


def test_lint_ignores_relative_imports(tmp_path: Path):
    """A relative import cannot cross a package boundary."""
    (tmp_path / "rel.py").write_text("from .sibling import thing\n", encoding="utf-8")
    assert layer_violations(tmp_path, ("vmx", "textual")) == []


def test_lint_matches_submodules_not_prefixes(tmp_path: Path):
    """`vmxtools` must not be mistaken for `vmx`."""
    (tmp_path / "a.py").write_text("import vmxtools\n", encoding="utf-8")
    assert layer_violations(tmp_path, ("vmx",)) == []
    (tmp_path / "b.py").write_text("import vmx.commands\n", encoding="utf-8")
    assert layer_violations(tmp_path, ("vmx",)) == [("b.py", "vmx.commands")]
