"""Speech gate: pinned Silero rule, streaming decode, and clip-level decisions.

The gate lives in services/speech-gate. These tests run the real pinned ONNX
model on the synthesised speech clip in testdata/ and on silence and noise.
"""

from __future__ import annotations

import io
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_ROOT = REPO_ROOT / "services" / "speech-gate"
TESTDATA = GATE_ROOT / "testdata"
if str(GATE_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE_ROOT))

from speech_gate import audio, vad  # noqa: E402


SPEECH_WAV = TESTDATA / "kokoro-speech-16k.wav"


def _wav_samples(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        assert handle.getframerate() == vad.SAMPLE_RATE
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _chunked(samples: np.ndarray, size: int):
    return lambda: (samples[i : i + size] for i in range(0, len(samples), size))


def _wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def model() -> vad.SileroVad:
    return vad.SileroVad()


@pytest.fixture(scope="module")
def speech() -> np.ndarray:
    return _wav_samples(SPEECH_WAV)


def test_pinned_model_matches_its_sha256() -> None:
    vad.verify_model(vad.MODEL_PATH)
    with pytest.raises(vad.ModelIntegrityError):
        vad.verify_model(vad.MODEL_PATH, expected_sha256="0" * 64)


def test_speech_clip_is_speech(model: vad.SileroVad, speech: np.ndarray) -> None:
    assert model.is_speech(_chunked(speech, 1000)) is True


def test_digital_silence_is_not_speech(model: vad.SileroVad) -> None:
    silence = np.zeros(3 * vad.SAMPLE_RATE, np.float32)
    assert model.is_speech(_chunked(silence, 1000)) is False


def test_quiet_white_noise_is_not_speech(model: vad.SileroVad) -> None:
    # The bench skipped every clip at -50 dBFS noise-only (RESULTS_vad_noisy.md).
    rng = np.random.default_rng(7)
    quiet = (rng.standard_normal(3 * vad.SAMPLE_RATE) * 10 ** (-50 / 20)).astype(np.float32)
    assert model.is_speech(_chunked(quiet, 1000)) is False


def test_speech_in_white_noise_at_0db_is_speech(model: vad.SileroVad, speech: np.ndarray) -> None:
    rng = np.random.default_rng(11)
    rms = float(np.sqrt(np.mean(speech**2)))
    noisy = (speech + rng.standard_normal(len(speech)).astype(np.float32) * rms).astype(np.float32)
    assert model.is_speech(_chunked(noisy, 1000)) is True


def test_empty_and_sub_frame_clips_are_not_speech(model: vad.SileroVad) -> None:
    assert model.is_speech(lambda: []) is False
    assert model.is_speech(_chunked(np.ones(vad.FRAME_SAMPLES - 1, np.float32), 100)) is False


def test_decision_does_not_depend_on_chunk_size(model: vad.SileroVad, speech: np.ndarray) -> None:
    rng = np.random.default_rng(3)
    quiet = (rng.standard_normal(2 * vad.SAMPLE_RATE) * 10 ** (-50 / 20)).astype(np.float32)
    for clip, expected in ((speech, True), (quiet, False)):
        for size in (1, 333, vad.FRAME_SAMPLES, 4096, len(clip)):
            assert model.is_speech(_chunked(clip, size)) is expected, size


class _PeakSession:
    """Stands in for ONNX: scores 0.9 when a frame reaches 0.5 in magnitude, else 0.1."""

    def run(self, _outputs, feeds):
        frame = feeds["input"][0]
        probability = 0.9 if float(np.max(np.abs(frame))) >= 0.5 else 0.1
        return [np.array([[probability]], np.float32), feeds["state"]]


def test_normalised_pass_decides_when_raw_pass_does_not() -> None:
    model = vad.SileroVad.__new__(vad.SileroVad)
    model._session = _PeakSession()
    quiet = np.full(4096, 0.3, np.float32)  # raw peak 0.3 misses; normalised peak 0.9 hits
    assert model.is_speech(_chunked(quiet, 700)) is True
    assert model.is_speech(_chunked(np.zeros(4096, np.float32), 700)) is False


def test_decoder_resamples_to_16k_mono(speech: np.ndarray) -> None:
    seconds = 1.0
    tone = 0.2 * np.sin(2 * np.pi * 440 * np.arange(int(24000 * seconds)) / 24000)
    data = _wav_bytes(tone.astype(np.float32), rate=24000)
    total = sum(len(chunk) for chunk in audio.iter_mono_16k(data))
    assert abs(total - 16000) <= 64
    for chunk in audio.iter_mono_16k(data):
        assert chunk.dtype == np.float32
        assert chunk.ndim == 1


def test_decoded_upload_is_speech(model: vad.SileroVad) -> None:
    data = SPEECH_WAV.read_bytes()
    assert model.is_speech(lambda: audio.iter_mono_16k(data)) is True


def test_undecodable_bytes_raise() -> None:
    with pytest.raises(audio.UndecodableAudio):
        list(audio.iter_mono_16k(b"this is not an audio file"))


LABELS = TESTDATA / "labels.jsonl"


def test_threshold_labels_are_marked_pending_owner_review() -> None:
    rows = [json.loads(line) for line in LABELS.read_text().splitlines() if line.strip()]
    assert len(rows) == 1221
    assert {row["status"] for row in rows} == {"machine-labelled-unverified"}
    assert {row["label"] for row in rows} == {"speech", "non-speech"}
    assert sum(row["label"] == "speech" for row in rows) == 1200
