"""Guard: secret generation/rotation must run BEFORE any step that bakes a
secret into a derived config value.

Root cause of the lightrag-init "password authentication failed for user
supabase_admin" launch failure: ``generate_service_configuration`` embeds
``SUPABASE_DB_PASSWORD`` (and ``GRAPH_DB_PASSWORD`` / ``REDIS_PASSWORD``) into
``LIGHTRAG_PG_URI`` / ``LIGHTRAG_NEO4J_PASSWORD`` / ``LIGHTRAG_REDIS_URI``
(services/service_config.py). It used to run BEFORE ``generate_encryption_keys``
rotated those passwords, so on a cold start (or a first-run placeholder→real
upgrade) the derived URI carried the STALE password while the Postgres volume
was initdb'd with the NEW one — auth then failed against a perfectly fresh
volume, which the launcher mis-reported as a "stale volume".

Both the linear (start.py main) and the Textual (wizard_screen pipeline) flows
run their steps in source order, so a source-position check is a faithful
ordering guard.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _pos(text: str, needle: str) -> int:
    i = text.find(needle)
    assert i != -1, f"marker not found (did the call/label change?): {needle!r}"
    # ensure it's unique so the position is unambiguous
    assert text.find(needle, i + 1) == -1, f"marker not unique: {needle!r}"
    return i


def test_linear_flow_generates_secrets_before_deriving_config():
    src = (
        REPO / "bootstrapper" / "core" / "linear_startup.py"
    ).read_text(encoding="utf-8")
    sup = _pos(src, "starter.validate_supabase_keys(cold_start=options.cold)")
    enc = _pos(src, "starter.generate_encryption_keys(cold_start=options.cold)")
    svc = _pos(src, "starter.generate_service_configuration,")
    kong = _pos(src, "starter.generate_kong_configuration,")
    litellm = _pos(src, "starter.generate_litellm_configuration,")
    assert enc < svc, "generate_encryption_keys must precede generate_service_configuration (LIGHTRAG_PG_URI embeds the rotated password)"
    assert sup < svc, "validate_supabase_keys must precede generate_service_configuration"
    assert enc < kong and enc < litellm, "secrets must be finalized before Kong/LiteLLM config bake them in"


def test_tui_pipeline_generates_secrets_before_deriving_config():
    src = (REPO / "bootstrapper" / "ui" / "textual" / "screens" / "wizard_screen.py").read_text(encoding="utf-8")
    sup = _pos(src, '"Validate Supabase keys"')
    enc = _pos(src, '"Generate encryption keys"')
    svc = _pos(src, '"Generate service configuration"')
    kong = _pos(src, '"Generate Kong configuration"')
    litellm = _pos(src, '"Generate LiteLLM configuration"')
    assert enc < svc, "TUI: 'Generate encryption keys' must precede 'Generate service configuration'"
    assert sup < svc, "TUI: 'Validate Supabase keys' must precede 'Generate service configuration'"
    assert enc < kong and enc < litellm, "TUI: secrets must be finalized before Kong/LiteLLM config steps"
