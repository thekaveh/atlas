"""Both Parakeet providers serve one OpenAI-compatible contract from the shared factory.

The GPU and MLX servers are loaded with stubbed model backends (no NeMo, no
parakeet-mlx, no weights) so the same HTTP scenarios run against each.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest
from httpx2 import ASGITransport, AsyncClient


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "services" / "parakeet" / "provider"
TOKEN = "parakeet-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
AUDIO = {"file": ("sample.wav", b"audio bytes", "audio/wav")}
ROUTES = {
    "/v1/audio/transcriptions",
    "/v1/audio/transcriptions/advanced",
}


def _load_named(monkeypatch, name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # monkeypatch restores sys.modules, so each test gets fresh provider modules.
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@dataclass
class Provider:
    name: str
    api: types.ModuleType
    mark_ready: Callable[[], None]
    fail_with: Callable[[Exception], None]


def _gpu_provider(monkeypatch) -> Provider:
    state = SimpleNamespace(loaded=False, error=None)
    transcribe = types.ModuleType("transcribe")
    transcribe.load_model = lambda: object()
    transcribe.model_is_loaded = lambda: state.loaded

    def transcribe_audio_sync(audio_path, language=None, temperature=0.0, **flags):
        assert Path(audio_path).read_bytes() == b"audio bytes"
        if state.error is not None:
            raise state.error
        result = {"text": "hello atlas", "language": language or "auto"}
        if flags.get("return_timestamps") or flags.get("word_timestamps"):
            result.update({"has_timestamps": True, "timestamps": {"segment": []}})
        return result

    transcribe.transcribe_audio_sync = transcribe_audio_sync
    monkeypatch.setitem(sys.modules, "transcribe", transcribe)
    api = _load_named(
        monkeypatch, "parakeet_gpu_factory_under_test", PROVIDER / "shared" / "api_server.py"
    )

    def mark_ready() -> None:
        state.loaded = True
        api._model_startup._state = "healthy"

    return Provider("gpu", api, mark_ready, lambda exc: setattr(state, "error", exc))


def _mlx_provider(monkeypatch) -> Provider:
    state = SimpleNamespace(error=None)

    class FakeModel:
        def transcribe(self, path):
            assert Path(path).read_bytes() == b"audio bytes"
            if state.error is not None:
                raise state.error
            token = SimpleNamespace(text="hello", start=0.0, end=0.5, duration=0.5)
            sentence = SimpleNamespace(
                text="hello atlas", start=0.0, end=1.0, duration=1.0, tokens=[token]
            )
            return SimpleNamespace(text="hello atlas", sentences=[sentence])

    model = FakeModel()
    parakeet_mlx = types.ModuleType("parakeet_mlx")
    parakeet_mlx.from_pretrained = lambda name: model
    monkeypatch.setitem(sys.modules, "parakeet_mlx", parakeet_mlx)
    # The MLX server prepends the provider directory to sys.path at import time.
    monkeypatch.setattr(sys, "path", list(sys.path))
    _load_named(monkeypatch, "alignment", PROVIDER / "mlx" / "alignment.py")
    api = _load_named(
        monkeypatch, "parakeet_mlx_factory_under_test", PROVIDER / "mlx" / "api_server.py"
    )

    def mark_ready() -> None:
        api._model_startup._model = model
        api._model_startup._state = "healthy"

    return Provider("mlx", api, mark_ready, lambda exc: setattr(state, "error", exc))


@pytest.fixture(params=["gpu", "mlx"])
def provider(request, monkeypatch) -> Provider:
    monkeypatch.setenv("PARAKEET_API_TOKEN", TOKEN)
    monkeypatch.setenv("PARAKEET_AUTH_MODE", "required")
    monkeypatch.setenv("PARAKEET_CONCURRENCY", "1")
    monkeypatch.setenv("PARAKEET_CORS_ORIGINS", "")
    monkeypatch.delenv("PARAKEET_BACKEND", raising=False)
    monkeypatch.delenv("PARAKEET_DEVICE", raising=False)
    monkeypatch.delenv("PARAKEET_MODEL", raising=False)
    for name in ("bounded_upload", "provider_boundary", "startup", "transcription_api"):
        _load_named(monkeypatch, name, PROVIDER / f"{name}.py")
    if request.param == "gpu":
        return _gpu_provider(monkeypatch)
    return _mlx_provider(monkeypatch)


def _call(provider: Provider, method: str, path: str, **kwargs):
    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=provider.api.app, raise_app_exceptions=False),
            base_url="http://parakeet.test",
        ) as client:
            return await getattr(client, method)(path, **kwargs)

    return asyncio.run(scenario())


def _transcribe(provider: Provider, data=None, route="/v1/audio/transcriptions", files=None):
    return _call(
        provider,
        "post",
        route,
        headers=AUTH,
        data=data or {},
        files=files if files is not None else AUDIO,
    )


def test_health_reports_loading_then_healthy(provider):
    loading = _call(provider, "get", "/health")
    assert loading.status_code == 503
    assert loading.json()["status"] == "loading"
    assert loading.json()["model_loaded"] is False

    provider.mark_ready()
    healthy = _call(provider, "get", "/health")
    assert healthy.status_code == 200
    assert healthy.json()["status"] == "healthy"
    assert healthy.json()["model_loaded"] is True


def test_root_and_health_report_each_providers_own_runtime_identity(provider, monkeypatch):
    monkeypatch.setenv("PARAKEET_DEVICE", "cpu")
    provider.mark_ready()
    # Only /health is exempt from the bearer token; the root document is not.
    root = _call(provider, "get", "/", headers=AUTH).json()
    health = _call(provider, "get", "/health").json()

    if provider.name == "mlx":
        # MLX is fixed to mlx/mps and ignores the GPU container's env vars.
        expected = {
            "backend": "mlx",
            "device": "mps",
            "model": "mlx-community/parakeet-tdt-0.6b-v3",
        }
        assert root["name"] == "Parakeet MLX STT API"
    else:
        expected = {
            "backend": "cuda",
            "device": "cpu",
            "model": "nvidia/parakeet-tdt-0.6b-v3",
        }
        assert root["name"] == "Parakeet STT API"
    for body in (root, health):
        assert {k: body[k] for k in expected} == expected
    assert root["version"] == "1.0.0"


def test_transcription_requires_the_bearer_token(provider):
    provider.mark_ready()
    response = _call(
        provider,
        "post",
        "/v1/audio/transcriptions",
        files=AUDIO,
    )
    assert response.status_code == 401


def test_transcription_is_refused_while_the_model_loads(provider):
    response = _transcribe(provider)
    assert response.status_code == 503
    assert response.json() == {"detail": "Service is loading"}


def test_standard_formats_share_one_contract(provider):
    provider.mark_ready()

    json_body = _transcribe(provider, data={"language": "en"}).json()
    assert json_body == {"text": "hello atlas"}

    text = _transcribe(provider, data={"response_format": "text"})
    assert text.status_code == 200
    assert text.headers["content-type"].startswith("text/plain")
    assert text.text == "hello atlas"

    verbose = _transcribe(provider, data={"response_format": "verbose_json", "language": "en"})
    assert verbose.status_code == 200
    assert verbose.json()["text"] == "hello atlas"
    assert verbose.json()["language"] == "en"


def test_advanced_route_returns_timestamps_on_request(provider):
    provider.mark_ready()
    response = _transcribe(
        provider,
        data={"return_timestamps": "true", "word_timestamps": "true"},
        route="/v1/audio/transcriptions/advanced",
    )
    assert response.status_code == 200
    assert response.json()["has_timestamps"] is True


def test_empty_upload_is_a_client_error(provider):
    provider.mark_ready()
    response = _transcribe(provider, files={"file": ("empty.wav", b"", "audio/wav")})
    assert response.status_code == 400


@pytest.mark.parametrize(
    "route",
    ["/v1/audio/transcriptions", "/v1/audio/transcriptions/advanced"],
)
def test_internal_failures_return_generic_500_and_log_only_the_type(
    provider, route, caplog
):
    provider.mark_ready()
    provider.fail_with(RuntimeError("secret /private/model-cache path"))
    response = _transcribe(provider, route=route)

    assert response.status_code == 500
    assert response.json() == {"detail": "Transcription failed"}
    assert "secret" not in response.text
    expected_log = (
        "Advanced transcription failed (error_type=RuntimeError)"
        if route.endswith("/advanced")
        else "Transcription failed (error_type=RuntimeError)"
    )
    assert expected_log in caplog.text
    assert "secret" not in caplog.text


def test_both_providers_expose_the_same_route_table(provider):
    paths = {route.path for route in provider.api.app.routes}
    assert ROUTES <= paths
    assert {"/", "/health"} <= paths
