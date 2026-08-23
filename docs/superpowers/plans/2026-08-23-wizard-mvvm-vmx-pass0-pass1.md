# Wizard MVVM/VMx — Pass 0 + Pass 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Textual, then extract every wizard domain rule into a new VMx-free `wizard/model/` package guarded by an enforced import-direction lint — so the later VMx passes have a clean Model to build on and the `--no-tui` path is structurally unable to depend on `vmx`.

**Architecture:** Three-layer MVVM split under `bootstrapper/wizard/` — `model/` (VMx-free, Textual-free), `viewmodel/` (VMx, no Textual), `view/` (Textual, no Model). This plan builds the `model/` layer and the lint that keeps the layers honest. No VMx is added in this plan.

**Tech Stack:** Python ≥3.10, pytest, Textual, `uv`. VMx (`vmx==3.23.0`) arrives in Pass 2, **not here**.

**Spec:** `docs/superpowers/specs/2026-08-23-wizard-mvvm-vmx-design.md`

## Global Constraints

- **Branch:** `feat/535-rebuild-setup-wizard-tui-mvvm-vmx`, cut from `develop`. Pass 0 is a **separate PR into `develop`**; Pass 1 lands on the feature branch.
- **Integration target is `develop`, not `main`.** `main` and `develop` are both protected by the `gitflow` ruleset — PR required, strict mode, no direct push.
- **Python floor: `>=3.10`.** Nothing may raise it. This is why `textual-image` cannot go past `0.12.x` (0.13+ requires ≥3.12).
- **Do NOT add `vmx` in this plan.** It is pinned in Pass 2.
- **Do NOT add a linter, formatter, or type-checker.** None is configured; `CLAUDE.md` forbids introducing one.
- **Parity is the invariant.** No wizard behaviour changes in this plan. Every move is a move.
- **Tests live in `bootstrapper/tests/`,** named `test_*.py`. `pyproject.toml` sets `pythonpath = [".", ".."]`, so modules import as bare names (`from ui.state_builder import resolve_port`), not `bootstrapper.ui...`.
- **All commands run from `bootstrapper/`** unless stated: `cd bootstrapper && uv run pytest ...`
- **Full suite is 3,908 tests, ~6–7 min.** Run targeted tests per step; run the full suite before each commit that moves files.
- **Docs land in the same slice as the change, never deferred** (house rule). `CLAUDE.md` is gitignored — the tracked repo-facing file is `AGENTS.md`.

---

## File Structure

**Pass 0 — modified only:**
- `bootstrapper/pyproject.toml` — Textual pin
- `bootstrapper/uv.lock` — regenerated
- `bootstrapper/tests/test_textual_floor.py` — **created**; pins the floor and protects the `textual-image` seam

**Pass 1 — created:**
- `bootstrapper/wizard/model/__init__.py` — package marker + public re-exports
- `bootstrapper/wizard/model/state.py` — moved from `ui/state.py`
- `bootstrapper/wizard/model/state_builder.py` — moved from `ui/state_builder.py`
- `bootstrapper/wizard/model/service_discovery.py` — moved from `wizard/service_discovery.py`
- `bootstrapper/wizard/model/llm_rules.py` — Model half of `wizard/llm_steps.py`
- `bootstrapper/wizard/model/track_rules.py` — track force-disable rule
- `bootstrapper/wizard/model/cloud_rules.py` — cloud secret/enable promotion rules
- `bootstrapper/tests/test_wizard_layer_boundaries.py` — the import-direction lint
- `bootstrapper/tests/test_wizard_model_track_rules.py`
- `bootstrapper/tests/test_wizard_model_cloud_rules.py`
- `bootstrapper/tests/test_wizard_model_llm_rules.py`
- `bootstrapper/scripts/loc_report.py` — reproducible LOC + complexity accounting

**Pass 1 — modified:**
- `bootstrapper/ui/textual/integration.py` — `_selections_to_args` delegates to the extracted rules
- `bootstrapper/wizard/llm_steps.py` — Model half removed, imports from `wizard.model.llm_rules`
- every importer of `ui.state`, `ui.state_builder`, `wizard.service_discovery` (see Task 3 Step 1 for the enumeration command)

**Deliberately NOT touched in this plan:** `wizard/comfyui_steps.py`, `wizard/ray_steps.py`, `_build_steps_and_rows`, and all of `ui/textual/widgets/`. Those are ViewModel and belong to Pass 2/3.

---

## Task 1: Upgrade Textual 6.2.1 → 8.2.8 (Pass 0 — separate PR)

**Files:**
- Modify: `bootstrapper/pyproject.toml:15`
- Modify: `bootstrapper/uv.lock`
- Test: `bootstrapper/tests/test_textual_floor.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: a Textual ≥8.2.8 environment. Later tasks rely on nothing from this task; it is sequenced first only so wizard regressions are never ambiguous between "VMx" and "Textual".

**Context:** Atlas imports `App, Binding, ComposeResult, Container, events, get_cell_size, Horizontal, Image, Input, Message, RichLog, Screen, Selection, SelectionList, Static, Vertical, VerticalScroll, Widget, Worker, WorkerState`. It does **not** import `Select`, so the headline 8.x break (`Select.BLANK` → `Select.NULL`) does not apply. The real risk is `textual-image` 0.12.0, which declares only `textual>=0.68.0` — a floor with no ceiling — and hooks terminal image protocols into Textual internals. It cannot be bumped to match (0.13+ needs Python ≥3.12).

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_textual_floor.py`:

```python
"""Textual version floor + the textual-image compatibility seam.

textual-image declares `textual>=0.68.0` with no upper bound, so pip
metadata cannot catch a break across Textual majors. These tests are
the only guard on that seam.
"""

from __future__ import annotations

import importlib.metadata as md

from packaging.version import Version


def test_textual_is_at_least_8_2_8():
    """Selection auto-scroll, cross-container selection and the
    TextSelected event all require >= 8.x (TextSelected: 6.11.0)."""
    installed = Version(md.version("textual"))
    assert installed >= Version("8.2.8"), (
        f"expected textual >= 8.2.8, got {installed}"
    )


def test_text_selected_event_is_importable():
    """Added in Textual 6.11.0. Pass 3 binds log-pane selection to a
    ViewModel through this event, so its absence is a hard failure."""
    from textual.events import TextSelected  # noqa: F401


def test_richlog_still_allows_selection():
    """LogPane subclasses RichLog. If a Textual upgrade ever flips this
    default, mouse selection in the log pane dies silently."""
    from textual.widgets import RichLog

    assert RichLog.ALLOW_SELECT is True


def test_textual_image_seam_still_imports():
    """atlas_splash.py imports these two names lazily at render time, so
    a break would surface as a broken splash at runtime rather than an
    ImportError at startup. Import them eagerly here instead."""
    from textual_image.widget import Image, get_cell_size  # noqa: F401


def test_textual_image_stays_below_0_13():
    """textual-image 0.13+ requires Python >=3.12 and would raise the
    bootstrapper's 3.10 floor."""
    installed = Version(md.version("textual-image"))
    assert installed < Version("0.13"), (
        f"textual-image {installed} would break the Python 3.10 floor"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd bootstrapper && uv run pytest tests/test_textual_floor.py -v
```

Expected: `test_textual_is_at_least_8_2_8` FAILS (`expected textual >= 8.2.8, got 6.2.1`) and `test_text_selected_event_is_importable` FAILS with `ImportError`. The other three should already pass.

If `packaging` is not importable, add `from importlib.metadata import version` and compare tuples of ints instead — do **not** add `packaging` as a dependency.

- [ ] **Step 3: Bump the pin**

In `bootstrapper/pyproject.toml`, change line 15 from:

```toml
    "textual>=0.85",         # TUI framework — owns the wizard, launch, and streaming-logs flows
```

to:

```toml
    "textual>=8.2.8",        # TUI framework — owns the wizard, launch, and streaming-logs flows.
                             # Floor raised from 0.85 for selection auto-scroll, cross-container
                             # selection, and the TextSelected event (#535 Pass 0).
```

- [ ] **Step 4: Resync the environment**

```bash
cd bootstrapper && uv sync --group dev
uv run python -c "import textual; print(textual.__version__)"
```

Expected: `8.2.8` or higher.

- [ ] **Step 5: Run the new test to verify it passes**

```bash
cd bootstrapper && uv run pytest tests/test_textual_floor.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Run the full suite**

```bash
cd bootstrapper && uv run pytest -q
```

Expected: 3,908 passed. **If anything fails, stop and report** — a Textual break is exactly what this pass exists to isolate. Do not proceed to Pass 1 with a red suite.

Pay particular attention to `tests/test_atlas_splash_widget.py` and `tests/test_atlas_splash_logic.py`: those cover the `textual-image` seam. If they fail, the fallback is the existing block-art path — **do not** drop Python 3.10 to chase `textual-image` 0.13.

- [ ] **Step 7: Commit**

```bash
git add bootstrapper/pyproject.toml bootstrapper/uv.lock bootstrapper/tests/test_textual_floor.py
git commit -m "build(deps): raise Textual floor to 8.2.8 (#535 Pass 0)

Selection auto-scroll, cross-container selection and the TextSelected
event land in 6.11.0-8.x. Atlas does not import Select, so the 8.x
Select.BLANK -> Select.NULL break does not apply.

Adds tests/test_textual_floor.py to guard the textual-image seam:
that package declares textual>=0.68.0 with no ceiling, so pip metadata
cannot catch a break across majors. It stays pinned <0.13 because
0.13+ requires Python >=3.12 and would raise the 3.10 floor.

Refs #535"
```

- [ ] **Step 8: Open the Pass 0 PR into `develop`**

```bash
git push -u origin HEAD
gh pr create --base develop --title "build(deps): raise Textual floor to 8.2.8 (#535 Pass 0)" --body "Pass 0 of #535. Landed separately and first so that no wizard regression during the MVVM rebuild is ambiguous between VMx and Textual.

Refs #535"
```

Wait for the three required `services-lint` checks before merging.

---

## Task 2: Layer-boundary lint (Pass 1 — the guardrail, built first)

**Files:**
- Create: `bootstrapper/tests/test_wizard_layer_boundaries.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `layer_violations(root: Path, banned: tuple[str, ...]) -> list[tuple[str, str]]` — importable by later tasks if needed. Returns `(relative_path, offending_module)` pairs.

**Context:** This is built **before** any file moves so the moves land against a guard that already works. The function takes an explicit `root` so it can be tested against a synthetic tree — a lint that has never failed is not a lint.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_wizard_layer_boundaries.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd bootstrapper && uv run pytest tests/test_wizard_layer_boundaries.py -v
```

Expected: `test_model_package_exists` FAILS (the package does not exist yet). The four mutation tests PASS. The three layer tests PASS vacuously (empty dirs return `[]`) — that vacuity is exactly what `test_model_package_exists` exists to catch.

- [ ] **Step 3: Commit the guard**

```bash
git add bootstrapper/tests/test_wizard_layer_boundaries.py
git commit -m "test(wizard): add MVVM layer-boundary lint (#535 Pass 1)

Enforces view -> viewmodel -> model. The linear_startup rule is what
makes the --no-tui VMx-free guarantee structural rather than asserted.

Mutation-proven: the suite includes cases that inject banned imports
into a synthetic tree and assert the checker flags them.

Committed red on test_model_package_exists; Task 3 turns it green.

Refs #535"
```

---

## Task 3: Create `wizard/model/` and move the state layer

**Files:**
- Create: `bootstrapper/wizard/model/__init__.py`
- Create: `bootstrapper/wizard/model/state.py` (git-mv of `bootstrapper/ui/state.py`, 81 lines)
- Create: `bootstrapper/wizard/model/state_builder.py` (git-mv of `bootstrapper/ui/state_builder.py`, 350 lines)
- Modify: every importer (enumerated in Step 1)
- Test: `bootstrapper/tests/test_wizard_layer_boundaries.py` (already exists — turns green)

**Interfaces:**
- Consumes: `layer_violations` from Task 2.
- Produces: `wizard.model.state` exporting `ServiceEntry`, `CloudApiEntry`, `ConsumerEntry`, `AppState`; `wizard.model.state_builder` exporting `build_app_state`, `resolve_port`, `alias_for`, `service_extras`, `all_services`, `all_cloud_apis`, `cloud_api_status_text`. Tasks 4–8 and all of Pass 2/3 import from these paths.

**Context:** This is a **pure move**. No logic changes. `ui/state.py` and `ui/state_builder.py` are already framework-agnostic — they were simply filed under `ui/`, which is what made "rules are Model" hard to see. `ui/term_caps.py` stays where it is: it is host-environment detection consumed by `start.py` before any wizard exists.

- [ ] **Step 1: Enumerate every importer before moving anything**

```bash
cd bootstrapper
grep -rn "from ui\.state\|from ui import state\|import ui\.state" \
  --include='*.py' . | grep -v __pycache__ | tee /tmp/state_importers.txt
wc -l /tmp/state_importers.txt
```

Record the count. Every line must be updated in Step 3 — none may be skipped.

- [ ] **Step 2: Move the files with `git mv` so history follows**

```bash
cd bootstrapper
mkdir -p wizard/model
git mv ui/state.py wizard/model/state.py
git mv ui/state_builder.py wizard/model/state_builder.py
```

Create `bootstrapper/wizard/model/__init__.py`:

```python
"""Wizard Model layer — VMx-free, Textual-free (#535).

Everything here is consumed by BOTH the Textual wizard and the --no-tui
linear flow. Nothing in this package may import ``vmx`` or ``textual``;
``tests/test_wizard_layer_boundaries.py`` enforces that.
"""

from __future__ import annotations

from wizard.model.state import (
    AppState,
    CloudApiEntry,
    ConsumerEntry,
    ServiceEntry,
)
from wizard.model.state_builder import (
    alias_for,
    all_cloud_apis,
    all_services,
    build_app_state,
    cloud_api_status_text,
    resolve_port,
    service_extras,
)

__all__ = [
    "AppState",
    "CloudApiEntry",
    "ConsumerEntry",
    "ServiceEntry",
    "alias_for",
    "all_cloud_apis",
    "all_services",
    "build_app_state",
    "cloud_api_status_text",
    "resolve_port",
    "service_extras",
]
```

If `wizard/model/state.py` re-exports names not in the list above, add them to both the import block and `__all__` — check with:

```bash
cd bootstrapper && uv run python -c "
import ast, pathlib
for f in ('state.py', 'state_builder.py'):
    t = ast.parse(pathlib.Path('wizard/model', f).read_text())
    print(f, sorted(n.name for n in ast.walk(t)
          if isinstance(n, (ast.FunctionDef, ast.ClassDef))
          and not n.name.startswith('_')))
"
```

- [ ] **Step 3: Repoint every importer**

```bash
cd bootstrapper
grep -rl "from ui\.state" --include='*.py' . | grep -v __pycache__ | \
  xargs sed -i '' \
    -e 's/from ui\.state_builder import/from wizard.model.state_builder import/g' \
    -e 's/from ui\.state import/from wizard.model.state import/g'
```

Note the `sed -i ''` form — this is macOS/BSD sed. On Linux use `sed -i` with no argument.

Then verify nothing was missed:

```bash
cd bootstrapper
grep -rn "from ui\.state\|import ui\.state" --include='*.py' . | grep -v __pycache__
```

Expected: no output.

- [ ] **Step 4: Run the boundary lint and the moved modules' own tests**

```bash
cd bootstrapper
uv run pytest tests/test_wizard_layer_boundaries.py tests/test_state_builder.py -v
```

Expected: all pass, including `test_model_package_exists`, which was red after Task 2.

- [ ] **Step 5: Run the full suite**

```bash
cd bootstrapper && uv run pytest -q
```

Expected: 3,908 passed. A move that changes a test count means something was renamed, not moved — investigate before committing.

- [ ] **Step 6: Verify the diff really is a pure move**

```bash
git add -A
git diff --cached -M --stat
```

Expected: `state.py` and `state_builder.py` show as renames (`R`), not add+delete. Import-line edits in consumers are expected; **no other content change in the moved files themselves** — confirm with:

```bash
git diff --cached -M -- bootstrapper/wizard/model/state.py bootstrapper/wizard/model/state_builder.py
```

Expected: empty (a pure rename has no content diff).

- [ ] **Step 7: Commit**

```bash
git commit -m "refactor(wizard): move state layer into wizard/model (#535 Pass 1)

Pure move, no logic change. ui/state.py and ui/state_builder.py were
already framework-agnostic; being filed under ui/ is what obscured
that they are Model, not View.

ui/term_caps.py deliberately stays put: it is host-environment
detection consumed by start.py before any wizard exists, so it
belongs to no wizard layer.

test_model_package_exists in the layer-boundary lint goes green here.

Refs #535"
```

---

## Task 4: Move `service_discovery.py` into the Model layer

**Files:**
- Create: `bootstrapper/wizard/model/service_discovery.py` (git-mv of `bootstrapper/wizard/service_discovery.py`, 207 lines)
- Modify: `bootstrapper/wizard/model/__init__.py`
- Modify: every importer (enumerated in Step 1)

**Interfaces:**
- Consumes: the `wizard.model` package from Task 3.
- Produces: `wizard.model.service_discovery` exporting `ServiceInfo`, `ServiceDiscovery`, `CLOUD_PROVIDER_KEYS`. Task 6 (`track_rules`) takes `Sequence[ServiceInfo]` as a parameter, so this must land first.

**Context:** `ServiceInfo` / `ServiceDiscovery` are service metadata — domain truth with no UI in them. The three *step-builder* modules beside it (`llm_steps.py`, `comfyui_steps.py`, `ray_steps.py`) are ViewModel and are **not** touched here.

- [ ] **Step 1: Enumerate importers**

```bash
cd bootstrapper
grep -rn "wizard\.service_discovery\|from wizard import service_discovery" \
  --include='*.py' . | grep -v __pycache__
```

- [ ] **Step 2: Move it**

```bash
cd bootstrapper && git mv wizard/service_discovery.py wizard/model/service_discovery.py
```

- [ ] **Step 3: Repoint importers**

```bash
cd bootstrapper
grep -rl "wizard\.service_discovery" --include='*.py' . | grep -v __pycache__ | \
  xargs sed -i '' 's/wizard\.service_discovery/wizard.model.service_discovery/g'
grep -rn "wizard\.service_discovery" --include='*.py' . | grep -v __pycache__ | \
  grep -v "wizard\.model\.service_discovery"
```

Expected: the final grep produces no output.

- [ ] **Step 4: Add the re-exports**

In `bootstrapper/wizard/model/__init__.py`, add after the `state_builder` import block:

```python
from wizard.model.service_discovery import (
    CLOUD_PROVIDER_KEYS,
    ServiceDiscovery,
    ServiceInfo,
)
```

and add `"CLOUD_PROVIDER_KEYS"`, `"ServiceDiscovery"`, `"ServiceInfo"` to `__all__`, keeping it alphabetically sorted.

- [ ] **Step 5: Run the affected tests**

```bash
cd bootstrapper
uv run pytest tests/test_wizard_app_discovery.py tests/test_wizard_layer_boundaries.py -v
```

Expected: all pass.

- [ ] **Step 6: Run the full suite**

```bash
cd bootstrapper && uv run pytest -q
```

Expected: 3,908 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(wizard): move service_discovery into wizard/model (#535 Pass 1)

ServiceInfo/ServiceDiscovery are service metadata — domain truth with
no UI in them. The step-builder modules beside it (llm_steps,
comfyui_steps, ray_steps) are ViewModel and stay put until Pass 3.

Refs #535"
```

---

## Task 5: Extract the LLM Model predicates out of `llm_steps.py`

**Files:**
- Create: `bootstrapper/wizard/model/llm_rules.py`
- Modify: `bootstrapper/wizard/llm_steps.py:53-127` (remove the moved functions, import them back)
- Test: `bootstrapper/tests/test_wizard_model_llm_rules.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `parse_csv(value: str | None) -> list[str]`
  - `is_localhost_or_external(source: str) -> bool`
  - `is_container_ollama(source: str) -> bool`
  - `selected_llm_source(env_vars: dict[str, str], selections: dict) -> str`

  Task 8 imports `parse_csv`. Pass 3's step builders import the rest.

**Context:** `llm_steps.py` (1,064 lines) straddles the layer boundary — it is the one file that must be *split*, not moved. The four functions above are domain predicates the CLI path honours too. Everything else in the file (title constants, `build_ollama_steps`, `_build_library_options`, `_merge_badges`, `_compose_hint`, `_is_legacy`, `_sort_key`, `_make_cloud_options_provider`, `_make_cloud_skip_predicate`) is ViewModel and stays until Pass 3.

The originals are private (`_csv`, `_is_localhost_or_external`, `_is_container_ollama`, `_selected_llm_source`). They become public on the way out, because a Model boundary is a published surface.

- [ ] **Step 1: Read the four current implementations**

```bash
cd bootstrapper && sed -n '53,128p' wizard/llm_steps.py
```

Copy the bodies **verbatim** in Step 3. Do not "improve" them — this is an extraction, and any behaviour change here is a parity regression that the wizard tests may not catch.

- [ ] **Step 2: Write the failing test**

Create `bootstrapper/tests/test_wizard_model_llm_rules.py`:

```python
"""Model-layer LLM predicates extracted from wizard/llm_steps.py (#535).

These are domain rules — the CLI flag path honours them too — so they
live in wizard/model and must stay importable without vmx or textual.
"""

from __future__ import annotations

import pytest

from wizard.model.llm_rules import (
    is_container_ollama,
    is_localhost_or_external,
    parse_csv,
    selected_llm_source,
)


@pytest.mark.parametrize("raw,expected", [
    (None, []),
    ("", []),
    ("   ", []),
    ("a", ["a"]),
    ("a,b", ["a", "b"]),
    ("a, b ,c", ["a", "b", "c"]),
    ("a,,b", ["a", "b"]),
    (",a,", ["a"]),
])
def test_parse_csv(raw, expected):
    assert parse_csv(raw) == expected


def test_parse_csv_preserves_order_and_duplicates():
    """Model order is the user's declared order; de-duplication is a
    caller policy, not a parsing concern."""
    assert parse_csv("b,a,b") == ["b", "a", "b"]


@pytest.mark.parametrize("source", [
    "ollama-localhost",
    "ollama-external",
])
def test_is_localhost_or_external_true(source):
    assert is_localhost_or_external(source) is True


@pytest.mark.parametrize("source", [
    "ollama-container-cpu",
    "ollama-container-gpu",
    "none",
    "disabled",
    "",
])
def test_is_localhost_or_external_false(source):
    assert is_localhost_or_external(source) is False


@pytest.mark.parametrize("source,expected", [
    ("ollama-container-cpu", True),
    ("ollama-container-gpu", True),
    ("ollama-localhost", False),
    ("none", False),
    ("disabled", False),
    ("", False),
])
def test_is_container_ollama(source, expected):
    assert is_container_ollama(source) is expected


def test_selected_llm_source_prefers_selections_over_env():
    """A live wizard selection outranks the persisted .env value."""
    env = {"LLM_PROVIDER_SOURCE": "ollama-container-cpu"}
    from wizard.llm_steps import LLM_ENGINE_TITLE

    result = selected_llm_source(env, {LLM_ENGINE_TITLE: "ollama-localhost"})
    assert result == "ollama-localhost"


def test_selected_llm_source_falls_back_to_env():
    env = {"LLM_PROVIDER_SOURCE": "ollama-container-gpu"}
    assert selected_llm_source(env, {}) == "ollama-container-gpu"


def test_llm_rules_module_is_framework_free():
    """Belt-and-braces alongside the layer lint: this module in
    particular is imported by the --no-tui path."""
    import wizard.model.llm_rules as mod
    source = open(mod.__file__, encoding="utf-8").read()
    assert "import vmx" not in source
    assert "import textual" not in source
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd bootstrapper && uv run pytest tests/test_wizard_model_llm_rules.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'wizard.model.llm_rules'`.

- [ ] **Step 4: Create the module**

Create `bootstrapper/wizard/model/llm_rules.py`. Paste the four bodies from Step 1 verbatim, renamed as below, with the module docstring:

```python
"""LLM domain predicates (#535 Pass 1).

Extracted verbatim from wizard/llm_steps.py. These are Model, not
ViewModel: the CLI flag path honours the same rules, so they must be
reachable without importing the wizard's step builders.

Made public on extraction — a Model boundary is a published surface.
"""

from __future__ import annotations

from typing import Dict


def parse_csv(value: str | None) -> list[str]:
    ...  # body of _csv, verbatim


def is_localhost_or_external(source: str) -> bool:
    ...  # body of _is_localhost_or_external, verbatim


def is_container_ollama(source: str) -> bool:
    ...  # body of _is_container_ollama, verbatim


def selected_llm_source(env_vars: Dict[str, str], selections: dict) -> str:
    ...  # body of _selected_llm_source, verbatim
```

If `_selected_llm_source` imports `LLM_ENGINE_TITLE` from module scope, import it lazily **inside** the function (`from wizard.llm_steps import LLM_ENGINE_TITLE`) to avoid a Model→ViewModel import at module load. The title constant moves to the ViewModel layer in Pass 3, at which point this becomes a parameter instead; leave a comment saying so.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd bootstrapper && uv run pytest tests/test_wizard_model_llm_rules.py -v
```

Expected: all pass.

- [ ] **Step 6: Repoint `llm_steps.py` at the extracted rules**

Delete the four original private functions from `wizard/llm_steps.py` and add near the top of its imports:

```python
from wizard.model.llm_rules import (
    is_container_ollama as _is_container_ollama,
    is_localhost_or_external as _is_localhost_or_external,
    parse_csv as _csv,
    selected_llm_source as _selected_llm_source,
)
```

The aliases keep every existing call site in the 1,064-line file working unchanged, so this step's diff stays reviewable. Pass 3 drops the aliases when the file itself moves.

- [ ] **Step 7: Run the full suite**

```bash
cd bootstrapper && uv run pytest -q
```

Expected: 3,908 passed, plus the new `test_wizard_model_llm_rules.py` tests. **If `tests/test_wizard_ollama_options.py` fails, the extraction was not verbatim** — diff the bodies against Step 1's output.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(wizard): extract LLM domain predicates to Model (#535 Pass 1)

llm_steps.py straddles the layer boundary and is the one file that
must be split rather than moved. parse_csv, is_localhost_or_external,
is_container_ollama and selected_llm_source are domain rules the CLI
path honours too; the rest of the file is ViewModel and stays until
Pass 3.

Bodies moved verbatim. Aliased back at the old private names so the
1,064-line caller diff stays reviewable.

Refs #535"
```

---

## Task 6: Extract the track force-disable rule

**Files:**
- Create: `bootstrapper/wizard/model/track_rules.py`
- Test: `bootstrapper/tests/test_wizard_model_track_rules.py` (create)
- **Does NOT modify `integration.py`** — the rule lands with its tests first; Task 8 wires it in. A reviewer should expect no `integration.py` diff from this task.

**Interfaces:**
- Consumes: `ServiceInfo` from `wizard.model.service_discovery` (Task 4).
- Produces: `track_force_disabled_sources(*, track_key: str | None, services_info: Sequence[ServiceInfo], already_set: Mapping[str, str]) -> dict[str, str]` — returns **only the synthesized additions**, keyed by CLI key (e.g. `"comfyui_source"`), valued `"disabled"`. Task 8 merges the result.

**Context:** The current block lives inside `_selections_to_args` (CC 63) and mutates `source_args` in place. The extraction returns additions instead of mutating, which is what makes it testable. Two behaviours must be preserved exactly:

1. `_track.services is None` (the `all` track) means **no** force-disable.
2. A bare `except Exception: pass` — track-registry load failure must never block the wizard.

That second one is deliberate, not sloppy. Keep it, keep the `# noqa: BLE001`, and keep the comment explaining why.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_wizard_model_track_rules.py`:

```python
"""Track force-disable rule, extracted from _selections_to_args (#535).

When a track is selected, every source-configurable service that is
out-of-track AND not explicitly overridden gets *_SOURCE=disabled
force-written. Their wizard step was skipped, so without this pass
.env would silently retain the user's prior choice — defeating the
track's force-disable semantic.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from wizard.model.track_rules import track_force_disabled_sources


@dataclass
class _Svc:
    """Minimal ServiceInfo stand-in — the rule only reads .key."""
    key: str


def test_no_track_selected_synthesizes_nothing():
    result = track_force_disabled_sources(
        track_key=None,
        services_info=[_Svc("comfyui"), _Svc("n8n")],
        already_set={},
    )
    assert result == {}


def test_empty_track_key_synthesizes_nothing():
    result = track_force_disabled_sources(
        track_key="",
        services_info=[_Svc("comfyui")],
        already_set={},
    )
    assert result == {}


def test_unknown_track_key_synthesizes_nothing():
    """A track key with no registry entry must not disable everything."""
    result = track_force_disabled_sources(
        track_key="no-such-track",
        services_info=[_Svc("comfyui")],
        already_set={},
    )
    assert result == {}


def test_all_track_synthesizes_nothing():
    """The 'all' track has services=None, meaning no force-disable."""
    result = track_force_disabled_sources(
        track_key="all",
        services_info=[_Svc("comfyui"), _Svc("n8n")],
        already_set={},
    )
    assert result == {}


def test_out_of_track_service_is_disabled():
    """gen-ai-rag excludes comfyui, so it must be force-disabled."""
    result = track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("comfyui")],
        already_set={},
    )
    assert result == {"comfyui_source": "disabled"}


def test_in_track_service_is_left_alone():
    """weaviate is in gen-ai-rag, so it must not be synthesized."""
    result = track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("weaviate")],
        already_set={},
    )
    assert "weaviate_source" not in result


def test_explicit_override_is_never_clobbered():
    """If the user visited the step, their choice wins."""
    result = track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("comfyui")],
        already_set={"comfyui_source": "container-gpu"},
    )
    assert result == {}


def test_hyphenated_service_key_becomes_underscored_cli_key():
    """CLI keys use underscores; service keys may use hyphens.

    label-studio is verified out-of-track for gen-ai-rag and not
    always-on, so it force-disables and the key must be rewritten.
    """
    result = track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("label-studio")],
        already_set={},
    )
    assert result == {"label_studio_source": "disabled"}


def test_registry_failure_never_blocks_the_wizard(monkeypatch):
    """A broken track registry must degrade to 'synthesize nothing',
    never raise. This bare-except is deliberate."""
    import wizard.model.track_rules as mod

    def _boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(mod, "load_tracks", _boom, raising=False)
    result = track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("comfyui")],
        already_set={},
    )
    assert result == {}


def test_rule_does_not_mutate_its_input():
    """Returning additions rather than mutating is what makes this
    testable; a regression to in-place mutation must fail here."""
    already = {"n8n_source": "container"}
    track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("comfyui")],
        already_set=already,
    )
    assert already == {"n8n_source": "container"}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd bootstrapper && uv run pytest tests/test_wizard_model_track_rules.py -v
```

Expected: `ModuleNotFoundError: No module named 'wizard.model.track_rules'`.

- [ ] **Step 3: Create the module**

Create `bootstrapper/wizard/model/track_rules.py`:

```python
"""Track force-disable rule (#535 Pass 1).

Extracted from ui/textual/integration.py::_selections_to_args. Domain
truth: the CLI flag path honours the same track semantics, so this
must be reachable without importing the wizard's Textual layer.

Returns additions instead of mutating a caller dict — that is what
makes the rule unit-testable.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from tracks import is_in_track, load_tracks


def track_force_disabled_sources(
    *,
    track_key: str | None,
    services_info: Sequence[object],
    already_set: Mapping[str, str],
) -> dict[str, str]:
    """CLI-key -> "disabled" for every out-of-track service not already set.

    ``services_info`` items need only a ``.key`` attribute.

    An ``all``-style track (``track.services is None``) force-disables
    nothing. A track-registry load failure degrades to ``{}``: it must
    never block the wizard.
    """
    if not track_key:
        return {}

    synthesized: dict[str, str] = {}
    try:
        registry = load_tracks()
        track = registry.by_key.get(track_key)
        if track is not None and track.services is not None:
            for svc in services_info:
                if is_in_track(track, svc.key, always_on=registry.always_on):
                    continue
                cli_key = svc.key.replace("-", "_") + "_source"
                if cli_key not in already_set:
                    synthesized[cli_key] = "disabled"
    except Exception:  # noqa: BLE001
        # Track-registry load failure must not block the wizard.
        return {}

    return synthesized
```

Note `load_tracks` and `is_in_track` are imported at module scope (not inside the function as the original did) so `monkeypatch.setattr(mod, "load_tracks", ...)` in the test can reach them.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd bootstrapper && uv run pytest tests/test_wizard_model_track_rules.py -v
```

Expected: all pass. If `test_out_of_track_service_is_disabled` fails, check that `gen-ai-rag` really excludes `comfyui` in `bootstrapper/tracks.yml`; if the track membership has changed, pick a genuinely out-of-track service and update the test's docstring to match.

- [ ] **Step 5: Commit the rule before wiring it**

```bash
git add bootstrapper/wizard/model/track_rules.py bootstrapper/tests/test_wizard_model_track_rules.py
git commit -m "feat(wizard/model): extract track force-disable rule (#535 Pass 1)

Returns synthesized additions rather than mutating the caller's dict,
which is what makes it unit-testable. Preserves both deliberate
behaviours: an 'all' track (services=None) disables nothing, and a
registry load failure degrades to {} rather than blocking the wizard.

Not yet wired into _selections_to_args — Task 8 does that.

Refs #535"
```

---

## Task 7: Extract the cloud-provider promotion rules

**Files:**
- Create: `bootstrapper/wizard/model/cloud_rules.py`
- Modify: `bootstrapper/ui/textual/widgets/prompt_panel.py` (sentinels move out, re-imported)
- Test: `bootstrapper/tests/test_wizard_model_cloud_rules.py` (create)
- **Does NOT modify `integration.py`** — Task 8 wires it in. A reviewer should expect no `integration.py` diff from this task.

**Interfaces:**
- Consumes: `CLOUD_PROVIDERS` from `utils.cloud_providers`.
- Produces:
  - `resolve_cloud_provider(*, provider_key: str, secret_value: str | None, selected_models: Sequence[str], existing_key_set: bool) -> CloudResolution`
  - `@dataclass(frozen=True) class CloudResolution: source: str; api_key: str | None; models: list[str]`

  Task 8 calls `resolve_cloud_provider` once per provider in `CLOUD_PROVIDERS`.

**Context:** This is the subtlest rule in the wizard and the one most likely to regress silently. Two independent signals must be reconciled:

- the **secret step** — a key, or the `SECRET_KEEP` sentinel (user pressed Enter past an existing key), or `SECRET_CLEAR`
- the **models multiselect** — where selecting zero models is an explicit "disable this provider" override

Read the current block before writing anything:

```bash
cd bootstrapper && sed -n '912,1010p' ui/textual/integration.py
```

`SECRET_KEEP` / `SECRET_CLEAR` currently live in `ui/textual/widgets/prompt_panel.py`. They are **sentinel values in a domain protocol**, not widget state, so they move to `wizard/model/cloud_rules.py` and `prompt_panel.py` imports them back — the same aliasing trick as Task 5.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_wizard_model_cloud_rules.py`:

```python
"""Cloud-provider enable/disable promotion rules (#535 Pass 1).

Two signals are reconciled here:
  * the secret step   -> a key, SECRET_KEEP, or SECRET_CLEAR
  * the models step   -> zero selected models is an explicit disable

This is the subtlest rule in the wizard; these tests are the contract.
"""

from __future__ import annotations

from wizard.model.cloud_rules import (
    SECRET_CLEAR,
    SECRET_KEEP,
    resolve_cloud_provider,
)


def test_new_key_with_models_enables():
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value="sk-test",
        selected_models=["gpt-4o"],
        existing_key_set=False,
    )
    assert r.source == "enabled"
    assert r.api_key == "sk-test"
    assert r.models == ["gpt-4o"]


def test_zero_models_disables_even_with_a_valid_key():
    """Selecting no models is an explicit override, not an omission."""
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value="sk-test",
        selected_models=[],
        existing_key_set=False,
    )
    assert r.source == "disabled"


def test_secret_keep_with_existing_key_promotes_to_enabled():
    """User pressed Enter past an existing key: keep it, and enable."""
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=SECRET_KEEP,
        selected_models=["gpt-4o"],
        existing_key_set=True,
    )
    assert r.source == "enabled"
    assert r.api_key is None, "KEEP must not rewrite the stored key"


def test_secret_keep_without_existing_key_does_not_enable():
    """Nothing to keep means nothing to enable."""
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=SECRET_KEEP,
        selected_models=["gpt-4o"],
        existing_key_set=False,
    )
    assert r.source == "disabled"


def test_secret_clear_disables_and_blanks_the_key():
    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value=SECRET_CLEAR,
        selected_models=["gpt-4o"],
        existing_key_set=True,
    )
    assert r.source == "disabled"
    assert r.api_key == ""


def test_empty_secret_with_no_existing_key_disables():
    r = resolve_cloud_provider(
        provider_key="anthropic",
        secret_value="",
        selected_models=[],
        existing_key_set=False,
    )
    assert r.source == "disabled"


def test_resolution_is_frozen():
    """Callers merge these into env; accidental mutation would be a
    cross-provider leak."""
    import dataclasses

    r = resolve_cloud_provider(
        provider_key="openai",
        secret_value="sk-test",
        selected_models=["gpt-4o"],
        existing_key_set=False,
    )
    assert dataclasses.is_dataclass(r)
    try:
        r.source = "hacked"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("CloudResolution must be frozen")


def test_every_declared_provider_resolves():
    """Adding a 4th provider must not silently miss this rule."""
    from utils.cloud_providers import CLOUD_PROVIDERS

    for provider in CLOUD_PROVIDERS:
        r = resolve_cloud_provider(
            provider_key=provider.key,
            secret_value="sk-test",
            selected_models=["m"],
            existing_key_set=False,
        )
        assert r.source in {"enabled", "disabled"}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd bootstrapper && uv run pytest tests/test_wizard_model_cloud_rules.py -v
```

Expected: `ModuleNotFoundError: No module named 'wizard.model.cloud_rules'`.

- [ ] **Step 3: Create the module**

Create `bootstrapper/wizard/model/cloud_rules.py` with the `CloudResolution` frozen dataclass, the two sentinels moved from `prompt_panel.py`, and `resolve_cloud_provider` implementing the reconciliation read in the Context block. The truth table the tests above encode:

| `secret_value` | `existing_key_set` | `selected_models` | → `source` | → `api_key` |
|---|---|---|---|---|
| a real key | any | non-empty | `enabled` | the key |
| a real key | any | empty | `disabled` | the key |
| `SECRET_KEEP` | `True` | non-empty | `enabled` | `None` (do not rewrite) |
| `SECRET_KEEP` | `False` | any | `disabled` | `None` |
| `SECRET_CLEAR` | any | any | `disabled` | `""` |
| `""` / `None` | `False` | any | `disabled` | `None` |

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd bootstrapper && uv run pytest tests/test_wizard_model_cloud_rules.py -v
```

Expected: all pass.

- [ ] **Step 5: Repoint `prompt_panel.py` at the moved sentinels**

Remove the `SECRET_KEEP` / `SECRET_CLEAR` definitions from `ui/textual/widgets/prompt_panel.py` and add:

```python
from wizard.model.cloud_rules import SECRET_CLEAR, SECRET_KEEP  # noqa: F401
```

The `noqa` is required: the names are re-exported for existing importers, not used in that file directly. Verify no importer broke:

```bash
cd bootstrapper
grep -rn "SECRET_KEEP\|SECRET_CLEAR" --include='*.py' . | grep -v __pycache__
uv run pytest tests/test_wizard_fal_secret_step.py -v
```

- [ ] **Step 6: Run the full suite**

```bash
cd bootstrapper && uv run pytest -q
```

Expected: 3,908 passed plus the new tests.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(wizard/model): extract cloud provider promotion rules (#535 Pass 1)

Reconciles the secret step (key / SECRET_KEEP / SECRET_CLEAR) with the
models multiselect, where selecting zero models is an explicit disable
override rather than an omission.

SECRET_KEEP/SECRET_CLEAR move out of prompt_panel.py: they are sentinel
values in a domain protocol, not widget state. Aliased back so existing
importers keep working.

Not yet wired into _selections_to_args — Task 8 does that.

Refs #535"
```

---

## Task 8: Wire `_selections_to_args` to the extracted rules

**Files:**
- Modify: `bootstrapper/ui/textual/integration.py:852-1128`
- Test: existing `bootstrapper/tests/` parity suites (no new test file)

**Interfaces:**
- Consumes: `track_force_disabled_sources` (Task 6), `resolve_cloud_provider` + `SECRET_KEEP` + `SECRET_CLEAR` (Task 7). **Not** `parse_csv` — that is consumed by `llm_steps.py`, which Task 5 already repointed.
- Produces: `_selections_to_args` with an unchanged signature and unchanged output. This is the task that moves the complexity number.

**Context:** `_selections_to_args` is CC 63 and is one of two symbols named in `.maintenance.json` as an accepted signal, with the rationale *"Boundary translator for the same manifest-driven option matrix; CLI/TUI parity tests are the safer current control. Revisit together with `_build_steps_and_rows`."* This task is that revisit.

**The existing parity tests are the oracle.** Its output must not change by one character.

- [ ] **Step 1: Capture the current behaviour as a baseline**

```bash
cd bootstrapper
uv run pytest -q -k "selections_to_args or parity or tracks_no_tui or wizard_vocabulary" 2>&1 | tail -5
```

Record the pass count. This exact set must still pass at Step 5.

- [ ] **Step 2: Replace the track block**

Delete `ui/textual/integration.py:884-910` (the whole `# ─── Force-disable off-track services ───` block through its `pass`) and replace with:

```python
    # ─── Force-disable off-track services ────────────────────────────
    # Rule lives in wizard/model/track_rules.py (#535 Pass 1); see there
    # for why an 'all' track disables nothing and why a registry load
    # failure degrades silently.
    source_args.update(
        track_force_disabled_sources(
            track_key=selections.get(PICKER_STEP_TITLE),
            services_info=services_info,
            already_set=source_args,
        )
    )
```

Add to the imports at the top of `integration.py`:

```python
from wizard.model.track_rules import track_force_disabled_sources
```

- [ ] **Step 3: Replace the cloud block**

Replace the per-provider reconciliation loop with a call to `resolve_cloud_provider` per provider, merging each `CloudResolution` into the env/args dicts exactly as the original did. Keep the existing env-var-name derivation in `integration.py` — the `OPENROUTER_USER_MODELS` naming trap that `cloud_providers.py` warns about is a *naming* concern, not a promotion rule, and moving it is out of scope for this task.

- [ ] **Step 4: Run the parity oracle**

```bash
cd bootstrapper
uv run pytest -q -k "selections_to_args or parity or tracks_no_tui or wizard_vocabulary" 2>&1 | tail -5
```

Expected: identical pass count to Step 1, zero failures. **Any failure here is a parity regression — fix it before proceeding, do not adjust the test.**

- [ ] **Step 5: Run the full suite**

```bash
cd bootstrapper && uv run pytest -q
```

Expected: 3,908 passed.

- [ ] **Step 6: Measure the complexity drop**

```bash
cd bootstrapper
uv run radon cc -s ui/textual/integration.py | grep -E "_selections_to_args|_build_steps_and_rows"
```

Expected: `_selections_to_args` has dropped well below 63. Record the new value — Task 9 reports it. `_build_steps_and_rows` should be **unchanged at 70**; it is ViewModel and belongs to Pass 3.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(wizard): route _selections_to_args through Model rules (#535 Pass 1)

Replaces the inline track force-disable and cloud promotion blocks with
calls into wizard/model. Output is byte-identical; the CLI/TUI parity
suites are the oracle and were not modified.

This is the 'revisit' that .maintenance.json's accepted_signals entry
for _selections_to_args (CC 63) anticipated. _build_steps_and_rows
stays at CC 70 — it is ViewModel and belongs to Pass 3.

Refs #535"
```

---

## Task 9: LOC and complexity report script

**Files:**
- Create: `bootstrapper/scripts/loc_report.py`
- Test: `bootstrapper/tests/test_loc_report.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `python bootstrapper/scripts/loc_report.py` printing a per-layer LOC + complexity table. Every later pass runs it at its boundary.

**Context:** The spec requires LOC be reported split three ways — **relocated**, **eliminated**, **added** — because conflating them would flatter the result. It also requires complexity be tracked as the primary signal, since a VMx refactor can legitimately *increase* line count while removing branching.

`bootstrapper/scripts/` has no `__init__.py` and does not need one — `pythonpath = [".", ".."]` makes it an implicit namespace package. Existing precedent: `tests/test_bounded_subprocess.py:17` does `from scripts import bounded_subprocess`. Be aware the namespace merges **two** directories (`bootstrapper/scripts/` and the repo-root `scripts/`); put the new file in `bootstrapper/scripts/` and it resolves correctly. Do not add an `__init__.py` — that would shadow the repo-root half of the namespace and break `scripts.docs` imports elsewhere.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_loc_report.py`:

```python
"""The LOC/complexity reporter is committed so the #535 before/after
numbers are reproducible rather than asserted."""

from __future__ import annotations

from pathlib import Path

from scripts.loc_report import count_layer, format_report

ROOT = Path(__file__).resolve().parents[1]


def test_count_layer_counts_python_lines(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("z = 3\n", encoding="utf-8")
    assert count_layer(tmp_path)["lines"] == 3


def test_count_layer_ignores_pycache(tmp_path: Path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "junk.py").write_text("noise = 1\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("real = 1\n", encoding="utf-8")
    assert count_layer(tmp_path)["lines"] == 1


def test_count_layer_reports_worst_complexity(tmp_path: Path):
    (tmp_path / "branchy.py").write_text(
        "def f(n):\n"
        + "".join(f"    if n == {i}: return {i}\n" for i in range(12)),
        encoding="utf-8",
    )
    result = count_layer(tmp_path)
    assert result["max_complexity"] >= 12


def test_count_layer_on_missing_dir_is_zero(tmp_path: Path):
    assert count_layer(tmp_path / "nope")["lines"] == 0


def test_format_report_includes_every_layer():
    text = format_report(ROOT)
    for layer in ("wizard/model", "wizard/viewmodel", "wizard/view", "ui/textual"):
        assert layer in text
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd bootstrapper && uv run pytest tests/test_loc_report.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.loc_report'`.

- [ ] **Step 3: Create the script**

Create `bootstrapper/scripts/loc_report.py`:

```python
"""LOC + complexity accounting for the #535 MVVM migration.

Committed so the before/after numbers are reproducible rather than
asserted. Run at every pass boundary:

    cd bootstrapper && uv run python scripts/loc_report.py

LOC is a weak proxy and is tracked because it was asked for. The
primary signal is complexity: a VMx refactor can legitimately increase
line count while removing branching.
"""

from __future__ import annotations

import sys
from pathlib import Path

from radon.complexity import cc_visit

LAYERS = [
    "wizard/model",
    "wizard/viewmodel",
    "wizard/view",
    "ui/textual",
    "wizard",
]


def count_layer(root: Path) -> dict[str, int]:
    """{'files', 'lines', 'max_complexity'} for one directory tree."""
    if not root.exists():
        return {"files": 0, "lines": 0, "max_complexity": 0}
    files = 0
    lines = 0
    worst = 0
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        files += 1
        lines += len(source.splitlines())
        try:
            for block in cc_visit(source):
                worst = max(worst, block.complexity)
        except SyntaxError:
            continue
    return {"files": files, "lines": lines, "max_complexity": worst}


def format_report(bootstrapper_root: Path) -> str:
    rows = ["| layer | files | lines | worst CC |", "|---|---|---|---|"]
    for layer in LAYERS:
        stats = count_layer(bootstrapper_root / layer)
        rows.append(
            f"| {layer} | {stats['files']} | {stats['lines']} "
            f"| {stats['max_complexity']} |"
        )
    return "\n".join(rows)


if __name__ == "__main__":
    print(format_report(Path(__file__).resolve().parents[1]))
    sys.exit(0)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd bootstrapper && uv run pytest tests/test_loc_report.py -v
```

Expected: all pass.

- [ ] **Step 5: Record the Pass 1 numbers**

```bash
cd bootstrapper && uv run python scripts/loc_report.py
```

Paste the table into the Pass 1 PR description alongside the spec's `bf2e8403` baseline (`ui/textual/` 10,143; `wizard/` 1,810; total surface 12,384), labelling the delta as **relocated**, not eliminated. Nothing is eliminated in Pass 1.

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/scripts/loc_report.py bootstrapper/tests/test_loc_report.py
git commit -m "chore(wizard): add reproducible LOC + complexity reporter (#535 Pass 1)

Committed so the before/after numbers are reproducible rather than
asserted. Reports per-layer files/lines/worst-CC.

Pass 1 deltas are RELOCATION, not elimination — the split matters and
the PR description must say which is which.

Refs #535"
```

---

## Task 10: Update `AGENTS.md` for the new package layout

**Files:**
- Modify: `AGENTS.md` (repo root)

**Interfaces:**
- Consumes: the layout established by Tasks 3–7.
- Produces: nothing consumed by later tasks.

**Context:** House rule — docs land in the same slice, never deferred. `CLAUDE.md` is **gitignored** (`.gitignore:149`); `AGENTS.md` is the tracked repo-facing file. Editing `CLAUDE.md` would silently do nothing.

- [ ] **Step 1: Find the bootstrapper architecture section**

```bash
grep -n "ui/state.py\|state_builder\|ui/textual\|Key modules" AGENTS.md | head -20
```

- [ ] **Step 2: Update the module descriptions**

Replace the `ui/state.py`, `ui/state_builder.py` bullets with the three-layer description, and add the boundary rule:

```markdown
- `wizard/model/` — Wizard Model layer: `state.py`, `state_builder.py`,
  `service_discovery.py`, plus the extracted domain rules (`track_rules.py`,
  `cloud_rules.py`, `llm_rules.py`). VMx-free and Textual-free; consumed by
  BOTH the Textual wizard and the `--no-tui` linear flow.
- `wizard/viewmodel/` — VMx ViewModels (arrives in Pass 2 of #535). May import
  `vmx` and `wizard.model`; may never import `textual`.
- `wizard/view/` — Textual screens and widgets. May never import
  `wizard.model` directly; it reads ViewModel state.

The layer direction (`view -> viewmodel -> model`) is enforced by
`bootstrapper/tests/test_wizard_layer_boundaries.py`, which also asserts that
`core/linear_startup.py` never imports `vmx` — that is what makes the
`--no-tui` path structurally VMx-free rather than VMx-free by convention.
```

- [ ] **Step 3: Verify the docs gates still pass**

```bash
cd /Users/kaveh/repos/atlas
python scripts/check_doc_links.py; echo "links: $?"
python scripts/check-docs-drift.py; echo "drift: $?"
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check; echo "regen: $?"
```

Expected: all exit `0`.

- [ ] **Step 4: Run the full suite one final time**

```bash
cd bootstrapper && uv run pytest -q
```

Expected: 3,908 passed plus every test added in Tasks 2, 5, 6, 7, 9.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "docs: describe the wizard MVVM layer split (#535 Pass 1)

AGENTS.md, not CLAUDE.md — the latter is gitignored.

Refs #535"
```

- [ ] **Step 6: Open the Pass 1 PR into `develop`**

```bash
git push
gh pr create --base develop \
  --title "refactor(wizard): extract Model layer + layer-boundary lint (#535 Pass 1)" \
  --body "Pass 1 of #535. No VMx yet — this builds the clean Model that Pass 2 layers ViewModels over.

- New \`wizard/{model}\` package; \`ui/state*.py\` and \`service_discovery.py\` moved (pure \`git mv\`)
- Domain rules extracted with tests: track force-disable, cloud promotion, LLM predicates
- \`_selections_to_args\` routed through them; parity suites unmodified and green
- Layer-boundary lint enforces \`view -> viewmodel -> model\` and that \`linear_startup\` never imports \`vmx\`

LOC deltas in this pass are **relocation**, not elimination.

Refs #535"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Covered by |
|---|---|
| §2 zero-code-behind bar | Pass 4 (separate plan) — the AST lint. Pass 1 builds only the *import* lint (Task 2). |
| §3 layering + §3.1 package structure | Tasks 3, 4, 5, 10 |
| §3.2 enforced import direction | Task 2 |
| §4 VMx abstraction mapping | Pass 2/3 (separate plans) — no VMx in this plan |
| §5 binding + threading | Pass 2 (separate plan) |
| §6 Pass 0 | Task 1 |
| §6 Pass 1 | Tasks 2–10 |
| §7 tier 1 (Model tests) | Tasks 5, 6, 7 |
| §7 tier 5 (architecture lint, mutation-proven) | Task 2 Steps 1–2 |
| §8.1 LOC accounting | Task 9 |
| §8.2 complexity ledger | Task 8 Step 6 measures it; the `.maintenance.json` refresh belongs to Pass 4 |
| §9 Textual upgrade detail | Task 1 |
| §10 dependency posture | Not in this plan — `vmx` is pinned in Pass 2 |
| §12 documentation | Task 10 |

**Gap accepted deliberately:** `wizard/comfyui_steps.py` (515) and `wizard/ray_steps.py` (23) are ViewModel and are not moved here. Moving them before the ViewModel layer exists would leave them in a package that imports nothing — churn without a guard. They move in Pass 3.

**Placeholder scan:** The three `...  # body of X, verbatim` markers in Task 5 Step 4 are deliberate and safe — Step 1 has the engineer print the exact source lines first, and the instruction is explicitly "copy verbatim, do not improve". Writing 75 lines of existing code into the plan would go stale against the file. Everywhere else, code is complete and runnable.

**Type consistency:** `ServiceInfo` is produced by Task 4 and consumed by Task 6 as `Sequence[object]` reading only `.key` — deliberate, so the Task 6 tests can use a 1-field stand-in rather than constructing a real `ServiceInfo`. `CloudResolution` fields (`source`, `api_key`, `models`) are identical in the Task 7 interface block, tests, and truth table. `parse_csv` is named identically in Task 5's interface, module, tests, and the Task 8 consumer. `layer_violations(root, banned) -> list[tuple[str, str]]` matches across Task 2's interface block, implementation, and all four mutation tests.

---

## Passes 2–4 — separate plans

Each produces working, testable software on its own and gets its own plan once its predecessor lands:

- **Pass 2 — adopt VMx.** Pin `vmx==3.23.0`; build the root VM tree over the Pass 1 Model; prove the `RxDispatcher` worker→event-loop seam on `CommandSummary` before converting anything else. Written after Pass 1, because Pass 1's extracted rule surface determines the ViewModel constructor signatures.
- **Pass 3 — convert surfaces.** Risk-ascending: CommandSummary → ServiceTable → CloudApis → prompt steps → `prompt_panel.py` / `option_row.py` → launch and streaming → teardown. Likely two plans; `prompt_panel.py` (1,885 LOC, five of the wizard's twelve worst complexity blocks) deserves its own.
- **Pass 4 — enforce.** The AST no-domain-logic lint with an emptied allowlist, and the `.maintenance.json` refresh deleting the `_build_steps_and_rows` and `_selections_to_args` accepted-signal entries rather than re-accepting them.
