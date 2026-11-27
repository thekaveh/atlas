# 5.2.63. Speech gate threshold test set

> **Status: labels machine-generated, not verified against audio.** Every row in
> `labels.jsonl` carries `"status": "machine-labelled-unverified"`. The owner has
> declined manual review. Verification against reference transcripts (AMI,
> Earnings-22) needs the bench audio, which is not in this repository, so it has
> not been run. Until it is, the 0.5 threshold is not validated on real audio.
> The bench shows only the synthetic-noise behaviour.

## 1. Contents

- `labels.jsonl`: 1,221 rows, one per clip and condition. Each row has `clip`,
  `subset`, `condition`, `snr_db`, `label` (`speech` or `non-speech`),
  `gate_speech` (the gate's decision), and `status`.
- `build_labels.py`: rebuilds `labels.jsonl` from the noisy-VAD bench clips.
  It needs the bench manifest and audio, which are not in this repository.
- `kokoro-speech-16k.wav`: 3.8 s of synthetic speech, 16 kHz mono. The unit
  tests and the live check use it. Kokoro-82M produced it through Speaches TTS.

## 2. Sources and licences

The speech rows come from the AMI (`ami-*`) and Earnings-22 (`earnings22-*`)
conversational clips in the bench manifest. The audio is not redistributed
here. Check each dataset's licence card before sharing any clip.

## 3. Labels

- **speech:** each clip clean, and mixed with white, pink or babble noise at
  0 dB SNR. Babble is other speakers from the same set.
- **non-speech:** white noise at -50 and -70 dBFS (10 clips each), and 3 s of
  digital silence.

The labels follow from how the rows were built. Nobody listened to them. Some
conversational clips may contain little or no speech, so the owner review
covers every label.

## 4. Recorded decisions (labels run, machine-generated, not verified)

| Label | Condition | Rows | Gate speech |
|---|---|---:|---:|
| speech | clean | 300 | 300 |
| speech | white, 0 dB SNR | 300 | 294 |
| speech | pink, 0 dB SNR | 300 | 287 |
| speech | babble, 0 dB SNR | 300 | 300 |
| non-speech | white, -50 dBFS | 10 | 0 |
| non-speech | white, -70 dBFS | 10 | 0 |
| non-speech | digital silence | 1 | 0 |

The 0 dB white and pink misses differ by a few clips from
`RESULTS_vad_noisy.md`, which used a different noise draw order. The bench
notes that the counts vary between runs.
