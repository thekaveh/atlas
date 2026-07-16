from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "services/parakeet/provider/mlx/model_loader.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("parakeet_model_loader", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_model_initialization_is_single_flight_and_off_event_loop():
    module = _load_module()
    async def run():
        calls = 0
        loader_thread = None

        def load():
            nonlocal calls, loader_thread
            calls += 1
            loader_thread = threading.get_ident()
            time.sleep(0.02)
            return object()

        single_flight = module.AsyncSingleFlightModel(load)
        event_loop_thread = threading.get_ident()
        first, second = await asyncio.gather(single_flight.get(), single_flight.get())

        assert first is second
        assert calls == 1
        assert loader_thread != event_loop_thread
        assert single_flight.loaded

    asyncio.run(run())


def test_start_returns_immediately_while_model_loads():
    module = _load_module()
    async def run():
        release = threading.Event()
        single_flight = module.AsyncSingleFlightModel(
            lambda: (release.wait(1), object())[1]
        )

        task = single_flight.start()
        await asyncio.sleep(0)

        assert not task.done()
        assert not single_flight.loaded
        release.set()
        assert await single_flight.get() is not None

    asyncio.run(run())


def test_failed_load_can_be_retried():
    module = _load_module()

    async def run():
        attempts = 0

        def load():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary failure")
            return object()

        single_flight = module.AsyncSingleFlightModel(load)
        try:
            await single_flight.get()
        except RuntimeError:
            pass
        else:
            raise AssertionError("first load unexpectedly succeeded")

        assert await single_flight.get() is not None
        assert attempts == 2

    asyncio.run(run())
