"""Silero voice-activity rule for the speech gate.

A clip is speech when the largest Silero speech probability over its 512-sample
frames reaches SPEECH_THRESHOLD, on the raw samples or after peak normalisation
to NORMALISED_PEAK. This is the combined rule measured on the noisy bench clips
(RESULTS_vad_noisy.md). Changing any constant here changes the approved decision.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np
import onnxruntime as ort

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512
CONTEXT_SAMPLES = 64
SPEECH_THRESHOLD = 0.5
NORMALISED_PEAK = 0.9
MODEL_SHA256 = "f6f4407b41e05e277e7f5cfd9b9ae957b1dfe763c6076a934734c0b448e09b31"
MODEL_PATH = Path(__file__).resolve().parent.parent / "model" / "silero_vad.onnx"

_STATE_SHAPE = (2, 1, 128)


class ModelIntegrityError(RuntimeError):
    """The Silero file on disk is not the pinned model."""


def verify_model(path: Path, expected_sha256: str = MODEL_SHA256) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ModelIntegrityError(
            f"{path} sha256 {digest} does not match the pinned Silero model {expected_sha256}"
        )


class SileroVad:
    """Loads the pinned Silero ONNX model once and applies the speech rule."""

    def __init__(self, model_path: Path = MODEL_PATH) -> None:
        verify_model(model_path)
        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )

    def is_speech(self, blocks: Callable[[], Iterable[np.ndarray]]) -> bool:
        """Apply the rule to one clip.

        ``blocks`` returns the clip as float32 mono 16 kHz chunks. It is called
        again for the normalised pass, so a streaming decoder can be re-run
        without holding the whole clip in memory.
        """
        found, peak = self._scan(blocks())
        if found:
            return True
        if peak <= 0.0:
            return False
        found, _ = self._scan(blocks(), normalise_by=np.float32(peak))
        return found

    def _scan(
        self,
        chunks: Iterable[np.ndarray],
        normalise_by: np.float32 | None = None,
    ) -> tuple[bool, float]:
        """Return (speech_found, peak). Stops at the first frame over the threshold."""
        state = np.zeros(_STATE_SHAPE, np.float32)
        context = np.zeros(CONTEXT_SAMPLES, np.float32)
        sample_rate = np.array(SAMPLE_RATE, np.int64)
        pending = np.empty(0, np.float32)
        peak = 0.0
        for chunk in chunks:
            chunk = np.asarray(chunk, np.float32)
            if normalise_by is None:
                if chunk.size:
                    peak = max(peak, float(np.max(np.abs(chunk))))
            else:
                chunk = chunk / normalise_by * np.float32(NORMALISED_PEAK)
            pending = np.concatenate((pending, chunk)) if pending.size else chunk
            usable = (pending.size // FRAME_SAMPLES) * FRAME_SAMPLES
            for start in range(0, usable, FRAME_SAMPLES):
                frame = pending[start : start + FRAME_SAMPLES]
                output, state = self._session.run(
                    None,
                    {
                        "input": np.concatenate((context, frame))[None, :],
                        "state": state,
                        "sr": sample_rate,
                    },
                )
                if float(output[0, 0]) >= SPEECH_THRESHOLD:
                    return True, peak
                context = frame[-CONTEXT_SAMPLES:]
            pending = pending[usable:]
        return False, peak
