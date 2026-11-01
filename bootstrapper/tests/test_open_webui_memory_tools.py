from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MEMORY_TOOL = ROOT / "services" / "open-webui" / "extras" / "tools" / "memory_tool.py"


def test_open_webui_memory_delete_forwards_current_user_id() -> None:
    text = MEMORY_TOOL.read_text(encoding="utf-8")

    forget_start = text.index("    def forget(")
    list_start = text.index("    def list_memories(", forget_start)
    forget_body = text[forget_start:list_start]

    assert 'user_id = __user__.get("id", "")' in forget_body
    assert "User ID not available" in forget_body
    assert 'params={"user_id": user_id}' in forget_body


def test_open_webui_memory_views_show_the_full_id_forget_needs() -> None:
    # The backend DELETE accepts only a whole UUID; a truncated id (or none)
    # left the model nothing it could pass to forget.
    text = MEMORY_TOOL.read_text(encoding="utf-8")
    recall_body = text[text.index("    def recall("):text.index("    def forget(")]
    list_body = text[text.index("    def list_memories("):]

    for body in (recall_body, list_body):
        assert "(id: {mem.get('id') or 'N/A'}, " in body
    assert "[:8]" not in text


def test_open_webui_memory_tool_clips_input_to_backend_request_limits(monkeypatch) -> None:
    # Backend MemoryMessage.content allows 20,000 characters and
    # MemoryRecallRequest.query 4,000; longer model input got a 422 that the
    # tool reported as "try again later", failing the same way on every retry.
    import asyncio
    import importlib.util

    import requests

    posted = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {}

    def capture(url, **kwargs):
        posted.append((url, kwargs["json"]))
        return _Response()

    monkeypatch.setattr(requests, "post", capture)
    spec = importlib.util.spec_from_file_location("memory_tool", MEMORY_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tools = module.Tools()
    tools.valves.max_recall_results = 500
    user = {"id": "6f1c2a52-3c0e-4d5b-9a51-1f0b0d6c1e2a"}

    asyncio.run(tools.remember("x" * 30000, user))
    asyncio.run(tools.recall("q" * 5000, user))
    assert [url.rsplit("/", 1)[-1] for url, _ in posted] == ["extract", "recall"]
    assert len(posted[0][1]["messages"][0]["content"]) == 20000
    assert len(posted[1][1]["query"]) == 4000
    assert posted[1][1]["limit"] == 100

    posted.clear()
    assert asyncio.run(tools.remember("   ", user)).startswith("Please provide")
    assert asyncio.run(tools.recall(None, user)).startswith("Please provide")
    assert posted == []
