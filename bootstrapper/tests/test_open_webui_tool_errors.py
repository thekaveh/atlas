from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "services/open-webui/extras/tools"


def test_registered_tools_do_not_return_raw_error_details():
    forbidden = (
        "str(e)",
        "str(exc)",
        "str(parse_error)",
        "error_data.get('detail'",
        'error_data.get("detail"',
        "Raw response:",
        "result.get('error'",
        'result.get("error"',
        "health_data['error']",
        'health_data["error"]',
        "Backend URL:",
        "base64.b64encode",
    )
    findings = []
    for path in sorted(TOOLS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                findings.append(f"{path.name}: {token}")

    assert not findings, findings


def test_blocking_tools_run_off_the_open_webui_event_loop(monkeypatch):
    # Open WebUI 0.6.32 runs a sync tool on its only event loop; a 300 s
    # ComfyUI wait (or a 120 s memory call) froze the UI for every user.
    import asyncio
    import importlib.util
    import inspect

    import requests

    import threading

    threads = []

    def refuse(*_args, **_kwargs):
        threads.append(threading.current_thread())
        raise requests.ConnectionError("backend down")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)
    for name in ("comfyui_image_generation_tool", "memory_tool", "research_tool"):
        spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        public = [n for n, f in inspect.getmembers(module.Tools, inspect.isfunction) if not n.startswith("_")]
        assert public and all(inspect.iscoroutinefunction(getattr(module.Tools, n)) for n in public), name
        assert all(getattr(module.Tools, n).__doc__ for n in public), name
        if name == "comfyui_image_generation_tool":
            result = asyncio.run(module.Tools().check_comfyui_status())
            assert isinstance(result, str) and result  # awaited, not a coroutine
            assert threads and threads[-1] is not threading.main_thread()  # off the loop
