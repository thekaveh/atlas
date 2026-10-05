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
