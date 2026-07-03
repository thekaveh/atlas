from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EVALUATION = REPO_ROOT / "docs" / "strategy" / "infisical-secrets-manager-evaluation.md"


def test_infisical_secrets_evaluation_records_required_decisions() -> None:
    text = EVALUATION.read_text(encoding="utf-8")

    required_phrases = (
        "Infisical-first",
        "OpenBao watchlist",
        "disabled by default",
        "existing `.env` flows remain authoritative",
        "Only new high-risk credentials",
        "`INFISICAL_SOURCE=container|disabled`",
        "category: `infra`",
        "track: `identity-security`",
        "Postgres",
        "Redis",
        "machine identity",
        "Universal Auth",
        "must not require Infisical to fetch the secrets needed to start Infisical",
        "Service Admission Contract",
        "Acceptance Criteria For The Future Implementation Ticket",
    )

    for phrase in required_phrases:
        assert phrase in text


def test_infisical_secrets_evaluation_links_official_sources() -> None:
    text = EVALUATION.read_text(encoding="utf-8")

    for url in (
        "https://infisical.com/docs/self-hosting/deployment-options/docker-compose",
        "https://infisical.com/docs/documentation/platform/identities/machine-identities",
        "https://infisical.com/docs/self-hosting/configuration/requirements",
        "https://openbao.org/docs/concepts/integrated-storage/",
    ):
        assert url in text
