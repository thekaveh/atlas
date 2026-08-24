"""LLM domain predicates (#535 Pass 1).

Extracted verbatim from wizard/llm_steps.py. These are Model, not
ViewModel: they are domain rules, kept here so the `--no-tui` path can
adopt them in a later pass without importing the wizard's step
builders. Most of this module is called only from the Textual wizard
path (``wizard/llm_steps.py``).

``parse_csv`` is the exception: it's the canonical definition consolidated
from three byte-identical copies (#535 followups review, finding R2) —
``wizard/llm_steps.py`` already imported it from here as ``_csv``;
``utils/comfyui_resolver.py`` (host-only) now does too. A fourth,
NON-consolidatable copy remains inlined in ``utils/model_resolver.py``'s
loose-import (container) branch — that branch runs inside litellm-init,
where only ``bootstrapper/utils/`` is bind-mounted (as ``/catalog``), not
``bootstrapper/wizard/``, so it structurally cannot import this module;
see the comment there.

``LLM_ENGINE_TITLE`` used to live in ``wizard/llm_steps.py`` (ViewModel),
with ``selected_llm_source`` reaching it via a deferred, function-scope
import specifically to avoid a Model -> ViewModel import at module scope.
That import was real at RUNTIME, not just on paper: calling
``selected_llm_source`` pulled the whole of ``wizard.llm_steps`` in,
which module-imports ``ui.textual.widgets.prompt_panel``, which imports
``textual`` -- ~139 ``textual.*`` submodules landed in ``sys.modules`` the
first time anyone called this one Model-layer function (#535 followups
review, finding R5). Fixed by moving the constant here instead: it is
genuinely Model-layer data (the wizard-step title string used purely as a
``selections`` dict lookup key inside this function -- nothing in
``wizard/llm_steps.py`` itself ever referenced its own copy of it before
this fix; the step's real title is generated at
``f"{svc.display_name}  ·  source"`` in ``ui/textual/integration.py``,
which happens to produce the identical string). ``wizard/llm_steps.py``
now imports it from here instead of defining it, so there is exactly one
definition and the deferred import -- and the "one documented exception"
this package used to carry -- are both gone.
"""

from __future__ import annotations

from typing import Dict

#: Wizard-step title for the "LLM Engine" source picker, used as the
#: ``selections`` dict lookup key by ``selected_llm_source`` below. See
#: this module's docstring for why it lives here rather than in
#: ``wizard/llm_steps.py`` (which imports it from here for backward
#: compatibility).
LLM_ENGINE_TITLE = "LLM Engine  ·  source"


def parse_csv(val: str | None) -> list[str]:
    """Split a comma-separated string into a list of non-empty stripped tokens."""
    if not val:
        return []
    return [s.strip() for s in val.split(",") if s.strip()]


def is_localhost_or_external(src: str) -> bool:
    return "localhost" in src or "external" in src


def is_container_ollama(src: str) -> bool:
    return src.startswith("ollama-container-")


def selected_llm_source(env_vars: Dict[str, str], selections: dict) -> str:
    v = selections.get(LLM_ENGINE_TITLE)
    if v:
        return v.strip().lower()
    return (env_vars.get("LLM_PROVIDER_SOURCE", "ollama-container-cpu") or "").strip().lower()
