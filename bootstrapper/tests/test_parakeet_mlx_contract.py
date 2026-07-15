from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "services/parakeet/provider/mlx/alignment.py"


def _load_alignment_module():
    spec = importlib.util.spec_from_file_location("parakeet_alignment", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_aligned_result_uses_sentences_and_nested_tokens():
    alignment = _load_alignment_module()
    result = SimpleNamespace(
        text="hello world",
        sentences=[
            SimpleNamespace(
                text="hello world",
                start=0.25,
                end=1.5,
                duration=1.25,
                tokens=[
                    SimpleNamespace(text="hello", start=0.25, end=0.7, duration=0.45),
                    SimpleNamespace(text="world", start=0.8, end=1.5, duration=0.7),
                ],
            )
        ],
    )

    payload = alignment.alignment_payload(result)

    assert payload["duration"] == 1.5
    assert payload["segments"] == [
        {"id": 0, "text": "hello world", "start": 0.25, "end": 1.5, "duration": 1.25}
    ]
    assert [word["text"] for word in payload["words"]] == ["hello", "world"]


def test_timestamp_flag_requires_timestamp_data():
    alignment = _load_alignment_module()

    assert alignment.advanced_payload(SimpleNamespace(text="plain", sentences=[]), True, True) == {
        "text": "plain",
        "has_timestamps": False,
        "segments": [],
        "words": [],
    }
