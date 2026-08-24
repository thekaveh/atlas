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

Fix-round note (#535 Pass 1 followups review, finding C3): only ONE of
the three view/viewmodel/model rules above was ever load-bearing.
``layer_violations()`` returns ``[]`` when its ``root`` doesn't exist
(see its docstring below), and ``wizard/viewmodel/`` and
``wizard/view/`` don't exist yet as of Pass 1 -- they arrive in Pass 2
and Pass 3 of the design in
``docs/superpowers/specs/2026-08-23-wizard-mvvm-vmx-design.md``. So
``test_viewmodel_layer_imports_no_textual`` and
``test_view_layer_does_not_reach_past_the_viewmodel`` were each
asserting ``[] == []`` against a directory that never existed --
vacuously green, testing nothing, while AGENTS.md flatly claimed the
whole view -> viewmodel -> model direction was "enforced by" this
file.

The real view code lives at ``ui/textual/`` today (the design doc's
own package-structure table says as much: ``wizard/view/`` is where
``ui/textual/*`` MOVES TO in Pass 3, not a second copy of it that
exists in parallel). By the same design, ``ui/textual/`` legitimately
imports ``wizard.model`` directly right now -- Pass 1 built the Model
layer with exactly that in mind ("Today `integration.py` (the Textual
path) is its only caller", per every `wizard/model/*.py` module
docstring) precisely because no ViewModel layer exists yet for it to
go through. A rule that flags 100% of those imports as violations
would fail from the moment it started actually checking anything.

Two different fixes for two different situations, below:

- ``wizard/viewmodel/``: no directory exists AND no other location
  holds its future content in a form worth linting yet (the
  ViewModel-half logic the design doc identifies -- e.g.
  ``_build_steps_and_rows`` -- still lives inline in ``integration.py``
  itself, which legitimately needs ``textual``). There is nothing real
  to check. The test SKIPS with an explicit, loud reason instead of
  silently asserting ``[] == []`` -- pytest reports this as a distinct
  "skipped" outcome with a visible reason, not a green pass that looks
  identical to a real one. The skip lifts itself automatically the
  moment Pass 2 creates the directory.
- ``wizard/view/``: no directory exists YET, but its real, current
  stand-in (``ui/textual/``) does, and DOES import ``wizard.model`` at
  six known, deliberate Pass-1 sites. The test now scans
  ``ui/textual/`` for real and pins those six sites as a closed
  allowlist (see ``_KNOWN_PASS1_VIEW_MODEL_IMPORTS`` below) -- any
  violation beyond that exact set fails the test for real, and so does
  the allowlist becoming stale (over- OR under-inclusive) once Pass 3
  migration work starts landing. This is a real regression gate on
  Pass-1's known, tracked debt, not a rubber stamp.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # bootstrapper/


def _imported_modules(path: Path) -> set[str]:
    """Every absolute module name imported by one Python file.

    Relative imports (``from .x import y``) are skipped.

    Known limitation, not a proven safety property: a relative import
    CAN cross a layer boundary and this checker will not see it. For
    example, ``wizard/model/x.py`` doing ``from ..llm_steps import Y``
    reaches from Model into ViewModel exactly like an absolute
    ``from wizard.llm_steps import Y`` would, but because it's spelled
    with a leading dot it never appears in this function's output.
    Widening the checker to resolve relative imports is out of scope
    here (Pass 2 work); this docstring exists so the gap is documented
    rather than silently assumed away.
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
    """Placeholder until Pass 2 of #535 creates ``wizard/viewmodel/``.

    Before the fix-round C3 review, this asserted ``[] == []`` against
    a directory that has never existed -- a silent, indistinguishable-
    from-real green pass. There is no other location today that holds
    ViewModel-shaped content worth linting for "no textual" (the
    ViewModel-half logic identified in the design doc, e.g.
    ``_build_steps_and_rows``, still lives inline in
    ``ui/textual/integration.py``, which legitimately needs
    ``textual``). So this SKIPS loudly instead of passing silently;
    the skip lifts itself the moment ``wizard/viewmodel/`` exists,
    turning this back into a real assertion with no further edits
    needed here.
    """
    root = ROOT / "wizard" / "viewmodel"
    if not root.exists():
        pytest.skip(
            "wizard/viewmodel/ doesn't exist yet -- arrives in Pass 2 of "
            "#535 (see docs/superpowers/specs/"
            "2026-08-23-wizard-mvvm-vmx-design.md). This is an explicit "
            "placeholder, not a passing check -- see this file's module "
            "docstring, finding C3."
        )
    found = layer_violations(root, ("textual",))
    assert found == [], f"wizard/viewmodel must not import textual: {found}"


# The real view today is `ui/textual/` -- `wizard/view/` is where Pass 3
# of #535 MOVES it to, not a second copy that coexists with it (see this
# file's module docstring, finding C3). Pass 1 deliberately left
# `ui/textual/` importing `wizard.model` directly at these six sites —
# every `wizard/model/*.py` module docstring says as much ("Today
# `integration.py` (the Textual path) is its only caller") — because no
# ViewModel layer exists yet for those imports to go through. This is a
# closed allowlist, not an open one: recompute it with
# `layer_violations(ROOT / "ui" / "textual", ("wizard.model",))` any time
# `ui/textual/` changes its `wizard.model` usage, and prune entries here
# as Pass 3 migration work removes them from the source. If the set ever
# grows beyond what a specific, reviewed Pass-3-migration commit expects,
# that is a genuine new Model import that must go through a ViewModel
# instead, and this test is supposed to catch it.
_KNOWN_PASS1_VIEW_MODEL_IMPORTS = frozenset({
    ("integration.py", "wizard.model.cloud_rules"),
    ("integration.py", "wizard.model.service_discovery"),
    ("integration.py", "wizard.model.state_builder"),
    ("integration.py", "wizard.model.track_rules"),
    ("widgets/info_box.py", "wizard.model.state_builder"),
    ("widgets/prompt_panel.py", "wizard.model.cloud_rules"),
})


def test_view_layer_does_not_reach_past_the_viewmodel():
    """Scans the REAL view (``ui/textual/``), not the not-yet-created
    ``wizard/view/`` -- see this file's module docstring, finding C3.

    Exact-set equality (not a subset check) is deliberate: it fails
    loudly in BOTH directions. Growth beyond the allowlist is a new,
    unreviewed ``wizard.model`` import that must go through a
    ViewModel instead. Shrinkage means Pass 3 migration work landed
    here and this allowlist -- and ideally this rule's target
    directory, once ``wizard/view/`` exists -- is now stale and needs
    updating, not silently left to rot as permissive dead weight.
    """
    found = layer_violations(ROOT / "ui" / "textual", ("wizard.model",))
    assert set(found) == _KNOWN_PASS1_VIEW_MODEL_IMPORTS, (
        "ui/textual/ (the real view, pending its Pass 3 move to "
        "wizard/view/) reaches into wizard.model at unexpected sites. "
        "If new sites appeared, that's an unreviewed Model import that "
        "must go through a ViewModel instead. If known sites vanished, "
        "Pass 3 migration work landed -- update "
        "_KNOWN_PASS1_VIEW_MODEL_IMPORTS to match (and once "
        "wizard/view/ exists and ui/textual/ is empty of wizard.model "
        f"imports, re-point this test at wizard/view/ instead).\nfound: {sorted(found)}"
    )


def test_wizard_view_package_pending_pass_3():
    """Tripwire for the OTHER half of finding C3: the moment Pass 3 of
    #535 starts creating ``wizard/view/``, the test above must be
    re-pointed there (dropping the ``ui/textual/`` allowlist) rather
    than left silently checking a stand-in directory nobody reads
    anymore. This fails loudly the instant that directory appears, so
    it can't be missed the way the original vacuous-pass bug was."""
    assert not (ROOT / "wizard" / "view").exists(), (
        "wizard/view/ now exists -- re-point "
        "test_view_layer_does_not_reach_past_the_viewmodel at it "
        "(dropping _KNOWN_PASS1_VIEW_MODEL_IMPORTS and the ui/textual/ "
        "scan) instead of leaving this tripwire and the stale scan "
        "both in place."
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
    """Known limitation, not a safety property — see `_imported_modules`'s
    docstring: a relative import (e.g. `from ..llm_steps import Y` inside
    wizard/model) CAN cross a layer boundary and this checker won't catch
    it. This test pins the current (unresolved) behavior, not a guarantee."""
    (tmp_path / "rel.py").write_text("from .sibling import thing\n", encoding="utf-8")
    assert layer_violations(tmp_path, ("vmx", "textual")) == []


def test_lint_matches_submodules_not_prefixes(tmp_path: Path):
    """`vmxtools` must not be mistaken for `vmx`."""
    (tmp_path / "a.py").write_text("import vmxtools\n", encoding="utf-8")
    assert layer_violations(tmp_path, ("vmx",)) == []
    (tmp_path / "b.py").write_text("import vmx.commands\n", encoding="utf-8")
    assert layer_violations(tmp_path, ("vmx",)) == [("b.py", "vmx.commands")]
