from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "services/parakeet/provider/mlx/alignment.py"
MLX_API = ROOT / "services/parakeet/provider/mlx/api_server.py"
SHARED_API = ROOT / "services/parakeet/provider/shared/api_server.py"


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


def test_advanced_timestamp_defaults_are_provider_consistent():
    for path in (MLX_API, SHARED_API):
        source = path.read_text(encoding="utf-8")
        assert "return_timestamps: bool = Form(default=False)" in source


def test_response_format_contract_is_provider_consistent():
    expected = 'Literal["json", "verbose_json", "text"]'
    for path in (MLX_API, SHARED_API):
        source = path.read_text(encoding="utf-8")
        assert expected in source
    stt_guide = (ROOT / "services/stt-provider/README.md").read_text()
    assert "optional: json, text, verbose_json" in stt_guide


def test_cold_health_starts_loading_without_waiting_for_it():
    source = MLX_API.read_text(encoding="utf-8")

    assert "task = _model_loader.start()" in source
    assert '"status": "starting"' in source
    assert "status_code=503" in source


def test_mlx_server_supports_package_and_direct_import_modes():
    source = MLX_API.read_text(encoding="utf-8")

    assert "from .alignment import" in source
    assert "from alignment import" in source
    assert "from .model_loader import" in source
    assert "from model_loader import" in source
