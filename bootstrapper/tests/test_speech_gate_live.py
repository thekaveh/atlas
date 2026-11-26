"""Opt-in live check of a running speech gate in front of Speaches.

Skipped unless ATLAS_SPEECH_GATE_LIVE_ENDPOINT names the gate, for example
http://127.0.0.1:<SPEECH_GATE_PORT>. Set ATLAS_SPEACHES_LIVE_API_KEY to the
Speaches bearer key (SPEACHES_API_KEY), because the gate forwards it.
"""

from __future__ import annotations

import io
import os
import wave
from pathlib import Path

import pytest
import requests

TESTDATA = Path(__file__).resolve().parents[2] / "services" / "speech-gate" / "testdata"
GATE = os.environ.get("ATLAS_SPEECH_GATE_LIVE_ENDPOINT", "").rstrip("/")
KEY = os.environ.get("ATLAS_SPEACHES_LIVE_API_KEY", "")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not GATE, reason="set ATLAS_SPEECH_GATE_LIVE_ENDPOINT to run the live gate check"),
]


def _silence_wav(seconds: int = 3) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 16000 * seconds)
    return buffer.getvalue()


def _post(clip: bytes, **fields: str) -> requests.Response:
    headers = {"Authorization": f"Bearer {KEY}"} if KEY else {}
    return requests.post(
        f"{GATE}/v1/audio/transcriptions",
        headers=headers,
        files={"file": ("clip.wav", clip, "audio/wav")},
        data={"model": "whisper-1", **fields},
        timeout=300,
    )


def test_live_gate_returns_empty_transcript_for_silence() -> None:
    response = _post(_silence_wav())
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "", "logprobs": None, "usage": None}


def test_live_gate_forwards_speech_to_speaches() -> None:
    response = _post((TESTDATA / "kokoro-speech-16k.wav").read_bytes())
    assert response.status_code == 200, response.text
    assert "quick brown fox" in response.json()["text"].lower()
