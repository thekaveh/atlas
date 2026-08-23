"""Unit tests for wizard.model.state_builder.resolve_port — focuses on the
localhost path which now reads PORT vars directly instead of regex-
extracting from a URL var (the URL vars are gone)."""

from __future__ import annotations

import pytest

from wizard.model.state_builder import resolve_port


@pytest.mark.parametrize("display_name,source,port_env_var,port_value", [
    ("ComfyUI", "localhost", "COMFYUI_LOCALHOST_PORT", "9000"),
    ("Document Processor", "docling-localhost", "DOCLING_LOCALHOST_PORT", "63099"),
    ("LLM Engine", "ollama-localhost", "OLLAMA_LOCALHOST_PORT", "11500"),
])
def test_resolve_port_for_localhost_reads_port_var(
    display_name, source, port_env_var, port_value
):
    """For a localhost-source row, the displayed port is the value of
    the row's localhost_port_var in env (formatted as ``:<port>``)."""
    env = {port_env_var: port_value}
    result = resolve_port(display_name, source=source, port_var=None, env=env)
    assert result == f":{port_value}", (
        f"resolve_port should return :{port_value}; got {result!r}"
    )


def test_resolve_port_for_localhost_returns_none_when_var_unset():
    """An empty PORT var falls back to nothing — wizard's pending row
    state will surface the manifest default via .env.example backfill
    before the next read."""
    result = resolve_port("ComfyUI", source="localhost", port_var=None, env={})
    assert result is None


def test_resolve_port_for_disabled_returns_none():
    """Disabled rows show no port (existing baseline)."""
    assert resolve_port("ComfyUI", "disabled", None, {}) is None


def test_resolve_port_for_container_source_uses_port_var():
    """Container source falls through to the port_var path (no change
    from baseline behaviour)."""
    env = {"COMFYUI_PORT": "63010"}
    result = resolve_port("ComfyUI", source="container-cpu", port_var="COMFYUI_PORT", env=env)
    assert result == ":63010"


def test_appstate_brand_defaults_match_globals_manifest():
    """The in-code ``AppState`` brand-field defaults MUST mirror the
    ``BRAND_*`` env-var defaults declared in
    ``services/globals/service.yml``.

    Drift between the two layers (caught twice in the 2026-05-28
    audit: a missing BRAND_AUTHOR_EMAIL on the manifest side, and 4
    stale strings on the AppState side) silently shows different
    brand metadata depending on whether the user blanked out the
    env value or removed it entirely. Both layers consume the same
    source of truth, so they must stay aligned.
    """
    from pathlib import Path

    import yaml

    from wizard.model.state import AppState

    repo_root = Path(__file__).resolve().parent.parent.parent
    globals_manifest = repo_root / "services" / "globals" / "service.yml"
    # BRAND_LOGO_FILE points at a block-art file and is resolved at render time
    # by utils.brand_logo — it is intentionally NOT modeled as an AppState brand
    # field, so it is excluded from this AppState↔manifest parity check.
    _not_in_appstate = {"BRAND_LOGO_FILE"}
    manifest_brand = {
        e["name"]: e.get("default", "")
        for e in yaml.safe_load(globals_manifest.read_text())["env"]
        if e["name"].startswith("BRAND_") and e["name"] not in _not_in_appstate
    }

    state = AppState()
    mapping = {
        "BRAND_NAME": state.brand_name,
        "BRAND_TAGLINE": state.tagline,
        "BRAND_VERSION": state.version,
        "BRAND_AUTHOR": state.creator,
        "BRAND_AUTHOR_EMAIL": state.creator_email,
        "BRAND_LICENSE": state.license,
        "BRAND_REPO_URL": state.repo_url,
    }

    assert set(mapping) == set(manifest_brand), (
        f"BRAND_* key set drift — manifest has {sorted(manifest_brand)}, "
        f"AppState mirrors {sorted(mapping)}"
    )
    for key, app_value in mapping.items():
        assert manifest_brand[key] == app_value, (
            f"{key}: manifest default {manifest_brand[key]!r} != "
            f"AppState default {app_value!r}"
        )
