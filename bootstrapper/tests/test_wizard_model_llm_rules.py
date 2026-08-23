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
