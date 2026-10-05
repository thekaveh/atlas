from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "services" / "open-webui" / "extras" / "tools"


def test_open_webui_research_tools_use_current_ldr_assistant_id() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            TOOLS_DIR / "research_tool.py",
            TOOLS_DIR / "research_streaming_tool.py",
        ]
    )

    assert "a6ab75b8-fb3d-5c2c-a436-2fee55e33a06" not in combined
    assert "ollama_deep_researcher" in combined
    assert '"on_disconnect": "cancel"' in combined


def test_open_webui_research_tool_does_not_call_unsupported_run_cancel() -> None:
    text = (TOOLS_DIR / "research_tool.py").read_text(encoding="utf-8")

    assert "/runs/cancel" not in text
    assert "has been cancelled" not in text


def test_streaming_research_tool_is_gated_and_timed_like_research_tool() -> None:
    # show_progress used to disable the whole tool, and a 300s cap cancelled
    # typical 3-loop runs that research_tool allows 900s for.
    text = (TOOLS_DIR / "research_streaming_tool.py").read_text(encoding="utf-8")

    assert "if not self.valves.enable_tool:" in text
    assert "if not self.valves.show_progress:" not in text
    assert "default=900" in text
    assert ":param query:" in text


def test_research_tools_do_not_block_open_webui_event_loop() -> None:
    # Open WebUI 0.6.32 awaits a sync tool inline on its event loop, so a
    # 900s research call stalled every user; block in a worker thread instead.
    for name, method in (
        ("research_tool.py", "async def research("),
        ("research_streaming_tool.py", "async def research_with_progress("),
    ):
        text = (TOOLS_DIR / name).read_text(encoding="utf-8")
        assert method in text
        assert "await asyncio.to_thread(self._research_blocking, query)" in text
