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

Both the linear and the Textual flows run ``core/launch_plan.py``'s stages in
order (#1043), so the plan's order is the launch order.
"""
from __future__ import annotations

from core.launch_plan import CONFIGURE_STAGES


def test_the_launch_plan_generates_secrets_before_deriving_config():
    """Both front ends run this plan; tests/test_tui_pipeline_steps.py proves it."""
    ids = [stage.id for stage in CONFIGURE_STAGES]
    sup = ids.index("validate-supabase-keys")
    enc = ids.index("generate-encryption-keys")
    svc = ids.index("generate-service-configuration")
    kong = ids.index("generate-kong-configuration")
    litellm = ids.index("generate-litellm-configuration")
    assert enc < svc, "generate_encryption_keys must precede generate_service_configuration (LIGHTRAG_PG_URI embeds the rotated password)"
    assert sup < svc, "validate_supabase_keys must precede generate_service_configuration"
    assert enc < kong and enc < litellm, "secrets must be finalized before Kong/LiteLLM config bake them in"
