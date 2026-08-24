"""Wizard Model layer — no module-scope vmx or textual imports (#535).

Everything here is consumed by BOTH the Textual wizard and the --no-tui
linear flow. Nothing in this package may import ``vmx`` or ``textual`` at
module scope; ``tests/test_wizard_layer_boundaries.py`` enforces that via
a static AST scan.

The package used to carry a documented exception here:
``llm_rules.selected_llm_source`` did a deferred, function-scope import
of ``wizard.llm_steps`` (a ViewModel module) to reach the
``LLM_ENGINE_TITLE`` constant — invisible to the static layer check
(which only sees module-scope imports), but real at runtime: calling
``selected_llm_source`` pulled ``textual`` into ``sys.modules``
transitively (~139 submodules). Fixed in the #535 followups review
(finding R5) by moving ``LLM_ENGINE_TITLE`` into ``llm_rules.py`` itself
— it was genuinely Model-layer data (a ``selections`` dict lookup key)
that had simply been defined in the wrong layer. No exception remains.

No re-exports here (#535 followups review, finding R3): this package used
to re-export 16 names from its submodules for a ``from wizard.model import
X`` convenience import that, repo-wide, nothing ever used — every actual
importer already goes through the submodule path (``from
wizard.model.state_builder import build_app_state``, etc.). The re-export
block's only real effect was forcing ``core.config_parser`` +
``services.topology`` to load as a side effect of importing ANY
``wizard.model.*`` submodule (even an unrelated one, e.g.
``ui/textual/widgets/prompt_panel.py`` importing just the two
``SECRET_KEEP``/``SECRET_CLEAR`` sentinels from ``wizard.model.cloud_rules``),
since importing a submodule always runs its package's ``__init__.py``
first. Import the submodule you need directly instead.
"""

from __future__ import annotations
