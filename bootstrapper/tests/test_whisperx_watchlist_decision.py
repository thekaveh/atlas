from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "whisperx.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFEST = ROOT / "services" / "whisperx" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_whisperx_remains_watchlist_until_audio_workflow_exists() -> None:
    text = _candidate_text()

    assert "Watchlist decision (2026-07-04)" in text
    assert "must not add `services/whisperx/service.yml` yet" in text
    assert "named meeting/audio ingestion workflow" in text
    assert "not needed for generic transcription" in text
    assert "Diarization gate" in text
    assert "Provenance gate" in text


def test_whisperx_future_service_spec_covers_atlas_service_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`rag`",
        "`voice`",
        "`all`",
        "`media`",
        "`WHISPERX_SOURCE=disabled|container-gpu|localhost`",
        "disabled by default",
        "`STT_PROVIDER_SOURCE`",
        "Wizard placement",
        "`whisperx.localhost`",
        "`whisperx -> minio`",
        "`whisperx -> supabase`",
        "`whisperx -> weaviate`",
        "Hugging Face/pyannote token",
        "GPU source is preferred",
        "custom `BASE_PORT`",
    ]

    for term in expected_terms:
        assert term in text


def test_whisperx_service_manifest_is_not_added_by_watchlist_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_whisperx_candidate_remains_indexed_from_stt_research_row() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    assert "| WhisperX | media | stt-provider | [candidates/whisperx.md]" in matrix
