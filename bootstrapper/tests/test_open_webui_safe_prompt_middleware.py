from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FUNCTION_PATH = ROOT / "services/open-webui/extras/functions/atlas_safe_prompt_middleware.py"
OPEN_WEBUI_README = ROOT / "services/open-webui/README.md"
PIPELINES_CANDIDATE = ROOT / "docs/research/candidates/open-webui-pipelines.md"


def _load_filter_module():
    assert FUNCTION_PATH.exists(), "Open WebUI safe prompt middleware function must exist"
    spec = importlib.util.spec_from_file_location("atlas_safe_prompt_middleware", FUNCTION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _messages(*contents: str) -> dict:
    return {"messages": [{"role": "user", "content": content} for content in contents]}


def test_safe_prompt_middleware_metadata_and_default_disabled() -> None:
    content = FUNCTION_PATH.read_text(encoding="utf-8")
    module = _load_filter_module()
    filter_instance = module.Filter()

    assert "title: Atlas Safe Prompt Middleware" in content
    assert "type: filter" in content
    assert filter_instance.valves.enabled is False
    assert filter_instance.valves.redact_secrets is True


def test_safe_prompt_middleware_passes_through_when_disabled() -> None:
    module = _load_filter_module()
    filter_instance = module.Filter()
    body = _messages("use sk-live-secret and password=my-password")

    result = asyncio.run(filter_instance.inlet(body, __user__={"id": "u1"}))

    assert result is body
    assert result["messages"][0]["content"] == "use sk-live-secret and password=my-password"


def test_safe_prompt_middleware_redacts_user_message_secrets_when_enabled() -> None:
    module = _load_filter_module()
    filter_instance = module.Filter()
    filter_instance.valves.enabled = True
    body = {
        "messages": [
            {"role": "system", "content": "keep system prompt unchanged"},
            {"role": "user", "content": "Bearer abc.def.ghi and sk-1234567890abcdef and password = hunter2"},
            {"role": "assistant", "content": "previous assistant text"},
        ]
    }

    result = asyncio.run(filter_instance.inlet(body, __user__={"id": "u1"}))

    assert result is not body
    assert result["messages"][0]["content"] == "keep system prompt unchanged"
    assert result["messages"][2]["content"] == "previous assistant text"
    user_text = result["messages"][1]["content"]
    assert "abc.def.ghi" not in user_text
    assert "sk-1234567890abcdef" not in user_text
    assert "hunter2" not in user_text
    assert "[REDACTED:" in user_text


def test_safe_prompt_middleware_documents_supported_open_webui_function_path() -> None:
    open_webui_readme = OPEN_WEBUI_README.read_text(encoding="utf-8")
    candidate = PIPELINES_CANDIDATE.read_text(encoding="utf-8")

    for expected in [
        "Atlas Safe Prompt Middleware",
        "inside Open WebUI before requests reach LiteLLM",
        "LiteLLM + Langfuse remains the stack-wide observability path",
        "standalone Pipelines are intentionally not added",
        "upstream now marks Pipelines as legacy",
        "OpenLIT remains deferred",
    ]:
        assert expected in open_webui_readme

    assert "Filter Function" in candidate
    assert "standalone Pipelines are intentionally not added" in candidate
    assert "OPEN_WEBUI_PIPELINES_SOURCE" not in candidate
