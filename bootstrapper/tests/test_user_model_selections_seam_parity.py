"""Cross-cutting regression guard for the user-model selection plumbing.

PR #16 caught a class of bug where a CLI flag was declared but never
plumbed into the dict passed to apply_*. The Ollama-side pattern has
two seams:

  1. `@click.option('--<feature>-...')` + `main()` signature param
  2. `if <kwarg> is not None: user_model_selections['<ENV>'] = <kwarg>`

This test introspects start.py + integration.py and asserts both seams
agree for every `*_USER_MODELS` env var (and the parallel ComfyUI
custom-models-file plumbing).

When this test fails, either:
  - start.py is missing the `user_model_selections[KEY] = kwarg` assignment, OR
  - integration.py / start.py disagree on the env var name.
"""

# LightRAG and TEI Reranker (added 2026-06-05) intentionally have NO model
# picker step in the wizard. LightRAG inherits LITELLM_DEFAULT_MODEL /
# LITELLM_EMBEDDING_MODEL via lightrag-init at startup; TEI Reranker uses a
# static TEI_RERANKER_MODEL_ID default. Neither needs the --<svc>-models
# four-seam pattern guarded by this file's tests. Do not add them here.

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
START_PY = REPO_ROOT / "bootstrapper" / "start.py"
INTEGRATION_PY = REPO_ROOT / "bootstrapper" / "ui" / "textual" / "integration.py"


def _find_user_model_keys_in_start_py() -> set[str]:
    """Find every string literal KEY where `user_model_selections[KEY] = ...` appears."""
    tree = ast.parse(START_PY.read_text())
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id == "user_model_selections":
                slot = node.slice
                if isinstance(slot, ast.Constant) and isinstance(slot.value, str):
                    keys.add(slot.value)
    return keys


def test_user_model_selections_includes_comfyui():
    keys = _find_user_model_keys_in_start_py()
    assert "COMFYUI_USER_MODELS" in keys, (
        "start.py's user_model_selections dict is missing COMFYUI_USER_MODELS. "
        "Without this, --comfyui-models is a silent no-op."
    )


def test_user_model_selections_includes_ollama():
    """Sanity — also covers the existing Ollama plumbing."""
    keys = _find_user_model_keys_in_start_py()
    assert "OLLAMA_USER_MODELS" in keys


def test_user_model_selections_includes_comfyui_custom_models_file():
    keys = _find_user_model_keys_in_start_py()
    assert "COMFYUI_CUSTOM_MODELS_FILE" in keys


def test_integration_emits_comfyui_user_models():
    """integration.py._selections_to_args must return a dict whose outer
    wrapper key set includes ``comfyui_user_models`` — wizard_screen.py
    later does ``stack_options.get("comfyui_user_models", {})`` to unpack
    it back into the env-write call (see [[project-cli-source-flag-three-seams]]
    seam #4). Look for the key on a Dict literal node, not a Subscript —
    the wrapper-key lives on the returned dict at integration.py:606-614,
    NOT on a subscript expression.
    """
    tree = ast.parse(INTEGRATION_PY.read_text())
    dict_literal_keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                dict_literal_keys.add(k.value)
    assert "comfyui_user_models" in dict_literal_keys, (
        "integration.py._selections_to_args must return a dict with key "
        "'comfyui_user_models'. Without it, wizard_screen's "
        "stack_options.get('comfyui_user_models') returns empty and ComfyUI "
        "wizard selections silently never reach apply_user_model_selections."
    )


def test_wizard_screen_consumes_comfyui_user_models():
    """The TUI's launch inputs must call `.get("comfyui_user_models", ...)`
    on stack_options for the plan's 'Apply user model selections' stage. Without this,
    wizard-driven ComfyUI selections silently drop on confirm — the P1
    bug fixed alongside this test.
    """
    WIZARD_SCREEN = REPO_ROOT / "bootstrapper" / "ui" / "textual" / "screens" / "wizard_screen.py"
    tree = ast.parse(WIZARD_SCREEN.read_text())
    keys_used_in_get_calls: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys_used_in_get_calls.add(node.args[0].value)
    for key in ("cloud_user_models", "ollama_user_models", "comfyui_user_models", "user_env_writes"):
        assert key in keys_used_in_get_calls, (
            f"wizard_screen.py must call `.get({key!r}, ...)` on stack_options "
            f"when it builds the launch plan's user_model_selections. Commented-out or string "
            f"literals in other contexts won't satisfy this (AST-checked)."
        )


def test_tui_launch_carries_user_env_writes_bucket():
    """start.py's TUI-launch stack_options must carry a `user_env_writes`
    bucket containing every user_model_selections entry that doesn't
    match the cosmetic *_USER_MODELS / OLLAMA_* filters
    (COMFYUI_CUSTOM_MODELS_FILE, RAY_WORKER_COUNT,
    PROMETHEUS_RETENTION_DAYS, SPARK_WORKER_COUNT). Without it the
    ./start.sh --flag <value> path under a TUI-capable terminal
    silently drops these flags.

    Locates the specific `stack_options = {...}` Dict literal (the one
    assigned to the local in main()) and confirms its keys include
    'user_env_writes' AND that the value is a DictComp filtering on
    user_model_selections — broader AST walks would green-light any
    unrelated dict literal that happens to use the same key name.
    """
    tree = ast.parse(START_PY.read_text())
    # Find an `Assign(targets=[Name("stack_options")], value=Dict)`
    stack_options_dicts: list[ast.Dict] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "stack_options"
            and isinstance(node.value, ast.Dict)
        ):
            stack_options_dicts.append(node.value)
    assert stack_options_dicts, (
        "Expected at least one `stack_options = {...}` Dict assignment in "
        "start.py; none found."
    )
    found_user_env_writes_dictcomp = False
    for d in stack_options_dicts:
        for k, v in zip(d.keys, d.values):
            if not (isinstance(k, ast.Constant) and k.value == "user_env_writes"):
                continue
            # Value must be a DictComp iterating user_model_selections.items()
            if (
                isinstance(v, ast.DictComp)
                and any(
                    isinstance(g.iter, ast.Call)
                    and isinstance(g.iter.func, ast.Attribute)
                    and g.iter.func.attr == "items"
                    and isinstance(g.iter.func.value, ast.Name)
                    and g.iter.func.value.id == "user_model_selections"
                    for g in v.generators
                )
            ):
                found_user_env_writes_dictcomp = True
                break
        if found_user_env_writes_dictcomp:
            break
    assert found_user_env_writes_dictcomp, (
        "stack_options must declare a 'user_env_writes' key whose value "
        "is a DictComp over user_model_selections.items() filtering the "
        "OLLAMA_*/_USER_MODELS-shaped keys away. A plain stub or unrelated "
        "dict literal won't satisfy this — the catch-all must actually "
        "ingest user_model_selections so RAY_WORKER_COUNT / "
        "PROMETHEUS_RETENTION_DAYS / SPARK_WORKER_COUNT / "
        "COMFYUI_CUSTOM_MODELS_FILE land in .env on the TUI-launch path."
    )


def test_integration_inner_keys_use_uppercase_env_var_names():
    """integration.py builds *_user_models / cloud_api_keys dicts whose KEYS are
    unpacked directly into apply_user_model_selections (env var names). A
    lowercase key like ``comfyui_user_models["comfyui_user_models"]`` would
    create a literal ``comfyui_user_models=`` line in .env instead of the
    intended ``COMFYUI_USER_MODELS=`` — silently breaking wizard persistence
    while CLI persistence (which constructs the dict separately in start.py)
    still works. The bug shipped in PR #17 and was caught in the sixth-
    convergence post-merge audit.

    Rule: every string-literal subscript assignment to an env-bearing dict
    must use UPPER_SNAKE_CASE. Variable subscripts (`dict[var]`) are skipped
    — those carry runtime env-var names from cloud_providers config.
    """
    ENV_BEARING_DICTS = {
        "ollama_user_models",
        "comfyui_user_models",
        "cloud_user_models",
        "cloud_api_keys",
    }
    UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9_]*$")
    tree = ast.parse(INTEGRATION_PY.read_text())
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        if node.value.id not in ENV_BEARING_DICTS:
            continue
        slot = node.slice
        if not (isinstance(slot, ast.Constant) and isinstance(slot.value, str)):
            continue
        if not UPPER_SNAKE.match(slot.value):
            violations.append(
                f"{node.value.id}[{slot.value!r}] at integration.py:{node.lineno} — "
                f"key must be UPPER_SNAKE_CASE env-var name"
            )
    assert not violations, (
        "Lowercase keys in env-bearing dicts silently break .env persistence:\n"
        + "\n".join(violations)
    )
