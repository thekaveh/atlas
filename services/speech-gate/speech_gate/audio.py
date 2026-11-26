"""Stream an uploaded audio file as 16 kHz mono float32 chunks for the VAD."""

from __future__ import annotations

import io
from collections.abc import Iterator

import av
import numpy as np

from speech_gate.vad import SAMPLE_RATE


class UndecodableAudio(ValueError):
    """The upload cannot be read as audio, so the gate cannot judge it."""


def iter_mono_16k(data: bytes) -> Iterator[np.ndarray]:
    """Yield the first audio stream as float32 mono 16 kHz chunks.

    Decoding streams, so memory stays bounded by the resampler buffer rather
    than the clip length. The upload is re-decoded for the normalised VAD pass.
    """
    try:
        container = av.open(io.BytesIO(data))
    except av.FFmpegError as exc:
        raise UndecodableAudio("the upload is not a decodable audio file") from exc
    try:
        streams = container.streams.audio
        if not streams:
            raise UndecodableAudio("the upload has no audio stream")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        try:
            for frame in container.decode(streams[0]):
                for resampled in resampler.resample(frame):
                    yield _as_samples(resampled)
            for resampled in resampler.resample(None):
                yield _as_samples(resampled)
        except av.FFmpegError as exc:
            raise UndecodableAudio("the upload audio could not be decoded") from exc
    finally:
        container.close()


def _as_samples(frame: av.AudioFrame) -> np.ndarray:
    return frame.to_ndarray().reshape(-1).astype(np.float32, copy=False)
