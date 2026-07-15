from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = {
    "voicebox": ROOT / "docs" / "research" / "candidates" / "voicebox.md",
    "omnivoice": ROOT / "docs" / "research" / "candidates" / "omnivoice.md",
    "unmute": ROOT / "docs" / "research" / "candidates" / "unmute.md",
}
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
SERVICE_MANIFESTS = [
    ROOT / "services" / "voicebox" / "service.yml",
    ROOT / "services" / "omnivoice" / "service.yml",
    ROOT / "services" / "unmute" / "service.yml",
]


def _text(name: str) -> str:
    return CANDIDATES[name].read_text(encoding="utf-8")


def test_voice_stack_candidates_have_july_deferred_decisions() -> None:
    for name, expected in {
        "voicebox": [
            "Deferred decision (2026-07-04)",
            "OpenAI-compatible endpoint",
            "desktop app",
            "MCP server",
        ],
        "omnivoice": [
            "Deferred decision (2026-07-04)",
            "immature HTTP wrapper",
            "TTS_PROVIDER_SOURCE",
            "Speaches",
        ],
        "unmute": [
            "Deferred decision (2026-07-04)",
            "realtime speech workflow",
            "OpenAI Realtime",
            "WebSocket",
        ],
    }.items():
        text = _text(name)
        for phrase in expected:
            assert phrase in text


def test_voice_stack_future_contract_is_conservative() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in CANDIDATES.values())

    expected_terms = [
        "`gen-ai-creative`",
        "`gen-ai-eng`",
        "`all`",
        "`media`",
        "`VOICEBOX_SOURCE=disabled|localhost`",
        "`TTS_PROVIDER_SOURCE`",
        "`UNMUTE_SOURCE=disabled|container-gpu|localhost`",
        "disabled by default",
        "Wizard placement",
        "no default Kong route",
        "custom `BASE_PORT`",
        "STT Provider",
        "TTS Provider",
        "Open WebUI",
        "Hermes",
        "LiteLLM",
        "backend",
        "n8n",
        "data_flow.calls",
        "init companion",
        "voice consent",
        "voice-cloning",
    ]

    for term in expected_terms:
        assert term in combined


def test_voice_stack_service_manifests_are_not_added() -> None:
    for manifest in SERVICE_MANIFESTS:
        assert not manifest.exists()


def test_voice_stack_decision_is_indexed_and_strategy_reflected() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")

    assert "| Voicebox (jamiepine) | media | _(none)_ | [candidates/voicebox.md]" in matrix
    assert "| OmniVoice (k2-fsa) | media | _(none)_ | [candidates/omnivoice.md]" in matrix
    assert "| Unmute (Kyutai) | media | tts-provider | [candidates/unmute.md]" in matrix
    assert "July 4, 2026 decision keeps Voicebox, OmniVoice, and Unmute deferred" in strategy
