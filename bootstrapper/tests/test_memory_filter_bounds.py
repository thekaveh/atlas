from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "services/open-webui/extras/functions/memory_filter.py"


def _load_module():
    pydantic = ModuleType("pydantic")
    pydantic.BaseModel = type("BaseModel", (), {})
    pydantic.Field = lambda default, **_kwargs: default
    spec = importlib.util.spec_from_file_location("atlas_memory_filter", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    previous = sys.modules.get("pydantic")
    sys.modules["pydantic"] = pydantic
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("pydantic", None)
        else:
            sys.modules["pydantic"] = previous
    return module


def test_executor_rejects_work_beyond_worker_and_queue_capacity():
    module = _load_module()
    executor = module._BoundedDaemonExecutor(max_workers=1, max_queue=1)
    started = threading.Event()
    release = threading.Event()

    assert executor.submit(lambda: (started.set(), release.wait(1)))
    assert started.wait(1)
    assert executor.submit(lambda: None)
    assert not executor.submit(lambda: None)
    release.set()


def test_extraction_checks_http_status_and_redacts_error(monkeypatch, capsys):
    module = _load_module()

    class Response:
        def raise_for_status(self):
            raise RuntimeError("SENTINEL_BACKEND_SECRET")

    monkeypatch.setattr(module.requests, "post", lambda *_args, **_kwargs: Response())
    memory_filter = module.Filter()
    memory_filter._extract(
        "user-id",
        [{"role": "user", "content": "remember this"}] * 4,
    )

    error = capsys.readouterr().err
    assert "error_type=RuntimeError" in error
    assert "SENTINEL_BACKEND_SECRET" not in error
