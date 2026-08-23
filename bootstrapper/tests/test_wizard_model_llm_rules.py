"""Model-layer LLM predicates extracted from wizard/llm_steps.py (#535).

These are domain rules, placed in wizard/model so the --no-tui path can
adopt them in a later pass without importing vmx or textual at module
scope. Today the Textual wizard is their only caller.
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


def test_llm_rules_has_no_module_scope_framework_imports():
    """Cheap file-local smoke check — NOT proof the module is framework-free
    at runtime.

    This scans llm_rules.py's own source for import/from lines naming
    vmx or textual, so a regression here fails fast and close to the
    module under test. It only catches MODULE-SCOPE imports (lines that
    literally start with ``import``/``from``); it does NOT prove the
    module never touches ``textual`` when called — ``selected_llm_source``
    does a deferred, function-scope import of ``wizard.llm_steps``, which
    pulls ``textual`` into ``sys.modules`` transitively the first time it
    runs. See this module's docstring and ``wizard/model/__init__.py`` for
    that documented exception. This check is intentionally source-based,
    not an AST parse (that is the layer lint's job) — it only matches
    lines that actually start with ``import``/``from`` so it can't be
    tripped by the module's own docstring prose mentioning those words.

    The authoritative, repo-wide guard is
    ``tests/test_wizard_layer_boundaries.py::test_model_layer_imports_no_framework``,
    which AST-parses every file under wizard/model/ and catches both
    ``import textual`` and ``from textual.widgets import Button`` forms
    plus submodules — but it is also a static, module-scope-only check
    and cannot see the deferred import either. Treat both as belts on
    module-scope imports, not a runtime guarantee.
    """
    import re

    import wizard.model.llm_rules as mod

    source = open(mod.__file__, encoding="utf-8").read()
    banned = re.compile(r"^\s*(?:import|from)\s+(?:vmx|textual)\b", re.MULTILINE)
    match = banned.search(source)
    assert match is None, f"found banned import line: {match.group(0)!r}"
