"""Model-layer LLM predicates extracted from wizard/llm_steps.py (#535).

These are domain rules, placed in wizard/model so the --no-tui path can
adopt them in a later pass without importing vmx or textual at module
scope. Today the Textual wizard is their only caller.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from wizard.model.llm_rules import (
    LLM_ENGINE_TITLE,
    is_container_ollama,
    is_localhost_or_external,
    parse_csv,
    selected_llm_source,
)

_BOOTSTRAPPER_ROOT = Path(__file__).resolve().parents[1]

# Driver for test_wizard_model_functions_do_not_import_textual, below.
# Module-level (not inlined in the test function) partly for readability
# and partly so the function itself stays a normal size — an early draft
# embedded this as a local string literal and the function's physical
# span (108 lines, driven almost entirely by this string) tripped
# .maintenance.json's functions_over_60_physical_lines signal for no
# real complexity reason (#535 followups review, finding R5 follow-up).
#
# Runs in a subprocess with a fresh interpreter — see the test's
# docstring for why in-process would prove nothing. Imports every
# wizard/model/ submodule, calls every public, locally-defined,
# module-level function with a type-appropriate trivial argument (only
# entering the function body matters here; business-logic errors from
# trivial args are swallowed on purpose), then asserts textual never
# landed in sys.modules.
_TEXTUAL_FREE_DRIVER = """
import sys

driver_calls = {
    ("llm_rules", "parse_csv"): lambda f: f("a,b"),
    ("llm_rules", "is_localhost_or_external"): lambda f: f("ollama-localhost"),
    ("llm_rules", "is_container_ollama"): lambda f: f("ollama-container-cpu"),
    ("llm_rules", "selected_llm_source"): lambda f: f({}, {}),
    ("cloud_rules", "resolve_cloud_provider"): lambda f: f(
        provider_key="openai", secret_value=None, selected_models=None,
        existing_key_set=False, existing_source="disabled",
    ),
    ("state_builder", "lookup_service_meta"): lambda f: f("weaviate"),
    ("state_builder", "service_extras"): lambda f: f("weaviate"),
    ("state_builder", "resolve_localhost_port"): lambda f: f(None, {}),
    ("state_builder", "resolve_port"): lambda f: f("weaviate", "container", None, {}),
    ("state_builder", "alias_for"): lambda f: f("weaviate"),
    ("state_builder", "all_services"): lambda f: f(),
    ("state_builder", "all_cloud_apis"): lambda f: f(),
    ("state_builder", "cloud_api_status_text"): lambda f: f(True, True),
    ("state_builder", "build_app_state"): lambda f: f(
        __import__("core.config_parser", fromlist=["ConfigParser"]).ConfigParser()
    ),
}

called = []
for modname in ("state", "state_builder", "service_discovery", "cloud_rules", "llm_rules"):
    mod = __import__(f"wizard.model.{modname}", fromlist=["_"])
    import inspect
    for name, obj in inspect.getmembers(mod, inspect.isfunction):
        if name.startswith("_"):
            continue
        if obj.__module__ != mod.__name__:
            continue  # skip re-exported names, only test locally-defined ones
        key = (modname, name)
        thunk = driver_calls.get(key)
        assert thunk is not None, f"no driver entry for {modname}.{name} -- add one"
        try:
            thunk(obj)
        except Exception:
            pass  # business-logic errors from trivial args are fine; only
                   # sys.modules pollution from entering the function body
                   # is under test here.
        called.append(f"{modname}.{name}")

assert called, "no public functions discovered -- driver is stale"
textual_modules = sorted(m for m in sys.modules if m == "textual" or m.startswith("textual."))
assert not textual_modules, (
    f"calling {called} pulled textual into sys.modules: {textual_modules}"
)
print("OK", len(called), "functions called, 0 textual modules")
"""


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

    result = selected_llm_source(env, {LLM_ENGINE_TITLE: "ollama-localhost"})
    assert result == "ollama-localhost"


def test_llm_engine_title_reexported_from_llm_steps_is_the_same_object():
    """wizard/llm_steps.py imports LLM_ENGINE_TITLE from here (#535
    followups review, finding R5) rather than defining its own copy — a
    regression back to two separate string literals would silently
    reintroduce a drift risk identical to what R2 fixed for parse_csv."""
    from wizard.llm_steps import LLM_ENGINE_TITLE as reexported

    assert reexported is LLM_ENGINE_TITLE


def test_selected_llm_source_falls_back_to_env():
    env = {"LLM_PROVIDER_SOURCE": "ollama-container-gpu"}
    assert selected_llm_source(env, {}) == "ollama-container-gpu"


def test_llm_rules_has_no_module_scope_framework_imports():
    """Cheap file-local smoke check.

    This scans llm_rules.py's own source for import/from lines naming
    vmx or textual, so a regression here fails fast and close to the
    module under test. It only catches MODULE-SCOPE imports (lines that
    literally start with ``import``/``from``); it does not by itself
    prove the module never touches ``textual`` when called — that used
    to matter here (``selected_llm_source`` did a deferred,
    function-scope import of ``wizard.llm_steps``, invisible to a
    source scan, which pulled ``textual`` into ``sys.modules``
    transitively), but the #535 followups review (finding R5) removed
    that import entirely by moving ``LLM_ENGINE_TITLE`` into this
    module. ``test_wizard_model_functions_do_not_import_textual`` below
    is the runtime-level proof; this test is intentionally
    source-based, not an AST parse (that is the layer lint's job) — it
    only matches lines that actually start with ``import``/``from`` so
    it can't be tripped by the module's own docstring prose mentioning
    those words.

    The authoritative, repo-wide static guard is
    ``tests/test_wizard_layer_boundaries.py::test_model_layer_imports_no_framework``,
    which AST-parses every file under wizard/model/ and catches both
    ``import textual`` and ``from textual.widgets import Button`` forms
    plus submodules — but it too is module-scope-only and would not
    have caught the deferred import that used to exist here (a
    deferred import's target module name, ``wizard.llm_steps``, does
    not itself contain ``vmx``/``textual``, so it wasn't banned by that
    checker's name list either — it takes a runtime check to catch a
    transitive, deferred pull like that).
    """
    import re

    import wizard.model.llm_rules as mod

    source = open(mod.__file__, encoding="utf-8").read()
    banned = re.compile(r"^\s*(?:import|from)\s+(?:vmx|textual)\b", re.MULTILINE)
    match = banned.search(source)
    assert match is None, f"found banned import line: {match.group(0)!r}"


def test_wizard_model_functions_do_not_import_textual():
    """Runtime proof for #535 followups review, finding R5.

    Static AST/source scans (the two tests above, plus
    test_wizard_layer_boundaries.py) only see MODULE-SCOPE imports. The
    actual regression this finding fixed was a DEFERRED, function-scope
    import inside ``selected_llm_source`` that pulled ~139 ``textual.*``
    submodules into ``sys.modules`` the first time it was called —
    invisible to every static check above.

    ``_TEXTUAL_FREE_DRIVER`` (module-level, above) does the real work:
    imports every wizard/model/ submodule, calls every public,
    locally-defined, module-level function, and checks ``sys.modules``
    afterward — in a SUBPROCESS, because countless other tests in this
    suite already import ``textual`` for unrelated reasons, so
    in-process would prove nothing (same reason
    ``test_litellm_init_loose_imports.py`` uses a subprocess). See its
    comment for what's deliberately out of reach (``ServiceDiscovery``
    is a class, not a module-level function) and why that doesn't
    weaken the proof.
    """
    result = subprocess.run(
        [sys.executable, "-c", _TEXTUAL_FREE_DRIVER],
        cwd=str(_BOOTSTRAPPER_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"subprocess failed (rc={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout, result.stdout
