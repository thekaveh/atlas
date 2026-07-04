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
