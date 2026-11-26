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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=HERE / "labels.jsonl")
    args = parser.parse_args()

    vad = SileroVad()
    rng = np.random.default_rng(7)
    manifest = [json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip()]
    conversational = [m for m in manifest if m["subset"] in ("ami", "earnings22")]
    clips: dict[str, np.ndarray] = {}
    for item in conversational:
        samples, _ = sf.read(item["path"], dtype="float32")
        clips[item["id"]] = samples.mean(axis=1) if samples.ndim > 1 else samples
    ids = list(clips)

    rows: list[dict] = []
    for clip_id in ids:
        rows.append(
            {
                "clip": clip_id,
                "subset": next(m["subset"] for m in conversational if m["id"] == clip_id),
                "condition": "clean",
                "snr_db": None,
                "label": "speech",
                "gate_speech": vad.is_speech(_chunks(clips[clip_id])),
                "status": STATUS,
            }
        )

    for kind in NOISE_KINDS:
        for clip_id in ids:
            x = clips[clip_id]
            n = len(x)
            if kind == "white":
                noise = rng.standard_normal(n).astype(np.float32)
            elif kind == "pink":
                noise = _pink(rng, n)
            else:
                others = [i for i in ids if i != clip_id]
                pick = rng.choice(others, size=3, replace=False)
                joined = np.concatenate([clips[p] for p in pick])
                noise = np.tile(joined, int(np.ceil(n / len(joined))) + 1)[:n].astype(np.float32)
            mixed = _mix(x, noise, SNR_DB)
            rows.append(
                {
                    "clip": clip_id,
                    "subset": next(m["subset"] for m in conversational if m["id"] == clip_id),
                    "condition": f"{kind}-{SNR_DB}dB",
                    "snr_db": SNR_DB,
                    "label": "speech",
                    "gate_speech": vad.is_speech(_chunks(mixed)),
                    "status": STATUS,
                }
            )

    seconds = 3
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
    silence = np.zeros(seconds * 16000, np.float32)
    rows.append(
        {
            "clip": "digital-silence-3s",
            "subset": "synthetic",
            "condition": "digital silence",
            "snr_db": None,
            "label": "non-speech",
            "gate_speech": vad.is_speech(_chunks(silence)),
            "status": STATUS,
        }
    )

    args.out.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    speech_rows = [r for r in rows if r["label"] == "speech"]
    missed = sum(not r["gate_speech"] for r in speech_rows)
    false_pass = sum(r["gate_speech"] for r in rows if r["label"] == "non-speech")
    print(f"wrote {len(rows)} rows: speech {len(speech_rows)} (missed {missed}), non-speech {len(rows) - len(speech_rows)} (passed {false_pass})")


if __name__ == "__main__":
    main()
