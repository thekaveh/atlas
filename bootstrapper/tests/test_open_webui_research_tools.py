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
