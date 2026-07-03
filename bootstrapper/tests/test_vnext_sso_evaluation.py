from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EVALUATION = REPO_ROOT / "docs" / "strategy" / "authentik-sso-pilot-evaluation.md"


def test_authentik_sso_evaluation_records_required_decisions() -> None:
    text = EVALUATION.read_text(encoding="utf-8")

    required_phrases = (
        "Authentik-first",
        "Keycloak as the heavier enterprise alternative",
        "route-level pilot",
        "one non-critical route",
        "Kong OpenID Connect plugin is Enterprise only",
        "forward auth",
        "no broad migration",
        "`AUTHENTIK_SOURCE=container|disabled`",
        "disabled by default",
        "category: `infra`",
        "track: `identity-security`",
        "Kong alias: `auth.localhost`",
        "Postgres",
        "Redis",
        "Service Admission Contract",
        "Acceptance Criteria For The Future Implementation Ticket",
    )

    for phrase in required_phrases:
        assert phrase in text


def test_authentik_sso_evaluation_covers_current_service_implications() -> None:
    text = EVALUATION.read_text(encoding="utf-8")

    for service_name in (
        "Open WebUI",
        "JupyterHub",
        "n8n",
        "MinIO",
        "Neo4j",
        "Kong",
        "Supabase Auth",
    ):
        assert service_name in text


def test_authentik_sso_evaluation_links_official_sources() -> None:
    text = EVALUATION.read_text(encoding="utf-8")

    for url in (
        "https://docs.goauthentik.io/install-config/install/docker-compose",
        "https://docs.goauthentik.io/add-secure-apps/providers/proxy/",
        "https://docs.goauthentik.io/add-secure-apps/providers/proxy/forward_auth/",
        "https://docs.goauthentik.io/add-secure-apps/providers/oauth2/",
        "https://www.keycloak.org/server/containers",
        "https://developer.konghq.com/plugins/openid-connect/",
    ):
        assert url in text
