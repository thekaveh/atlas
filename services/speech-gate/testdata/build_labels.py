"""Build labels.jsonl for the speech-gate threshold test set from the noisy-VAD bench clips.

The audio is not committed (see README.md). Run this where the bench clips exist:

    python services/speech-gate/testdata/build_labels.py \
        --manifest /path/to/bench/manifest_v2.jsonl

Requires soundfile and numpy. The gate's own SileroVad makes the decisions, with
the same seed, noise draw order and mixing as bench/noisy_vad.py, so the
recorded gate_speech values match RESULTS_vad_noisy.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from speech_gate.vad import SileroVad  # noqa: E402

SNR_DB = 0
NOISE_KINDS = ("white", "pink", "babble")
NOISE_ONLY_LEVELS_DBFS = (-50, -70)
NOISE_ONLY_PER_LEVEL = 10
STATUS = "pending-owner-review"
CHUNK = 1000


def _pink(rng: np.random.Generator, n: int) -> np.ndarray:
    white = rng.standard_normal(n)
    spectrum = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n)
    freqs[0] = freqs[1]
    shaped = np.fft.irfft(spectrum / np.sqrt(freqs), n)
    return (shaped / np.max(np.abs(shaped))).astype(np.float32)


def _mix(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    p_speech = np.mean(speech**2)
    p_noise = np.mean(noise**2) + 1e-12
    return (speech + np.sqrt(p_speech / (p_noise * 10 ** (snr_db / 10))) * noise).astype(np.float32)


def _chunks(samples: np.ndarray):
    return lambda: (samples[i : i + CHUNK] for i in range(0, len(samples), CHUNK))


def _load_clips(conversational: list[dict]) -> dict[str, tuple[str, np.ndarray]]:
    """Map clip id to ``(subset, mono samples)``, keeping manifest order."""
    clips: dict[str, tuple[str, np.ndarray]] = {}
    for item in conversational:
        samples, _ = sf.read(item["path"], dtype="float32")
        mono = samples.mean(axis=1) if samples.ndim > 1 else samples
        clips[item["id"]] = (item["subset"], mono)
    return clips


def _noise_for(kind: str, rng, n: int, pool: list[np.ndarray]) -> np.ndarray:
    if kind == "white":
        return rng.standard_normal(n).astype(np.float32)
    if kind == "pink":
        return _pink(rng, n)
    pick = rng.choice(len(pool), size=3, replace=False)
    joined = np.concatenate([pool[p] for p in pick])
    return np.tile(joined, int(np.ceil(n / len(joined))) + 1)[:n].astype(np.float32)


def _clean_rows(vad, clips: dict[str, tuple[str, np.ndarray]]) -> list[dict]:
    return [
        {
            "clip": clip_id,
            "subset": subset,
            "condition": "clean",
            "snr_db": None,
            "label": "speech",
            "gate_speech": vad.is_speech(_chunks(samples)),
            "status": STATUS,
        }
        for clip_id, (subset, samples) in clips.items()
    ]


def _mixed_rows(vad, clips: dict[str, tuple[str, np.ndarray]], rng) -> list[dict]:
    rows = []
    for kind in NOISE_KINDS:
        for clip_id, (subset, samples) in clips.items():
            pool = [other for other_id, (_, other) in clips.items() if other_id != clip_id]
            noise = _noise_for(kind, rng, len(samples), pool)
            mixed = _mix(samples, noise, SNR_DB)
            rows.append(
                {
                    "clip": clip_id,
                    "subset": subset,
                    "condition": f"{kind}-{SNR_DB}dB",
                    "snr_db": SNR_DB,
                    "label": "speech",
                    "gate_speech": vad.is_speech(_chunks(mixed)),
                    "status": STATUS,
                }
            )
    return rows


def _noise_only_rows(vad, rng, seconds: int) -> list[dict]:
    rows = []
    for level in NOISE_ONLY_LEVELS_DBFS:
        for index in range(NOISE_ONLY_PER_LEVEL):
            noise = (rng.standard_normal(seconds * 16000) * 10 ** (level / 20)).astype(np.float32)
            rows.append(
                {
                    "clip": f"white-{-level}dbfs-{index:02d}",
                    "subset": "synthetic",
                    "condition": f"white {level} dBFS noise only",
                    "snr_db": None,
                    "label": "non-speech",
                    "gate_speech": vad.is_speech(_chunks(noise)),
                    "status": STATUS,
                }
            )
    return rows


def _silence_row(vad, seconds: int) -> dict:
    silence = np.zeros(seconds * 16000, np.float32)
    return {
        "clip": "digital-silence-3s",
        "subset": "synthetic",
        "condition": "digital silence",
        "snr_db": None,
        "label": "non-speech",
        "gate_speech": vad.is_speech(_chunks(silence)),
        "status": STATUS,
    }


def _summary(rows: list[dict]) -> str:
    speech_rows = [r for r in rows if r["label"] == "speech"]
    missed = sum(not r["gate_speech"] for r in speech_rows)
    false_pass = sum(r["gate_speech"] for r in rows if r["label"] == "non-speech")
    return (
        f"wrote {len(rows)} rows: speech {len(speech_rows)} (missed {missed}), "
        f"non-speech {len(rows) - len(speech_rows)} (passed {false_pass})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=HERE / "labels.jsonl")
    args = parser.parse_args()

    vad = SileroVad()
    rng = np.random.default_rng(7)
    manifest = [json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip()]
    conversational = [m for m in manifest if m["subset"] in ("ami", "earnings22")]
    clips = _load_clips(conversational)

    # Order matters: the shared RNG is drawn in this sequence, so the labels stay reproducible.
    rows = _clean_rows(vad, clips)
    rows += _mixed_rows(vad, clips, rng)
    rows += _noise_only_rows(vad, rng, seconds=3)
    rows.append(_silence_row(vad, seconds=3))

    args.out.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    print(_summary(rows))


if __name__ == "__main__":
    main()
