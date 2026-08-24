"""Wizard Model layer — no module-scope vmx or textual imports (#535).

Everything here is consumed by BOTH the Textual wizard and the --no-tui
linear flow. Nothing in this package may import ``vmx`` or ``textual`` at
module scope; ``tests/test_wizard_layer_boundaries.py`` enforces that via
a static AST scan.

One documented exception: ``llm_rules.selected_llm_source`` does a
deferred, function-scope import of ``wizard.llm_steps`` (a ViewModel
module) to reach the ``LLM_ENGINE_TITLE`` constant. That import runs at
call time, not at package-import time, so the static layer check does not
see it — but calling ``selected_llm_source`` pulls ``textual`` into
``sys.modules`` transitively. This is deliberate for now; moving the
constant into the Model layer is Pass 3 work.

No re-exports here (#535 followups review, finding R3): this package used
to re-export 16 names from its submodules for a ``from wizard.model import
X`` convenience import that, repo-wide, nothing ever used — every actual
importer already goes through the submodule path (``from
wizard.model.state_builder import build_app_state``, etc.). The re-export
block's only real effect was forcing ``core.config_parser`` +
``services.topology`` to load as a side effect of importing ANY
``wizard.model.*`` submodule (even unrelated ones, e.g.
``wizard.model.llm_rules`` for two string constants), since importing a
submodule always runs its package's ``__init__.py`` first. Import the
submodule you need directly instead.
"""

from __future__ import annotations
