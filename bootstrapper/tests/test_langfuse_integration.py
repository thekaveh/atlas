"""Langfuse wiring: the two failure modes here are both SILENT.

#928 — `langfuse-web` bound to `$HOSTNAME`, which Docker sets to the
container ID, so the server listened on that container's eth0 IP only and
nothing was on loopback. The loopback healthcheck could never pass, the
container sat `(unhealthy)` forever, and `./start.sh` exited non-zero even
though the app was fully functional. Upstream's compose does not hit this
because it ships no healthcheck on langfuse-web at all.

#929 — LiteLLM got `LANGFUSE_BASE_URL`, but the langfuse-python **v2** SDK
bundled in the pinned LiteLLM image reads only `LANGFUSE_HOST` and defaults
to `https://cloud.langfuse.com`. Traces were shipped to the public cloud
with locally-generated keys, rejected, and dropped — while LiteLLM logged
the callback as initialised and every call succeeded. Nothing surfaced
locally at all, which is what made it survive.

Verified in the v2.57.13 source:
    os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
v4 prefers ``LANGFUSE_BASE_URL`` and keeps ``LANGFUSE_HOST`` as a
deprecated alias, so setting both is what survives an image bump.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LANGFUSE_COMPOSE = REPO_ROOT / "services" / "langfuse" / "compose.yml"
LITELLM_COMPOSE = REPO_ROOT / "services" / "litellm" / "compose.yml"


def _services(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


# ─── #928: the web container must bind all interfaces ────────────────


def test_langfuse_web_binds_all_interfaces() -> None:
    env = _services(LANGFUSE_COMPOSE)["langfuse-web"]["environment"]
    assert env.get("HOSTNAME") == "0.0.0.0", (
        "langfuse-web must pin HOSTNAME=0.0.0.0. Next.js standalone binds "
        "process.env.HOSTNAME, and Docker sets it to the container ID — so "
        "without this the server listens on the container IP only and the "
        "loopback healthcheck can never pass (#928)."
    )


def test_langfuse_web_healthcheck_probes_loopback() -> None:
    """The bind fix and the probe must stay consistent.

    Pinned together deliberately: if someone later changes the probe to
    target $HOSTNAME instead, the HOSTNAME pin becomes load-bearing for a
    different reason and this pair should be re-read as a unit.
    """
    health = _services(LANGFUSE_COMPOSE)["langfuse-web"]["healthcheck"]
    probe = " ".join(health["test"])
    assert "localhost" in probe or "127.0.0.1" in probe
    assert "/api/public/health" in probe


def test_langfuse_worker_shares_the_bind_setting() -> None:
    """Both come from the same Langfuse codebase and share the env anchor;
    if they ever diverge, that is a decision worth making explicitly."""
    services = _services(LANGFUSE_COMPOSE)
    assert services["langfuse-worker"]["environment"].get("HOSTNAME") == "0.0.0.0"


# ─── #929: the SDK reads LANGFUSE_HOST, not LANGFUSE_BASE_URL ────────


def test_litellm_sets_the_host_var_the_v2_sdk_actually_reads() -> None:
    env = _services(LITELLM_COMPOSE)["litellm"]["environment"]
    assert env.get("LANGFUSE_HOST") == "${LANGFUSE_ENDPOINT:-}", (
        "The langfuse-python v2 SDK bundled in the LiteLLM image reads "
        "LANGFUSE_HOST and silently defaults to https://cloud.langfuse.com. "
        "Without this, traces leave the machine and are dropped, with no "
        "error surfaced anywhere (#929)."
    )


def test_litellm_keeps_the_v4_base_url_var_too() -> None:
    """Forward compatibility: v4 prefers LANGFUSE_BASE_URL. Dropping either
    name re-breaks tracing on one side of an image bump."""
    env = _services(LITELLM_COMPOSE)["litellm"]["environment"]
    assert env.get("LANGFUSE_BASE_URL") == "${LANGFUSE_ENDPOINT:-}"


def test_both_host_vars_resolve_to_the_same_endpoint() -> None:
    """They must never drift apart — two different hosts would be worse
    than one wrong one, because which wins depends on the SDK version."""
    env = _services(LITELLM_COMPOSE)["litellm"]["environment"]
    assert env["LANGFUSE_HOST"] == env["LANGFUSE_BASE_URL"]


def test_langfuse_credentials_travel_with_the_host() -> None:
    env = _services(LITELLM_COMPOSE)["litellm"]["environment"]
    assert env.get("LANGFUSE_PUBLIC_KEY") == "${LANGFUSE_PUBLIC_KEY:-}"
    assert env.get("LANGFUSE_SECRET_KEY") == "${LANGFUSE_SECRET_KEY:-}"


def test_tracing_vars_are_inert_when_langfuse_is_disabled() -> None:
    """All four default to empty, so the litellm service is unaffected when
    Langfuse is off — the callback is what activates tracing, not these."""
    env = _services(LITELLM_COMPOSE)["litellm"]["environment"]
    for name in (
        "LANGFUSE_HOST",
        "LANGFUSE_BASE_URL",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ):
        assert env[name].endswith(":-}"), f"{name} must default to empty"
