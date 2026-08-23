"""Regressions introduced BY this maintenance run's own fixes.

Every one of these was a fix that made something else worse. They are pinned
together because the failure mode they share — a repair applied at the wrong
layer, or scoped more narrowly than the thing it replaced — is the one this
run kept re-committing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── D2: validate and render are different jobs at different layers ───


@pytest.mark.parametrize("value", [
    "ticket #1", "  padded  ", '"quoted-value"', "'single'", "a#b", "plain", "",
])
def test_a_value_is_not_rendered_twice(value, tmp_path):
    """`_set_scalar` stores the result, then the writer rendered it AGAIN.

    `render_env_value` is not idempotent, so a consumer manifest's
    `ticket #1` was stored as `"ticket #1"` and written as `'"ticket #1"'` —
    `.env` then read the quotes back as part of the secret. The docstring on
    `render_env_value` cites that exact path as the reason it exists.
    """
    from core.config_parser import ConfigParser
    from utils.atomic_write import assert_safe_env_assignment, render_env_assignment

    stored = assert_safe_env_assignment("ROUNDTRIP", value)   # parse boundary
    written = render_env_assignment("ROUNDTRIP", stored)      # write boundary
    assert written == render_env_assignment("ROUNDTRIP", value), "double render"

    (tmp_path / ".env").write_text(f"ROUNDTRIP={written}\n", encoding="utf-8")
    parser = ConfigParser(str(tmp_path))
    parser.env_file_path = tmp_path / ".env"
    assert parser.parse_env_file()["ROUNDTRIP"] == value


def test_a_double_quoted_value_does_not_abort_the_launch():
    """Accepted at the manifest boundary, then REJECTED by the second render.

    `_merge_env_file_overrides` is called uncaught from `start.py`, so the
    ValueError landed in the catch-all as "Unexpected error during startup".
    """
    from utils.atomic_write import assert_safe_env_assignment, render_env_assignment

    stored = assert_safe_env_assignment("K", '"quoted-value"')
    render_env_assignment("K", stored)  # must not raise


def test_the_parse_boundary_still_rejects_an_unencodable_value():
    """Failing at manifest load with an origin beats failing mid-launch."""
    from utils.atomic_write import assert_safe_env_assignment

    with pytest.raises(ValueError, match="reads it back unchanged"):
        assert_safe_env_assignment("K", "a'b\"c #d")


# ── D1: an unusable venv must still self-repair ──────────────────────


def test_an_unusable_venv_is_repaired_not_just_reported():
    """`python3 -m venv <existing dir>` re-runs ensurepip and restores pip.

    Scoping the create branch to "venv absent" turned a pip-less `uv venv`
    plus a newly added dependency from a self-repair into a hard launch
    failure.
    """
    source = (REPO_ROOT / "bootstrapper" / "_run.sh").read_text(encoding="utf-8")
    guard = source.index('if [ "$VENV_USABLE" = "0" ]; then')
    venv_call = source.index('python3 -m venv "$BOOTSTRAPPER_VENV"')
    refresh = source.index('echo "Refreshing Atlas bootstrapper dependencies')
    assert guard < venv_call < refresh, (
        "venv creation is no longer inside the not-usable branch"
    )
    assert 'if [ ! -x "$VENV_PYTHON" ]; then' not in source[guard:venv_call], (
        "creation is gated on absence again, so a broken venv is not repaired"
    )


# ── D3: the source-specific port var beats a stale endpoint ──────────


def test_the_wizard_shows_the_port_for_the_source_being_chosen():
    """The endpoint var is written AFTER the wizard renders.

    Preferring it meant the wizard showed the PREVIOUS run's value — the
    container-internal `:18188` — for every ComfyUI localhost variant.
    """
    from services.topology import get_topology
    from wizard.model.state_builder import resolve_port

    rows = {r.display_name: r for r in get_topology().rows}
    stale = {
        "COMFYUI_ENDPOINT": "http://comfyui:18188",   # container-internal, last run
        "COMFYUI_LOCALHOST_PORT": "8000",
        "COMFYUI_MPS_LOCALHOST_PORT": "8188",
    }
    for source, expected in (("localhost", ":8000"), ("managed-localhost-mps", ":8188")):
        row = rows["ComfyUI"]
        got = resolve_port("ComfyUI", source, getattr(row, "port_var", None), stale)
        assert got == expected, f"{source}: got {got}"


def test_the_port_wiring_table_covers_the_mps_variant():
    """It had no row, so neither the resolver nor the inline input knew it."""
    from wizard.model.state_builder import LOCALHOST_PORT_WIRING

    assert ("ComfyUI", "managed-localhost-mps") in LOCALHOST_PORT_WIRING
    assert LOCALHOST_PORT_WIRING[("ComfyUI", "managed-localhost-mps")] == (
        "COMFYUI_MPS_LOCALHOST_PORT", 8188,
    )


# ── D4: a blank list must not collapse a service's configuration ─────


def test_a_blank_weaviate_module_list_falls_back_to_the_defaults():
    """`.get(key, default)` returns '' for a present-but-blank key.

    Exempting the var from backfill let that blank survive, and every
    module — text2vec-openai, text2vec-ollama, generative-* — was dropped and
    written back durably.
    """
    from services.service_config import _configured_weaviate_modules

    default = "text2vec-openai,text2vec-ollama,multi2vec-clip,generative-openai,generative-ollama"
    # present-but-blank must NOT mean "no modules"
    assert _configured_weaviate_modules({"WEAVIATE_ENABLE_MODULES": ""}, default) == default
    assert _configured_weaviate_modules({}, default) == default
    # ...while a real declaration is honoured
    assert _configured_weaviate_modules({"WEAVIATE_ENABLE_MODULES": "only-this"}, default) == "only-this"


def test_weaviate_modules_is_not_exempt_from_backfill():
    """Nothing in the wizard writes it, so the deselect-all rationale never applied."""
    from start import _USER_OWNED_BLANKABLE

    assert "WEAVIATE_ENABLE_MODULES" not in _USER_OWNED_BLANKABLE
    assert "OLLAMA_USER_MODELS" in _USER_OWNED_BLANKABLE  # the genuine case stays
