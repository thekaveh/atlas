"""Exercise the GPU Parakeet API without NeMo or model downloads."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

from httpx2 import ASGITransport, AsyncClient


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "services" / "parakeet" / "provider"
SHARED = PROVIDER / "shared"


def _load_named(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_gpu_api_authenticates_and_runs_both_transcription_contracts(monkeypatch):
    monkeypatch.setenv("PARAKEET_API_TOKEN", "parakeet-test-token")
    monkeypatch.setenv("PARAKEET_AUTH_MODE", "required")
    monkeypatch.setenv("PARAKEET_CONCURRENCY", "1")
    monkeypatch.setenv("PARAKEET_CORS_ORIGINS", "")

    _load_named("bounded_upload", PROVIDER / "bounded_upload.py")
    _load_named("provider_boundary", PROVIDER / "provider_boundary.py")
    _load_named("startup", PROVIDER / "startup.py")

    calls = []
    transcribe = types.ModuleType("transcribe")
    transcribe.load_model = lambda: object()
    transcribe.model_is_loaded = lambda: True

    def transcribe_audio_sync(audio_path, **kwargs):
        calls.append(kwargs)
        assert Path(audio_path).read_bytes() == b"audio bytes"
        result = {"text": "hello atlas", "language": kwargs.get("language") or "auto"}
        if kwargs.get("return_timestamps"):
            result.update({"has_timestamps": True, "timestamps": {"segment": []}})
        return result

    transcribe.transcribe_audio_sync = transcribe_audio_sync
    monkeypatch.setitem(sys.modules, "transcribe", transcribe)

    api = _load_named("parakeet_gpu_api_under_test", SHARED / "api_server.py")
    api._model_startup._state = "healthy"
    api._model_startup._model = object()

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=api.app, raise_app_exceptions=False),
            base_url="http://parakeet.test",
        ) as client:
            health = await client.get("/health")
            rejected = await client.post(
                "/v1/audio/transcriptions",
                files={"file": ("sample.wav", b"audio bytes", "audio/wav")},
            )
            headers = {"Authorization": "Bearer parakeet-test-token"}
            standard = await client.post(
                "/v1/audio/transcriptions",
                headers=headers,
                data={"response_format": "json", "language": "en"},
                files={"file": ("sample.wav", b"audio bytes", "audio/wav")},
            )
            advanced = await client.post(
                "/v1/audio/transcriptions/advanced",
                headers=headers,
                data={"return_timestamps": "true"},
                files={"file": ("sample.wav", b"audio bytes", "audio/wav")},
            )
            return health, rejected, standard, advanced

    health, rejected, standard, advanced = asyncio.run(scenario())

    assert health.status_code == 200
    assert rejected.status_code == 401
    assert standard.status_code == 200
    assert standard.json() == {"text": "hello atlas"}
    assert advanced.status_code == 200
    assert advanced.json()["has_timestamps"] is True
    assert len(calls) == 2
