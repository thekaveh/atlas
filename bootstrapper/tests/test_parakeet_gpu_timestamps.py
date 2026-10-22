"""GPU timing contracts with source-matched NeMo 3 doubles, without inference."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import threading
import types

from httpx2 import ASGITransport, AsyncClient
import pytest


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "services" / "parakeet" / "provider"
TIMING = {
    "segment": [{"segment": "hello atlas", "start": 0.0, "end": 0.8}],
    "word": [{"word": "hello", "start": 0.0, "end": 0.4}],
    "char": [{"char": "h", "start": 0.0, "end": 0.1}],
    "timestep": [0, 1],
}
FLAGS = [(False, False), (True, False), (False, True), (True, True)]


@dataclass
class TranscribeConfig:
    """Relevant pinned defaults; override config replaces individual kwargs."""

    use_lhotse: bool = True
    batch_size: int = 4
    return_hypotheses: bool = False
    num_workers: int | None = None
    channel_selector: object = None
    augmentor: object = None
    timestamps: bool | None = None
    verbose: bool = True
    partial_hypothesis: object = None
    _internal: object = None


def _load(monkeypatch, name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class Model:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.compute_timestamps = False

    def transcribe(self, audio, *, timestamps=None, override_config=None):
        # NeMo 3 RNNT resolves False without an override to None, retaining
        # decoder state. Its mixin then uses the override's hypothesis flag.
        resolved = timestamps or (
            override_config.timestamps if override_config is not None else None
        )
        if resolved is not None:
            self.compute_timestamps = resolved
        self.calls.append((audio, timestamps, override_config, self.compute_timestamps))
        return self.result


class NemoModel(Model):
    def transcribe(self, audio, *, timestamps=None, override_config=None):
        result = super().transcribe(
            audio, timestamps=timestamps, override_config=override_config
        )
        if override_config is not None and not override_config.return_hypotheses:
            return [types.SimpleNamespace(text="hello atlas")]
        return result


@pytest.fixture
def gpu(monkeypatch):
    def forbidden_load(*args, **kwargs):
        raise AssertionError("Model loading is outside this deterministic test")

    asr = types.ModuleType("nemo.collections.asr")
    asr.models = types.SimpleNamespace(
        ASRModel=types.SimpleNamespace(from_pretrained=forbidden_load)
    )
    collections = types.ModuleType("nemo.collections")
    collections.asr = asr
    nemo = types.ModuleType("nemo")
    nemo.collections = collections
    mixins = types.ModuleType("nemo.collections.asr.parts.mixins")
    mixins.TranscribeConfig = TranscribeConfig
    for name, module in {
        "nemo": nemo,
        "nemo.collections": collections,
        "nemo.collections.asr": asr,
        "nemo.collections.asr.parts": types.ModuleType("nemo.collections.asr.parts"),
        "nemo.collections.asr.parts.mixins": mixins,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("PARAKEET_CONCURRENCY", "1")
    return _load(monkeypatch, "transcribe", PROVIDER / "gpu" / "transcribe.py")


def _use_model(gpu, result):
    gpu._model = NemoModel(result)
    return gpu._model


def _expected(segment, word, timing=TIMING):
    result = {"text": "hello atlas", "language": "auto"}
    if segment or word:
        keys = [key for flag, key in ((segment, "segment"), (word, "word")) if flag]
        selected = {key: timing[key] for key in keys if timing.get(key)}
        result["has_timestamps"] = bool(selected)
        if selected:
            result["timestamps"] = selected
    return result


def _assert_nemo_call(call, enabled):
    audio, requested, config, computed = call
    assert audio == ["synthetic.wav"]
    assert requested is enabled
    assert isinstance(config, TranscribeConfig)
    assert config.timestamps is enabled
    assert config.return_hypotheses is enabled
    assert config.num_workers == 0
    assert (config.batch_size, config.use_lhotse, config.verbose) == (4, True, True)
    assert computed is enabled


@pytest.mark.parametrize(("segment", "word"), FLAGS)
def test_independent_flags_select_requested_alignment_and_nemo_config(gpu, segment, word):
    before = deepcopy(TIMING)
    model = _use_model(
        gpu, [types.SimpleNamespace(text="hello atlas", timestamp=TIMING)]
    )
    result = gpu.transcribe_audio_sync(
        "synthetic.wav", return_timestamps=segment, word_timestamps=word
    )

    assert result == _expected(segment, word)
    enabled = bool(segment or word)
    _assert_nemo_call(model.calls[0], enabled)
    assert TIMING == before
    if enabled:
        key = "segment" if segment else "word"
        assert result["timestamps"][key] is TIMING[key]


@pytest.mark.parametrize("flags", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize("result", [
    "hello atlas",
    types.SimpleNamespace(text="hello atlas"),
    types.SimpleNamespace(text="hello atlas", timestamp=None),
    types.SimpleNamespace(text="hello atlas", timestamp={}),
    types.SimpleNamespace(text="hello atlas", timestamp={"segment": [], "word": []}),
    types.SimpleNamespace(text="hello atlas", timestamp={"char": [1], "timestep": [0]}),
    types.SimpleNamespace(text="hello atlas", timestamp="not a mapping"),
    types.SimpleNamespace(text="hello atlas", timestamp=[{"word": []}]),
    types.SimpleNamespace(text="hello atlas", timestamp={"segment": "bad", "word": (1,)}),
])
def test_unavailable_requested_alignment_is_reported_honestly(gpu, flags, result):
    _use_model(gpu, [result])
    assert gpu.transcribe_audio_sync(
        "synthetic.wav", return_timestamps=flags[0], word_timestamps=flags[1]
    ) == {"text": "hello atlas", "language": "auto", "has_timestamps": False}


@pytest.mark.parametrize("flags", [(True, False), (False, True)])
def test_unrequested_alignment_cannot_establish_availability(gpu, flags):
    timing = {"word" if flags[0] else "segment": [1]}
    _use_model(gpu, [types.SimpleNamespace(text="hello atlas", timestamp=timing)])
    assert gpu.transcribe_audio_sync(
        "synthetic.wav", return_timestamps=flags[0], word_timestamps=flags[1]
    ) == {"text": "hello atlas", "language": "auto", "has_timestamps": False}


@pytest.mark.parametrize("key", ["segment", "word"])
def test_both_flags_return_the_available_requested_collection(gpu, key):
    timing = {key: TIMING[key]}
    _use_model(gpu, types.SimpleNamespace(text="hello atlas", timestamp=timing))
    assert gpu.transcribe_audio_sync(
        "synthetic.wav", return_timestamps=True, word_timestamps=True
    ) == _expected(True, True, timing)


def test_extraction_exception_preserves_warning_and_false_capability(gpu, caplog):
    class Hypothesis:
        text = "hello atlas"

        @property
        def timestamp(self):
            raise RuntimeError("private alignment details")

    _use_model(gpu, [Hypothesis()])
    result = gpu.transcribe_audio_sync("synthetic.wav", word_timestamps=True)
    assert result == {"text": "hello atlas", "language": "auto", "has_timestamps": False}
    assert "Could not extract timestamps (error_type=RuntimeError)" in caplog.text
    assert "private alignment details" not in caplog.text


def test_timed_then_untimed_explicitly_disables_mutable_nemo_decoder(gpu):
    model = _use_model(
        gpu, [types.SimpleNamespace(text="hello atlas", timestamp=TIMING)]
    )
    results = [
        gpu.transcribe_audio_sync("synthetic.wav", return_timestamps=enabled)
        for enabled in (True, False, True)
    ]
    assert [call[3] for call in model.calls] == [True, False, True]
    assert results == [_expected(enabled, False) for enabled in (True, False, True)]
    configs = [call[2] for call in model.calls]
    assert len({id(config) for config in configs}) == 3


@pytest.mark.parametrize("result", ["hello atlas", types.SimpleNamespace(text="hello atlas")])
def test_default_preserves_plain_string_and_hypothesis_text(gpu, result):
    gpu._model = Model(result)
    assert gpu.transcribe_audio_sync("synthetic.wav", language="en") == {
        "text": "hello atlas", "language": "en"
    }


def test_other_result_keeps_existing_string_fallback(gpu):
    gpu._model = Model(123)
    assert gpu.transcribe_audio_sync("synthetic.wav") == {"text": "123", "language": "auto"}


def test_async_wrapper_forwards_all_arguments_and_offloads(gpu, monkeypatch):
    _use_model(gpu, [types.SimpleNamespace(text="hello atlas", timestamp=TIMING)])
    original = gpu.transcribe_audio_sync
    calls = []
    main_thread = threading.get_ident()

    def recording(*args):
        calls.append((args, threading.get_ident()))
        return original(*args)

    monkeypatch.setattr(gpu, "transcribe_audio_sync", recording)
    result = asyncio.run(gpu.transcribe_audio("synthetic.wav", "en", 0.25, False, True))
    assert calls[0][0] == ("synthetic.wav", "en", 0.25, False, True)
    assert calls[0][1] != main_thread
    assert result == {**_expected(False, True), "language": "en"}


@pytest.fixture
def api(monkeypatch, gpu):
    monkeypatch.setenv("PARAKEET_API_TOKEN", "parakeet-test-token")
    monkeypatch.setenv("PARAKEET_AUTH_MODE", "required")
    monkeypatch.setenv("PARAKEET_CORS_ORIGINS", "")
    for name in ("bounded_upload", "provider_boundary", "startup"):
        _load(monkeypatch, name, PROVIDER / f"{name}.py")
    module = _load(
        monkeypatch, "parakeet_gpu_timing_api", PROVIDER / "shared" / "api_server.py"
    )
    module._model_startup._state = "healthy"
    module._model_startup._model = object()
    return module


async def _post(api, endpoint, data):
    async with AsyncClient(
        transport=ASGITransport(app=api.app, raise_app_exceptions=False),
        base_url="http://parakeet.test",
    ) as client:
        return await client.post(
            endpoint,
            headers={"Authorization": "Bearer parakeet-test-token"},
            data=data,
            files={"file": ("sample.wav", b"audio bytes", "audio/wav")},
        )


@pytest.mark.parametrize(("segment", "word"), FLAGS)
def test_actual_advanced_api_projects_gpu_alignment(api, gpu, segment, word):
    model = _use_model(
        gpu, [types.SimpleNamespace(text="hello atlas", timestamp=TIMING)]
    )
    response = asyncio.run(_post(
        api,
        "/v1/audio/transcriptions/advanced",
        {"return_timestamps": str(segment).lower(), "word_timestamps": str(word).lower()},
    ))
    assert response.status_code == 200
    assert response.json() == _expected(segment, word)
    assert model.calls[0][1] is bool(segment or word)
    assert not Path(model.calls[0][0][0]).exists()


def test_actual_word_only_api_reports_absent_alignment(api, gpu):
    _use_model(gpu, [types.SimpleNamespace(text="hello atlas")])
    response = asyncio.run(_post(
        api, "/v1/audio/transcriptions/advanced", {"word_timestamps": "true"}
    ))
    assert response.status_code == 200
    assert response.json() == {"text": "hello atlas", "language": "auto", "has_timestamps": False}


@pytest.mark.parametrize("response_format", ["json", "verbose_json", "text"])
def test_actual_standard_api_preserves_default_formats(api, gpu, response_format):
    model = _use_model(
        gpu, [types.SimpleNamespace(text="hello atlas", timestamp=TIMING)]
    )
    response = asyncio.run(_post(
        api, "/v1/audio/transcriptions", {"response_format": response_format}
    ))
    assert response.status_code == 200
    if response_format == "text":
        assert response.text == "hello atlas"
    else:
        expected = (
            {"text": "hello atlas"} if response_format == "json" else _expected(False, False)
        )
        assert response.json() == expected
    assert model.calls[0][1] is False
    assert model.compute_timestamps is False
