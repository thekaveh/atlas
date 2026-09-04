"""Parakeet loading is non-blocking, truthful, and process-bounded."""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "services" / "parakeet" / "provider"
STARTUP = PROVIDER / "startup.py"
GPU_TRANSCRIBE = PROVIDER / "gpu" / "transcribe.py"
GPU_API = PROVIDER / "shared" / "api_server.py"
MLX_API = PROVIDER / "mlx" / "api_server.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_startup_loading_returns_immediately_and_becomes_healthy():
    startup_module = _load(STARTUP, "parakeet_startup_success")
    started = threading.Event()
    release = threading.Event()
    model = object()

    def loader():
        started.set()
        release.wait(timeout=5)
        return model

    async def scenario():
        startup = startup_module.ModelStartup(
            "PARAKEET", loader, timeout_seconds=2, terminate=lambda code: None
        )
        task = startup.start()
        await asyncio.sleep(0)
        assert startup.state == "loading"
        assert not task.done()
        deadline = asyncio.get_running_loop().time() + 5.0
        while not started.is_set():
            assert asyncio.get_running_loop().time() < deadline, (
                "loader never signalled started"
            )
            await asyncio.sleep(0.01)
        release.set()
        await task
        return startup

    startup = asyncio.run(scenario())
    assert startup.state == "healthy"
    assert startup.model is model
    assert startup.start_count == 1


def test_startup_deadline_marks_unhealthy_and_terminates_process():
    startup_module = _load(STARTUP, "parakeet_startup_timeout")
    release = threading.Event()
    exits: list[int] = []

    def loader():
        release.wait(timeout=5)
        return object()

    threading.Timer(0.05, release.set).start()

    async def scenario():
        startup = startup_module.ModelStartup(
            "PARAKEET",
            loader,
            timeout_seconds=0.01,
            terminate=lambda code: exits.append(code),
        )
        await startup.start()
        return startup

    startup = asyncio.run(scenario())
    assert startup.state == "unhealthy"
    assert exits == [startup_module.FATAL_TIMEOUT_EXIT_CODE]


def test_startup_loader_failure_is_generic_unhealthy_and_terminates():
    startup_module = _load(STARTUP, "parakeet_startup_failure")
    exits: list[int] = []

    def loader():
        raise RuntimeError("private model cache path")

    async def scenario():
        startup = startup_module.ModelStartup(
            "PARAKEET",
            loader,
            timeout_seconds=1,
            terminate=lambda code: exits.append(code),
        )
        await startup.start()
        return startup

    startup = asyncio.run(scenario())
    assert startup.state == "unhealthy"
    assert startup.model is None
    # A generic load failure must terminate for supervised restart, mirroring the
    # deadline branch — otherwise the container stays alive returning 503 forever.
    assert exits == [startup_module.FATAL_TIMEOUT_EXIT_CODE]


def test_lifespan_forces_exit_when_native_model_load_blocks_shutdown():
    startup_module = _load(STARTUP, "parakeet_startup_shutdown")
    started = threading.Event()
    release = threading.Event()
    exits: list[int] = []

    def loader():
        started.set()
        release.wait(timeout=5)
        return object()

    def terminate(code):
        exits.append(code)
        release.set()

    async def scenario():
        startup = startup_module.ModelStartup(
            "PARAKEET",
            loader,
            timeout_seconds=2,
            shutdown_timeout_seconds=0.01,
            terminate=terminate,
        )
        async with startup_module.model_lifespan(object(), startup):
            deadline = asyncio.get_running_loop().time() + 2
            while not started.is_set():
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
        return startup

    startup = asyncio.run(scenario())
    assert exits == [startup_module.FATAL_TIMEOUT_EXIT_CODE]
    assert startup.state == "unhealthy"


def test_lifespan_cancellation_terminates_and_leaves_no_wrapper_task():
    startup_module = _load(STARTUP, "parakeet_startup_cancelled_shutdown")
    started = threading.Event()
    release = threading.Event()
    exits: list[int] = []

    def loader():
        started.set()
        release.wait(timeout=5)
        return object()

    def terminate(code):
        exits.append(code)
        release.set()

    async def scenario():
        startup = startup_module.ModelStartup(
            "PARAKEET",
            loader,
            timeout_seconds=2,
            shutdown_timeout_seconds=1,
            terminate=terminate,
        )
        lifespan = startup_module.model_lifespan(object(), startup)
        await lifespan.__aenter__()
        deadline = asyncio.get_running_loop().time() + 2
        while not started.is_set():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert startup._task is not None
        assert startup._task.done()
        return startup

    startup = asyncio.run(scenario())
    assert exits == [startup_module.FATAL_TIMEOUT_EXIT_CODE]
    assert startup.state == "unhealthy"


def test_default_shutdown_terminator_exits_process_with_code_70():
    script = f"""
import asyncio
import importlib.util
import threading

spec = importlib.util.spec_from_file_location("parakeet_subprocess_startup", {str(STARTUP)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

async def main():
    startup = module.ModelStartup(
        "PARAKEET",
        lambda: threading.Event().wait(),
        timeout_seconds=30,
        shutdown_timeout_seconds=0.02,
    )
    startup.start()
    await asyncio.sleep(0.05)
    await startup.shutdown()

asyncio.run(main())
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 70, completed.stderr


def test_gpu_module_never_loads_model_during_import(monkeypatch):
    calls = 0

    class ASRModel:
        @staticmethod
        def from_pretrained(name):
            nonlocal calls
            calls += 1
            return object()

    asr = types.ModuleType("nemo.collections.asr")
    asr.models = types.SimpleNamespace(ASRModel=ASRModel)
    collections = types.ModuleType("nemo.collections")
    collections.asr = asr
    nemo = types.ModuleType("nemo")
    nemo.collections = collections
    monkeypatch.setitem(sys.modules, "nemo", nemo)
    monkeypatch.setitem(sys.modules, "nemo.collections", collections)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr", asr)
    monkeypatch.setenv("PRELOAD_MODEL", "true")

    _load(GPU_TRANSCRIBE, "parakeet_gpu_import_contract")

    assert calls == 0


@pytest.mark.parametrize(
    ("device", "expected_transfer"), (("cuda", "cuda"), ("cpu", "cpu"))
)
def test_gpu_loader_preserves_nemo3_model_load_contract(
    monkeypatch, device, expected_transfer
):
    calls: list[tuple[str, str | None]] = []

    class Model:
        def cuda(self):
            calls.append(("transfer", "cuda"))
            return self

        def cpu(self):
            calls.append(("transfer", "cpu"))
            return self

        def eval(self):
            calls.append(("eval", None))
            return self

    class ASRModel:
        @staticmethod
        def from_pretrained(model_name):
            calls.append(("from_pretrained", model_name))
            return Model()

    asr = types.ModuleType("nemo.collections.asr")
    asr.models = types.SimpleNamespace(ASRModel=ASRModel)
    collections = types.ModuleType("nemo.collections")
    collections.asr = asr
    nemo = types.ModuleType("nemo")
    nemo.collections = collections
    monkeypatch.setitem(sys.modules, "nemo", nemo)
    monkeypatch.setitem(sys.modules, "nemo.collections", collections)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr", asr)
    monkeypatch.setenv("PARAKEET_MODEL", "nvidia/parakeet-tdt-0.6b-v3")
    monkeypatch.setenv("PARAKEET_DEVICE", device)

    module = _load(GPU_TRANSCRIBE, f"parakeet_gpu_nemo3_{device}")
    loaded = module.load_model()

    assert isinstance(loaded, Model)
    assert calls == [
        ("from_pretrained", "nvidia/parakeet-tdt-0.6b-v3"),
        ("transfer", expected_transfer),
        ("eval", None),
    ]


def test_both_parakeet_apis_use_lifespan_boundary_and_deadline():
    for path in (GPU_API, MLX_API):
        source = path.read_text(encoding="utf-8")
        assert "lifespan=" in source
        assert "model_lifespan(app, _model_startup)" in source
        assert "load_boundary_settings(" in source
        assert '"/v1/audio/transcriptions"' in source
        assert '"/v1/audio/transcriptions/advanced"' in source
        assert "install_provider_boundary(app," in source
        assert "run_with_deadline(" in source
        assert "fatal_timeout_response(" in source
        assert "CORSMiddleware" not in source


def test_parakeet_native_server_defaults_to_loopback():
    source = MLX_API.read_text(encoding="utf-8")
    assert 'os.getenv("PARAKEET_LOCALHOST_BIND_HOST", "127.0.0.1")' in source
    assert "host=bind_host" in source


def test_parakeet_gpu_image_copies_boundary_and_startup_modules():
    dockerfile = (PROVIDER / "gpu" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY provider_boundary.py /app/" in dockerfile
    assert "COPY startup.py /app/" in dockerfile
