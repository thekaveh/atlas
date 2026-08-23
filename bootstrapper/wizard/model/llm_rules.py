"""LLM domain predicates (#535 Pass 1).

Extracted verbatim from wizard/llm_steps.py. These are Model, not
ViewModel: the CLI flag path honours the same rules, so they must be
reachable without importing the wizard's step builders.

Made public on extraction — a Model boundary is a published surface.
"""

from __future__ import annotations

from typing import Dict


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
    # LLM_ENGINE_TITLE lives in wizard/llm_steps.py (ViewModel) — importing
    # it at module scope here would create a Model -> ViewModel import, so
    # it is imported lazily inside the function instead. Pass 3 turns this
    # into a parameter once the title constant moves to the ViewModel layer.
    from wizard.llm_steps import LLM_ENGINE_TITLE

    v = selections.get(LLM_ENGINE_TITLE)
    if v:
        return v.strip().lower()
    return (env_vars.get("LLM_PROVIDER_SOURCE", "ollama-container-cpu") or "").strip().lower()
