"""
title: Atlas Safe Prompt Middleware
author: Atlas
author_url: https://github.com/thekaveh/atlas
description: Disabled-by-default Open WebUI filter for conservative prompt redaction before LiteLLM
required_open_webui_version: 0.6.32
version: 0.1.0
license: MIT
type: filter
"""

from __future__ import annotations

import copy
import re
from typing import Any

try:
    from pydantic import BaseModel, Field
except ModuleNotFoundError:
    class BaseModel:
        def __init__(self, **values: Any) -> None:
            for key, value in values.items():
                setattr(self, key, value)

    def Field(*, default: Any, description: str = "") -> Any:
        return default


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "bearer-token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
        "Bearer [REDACTED:bearer-token]",
    ),
    (
        "api-key",
        re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),
        "[REDACTED:api-key]",
    ),
    (
        "aws-access-key",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        "[REDACTED:aws-access-key]",
    ),
    (
        "password",
        re.compile(r"\b(password|passwd|pwd)\s*=\s*([^\s,;]+)", re.IGNORECASE),
        r"\1=[REDACTED:password]",
    ),
)


def redact_text(value: str) -> str:
    """Redact common accidental secret patterns from user-provided text."""

    redacted = value
    for _, pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _redact_content(content: Any) -> Any:
    if isinstance(content, str):
        return redact_text(content)
    if isinstance(content, list):
        redacted_parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part = {**part, "text": redact_text(part["text"])}
            redacted_parts.append(part)
        return redacted_parts
    return content


class Filter:
    class Valves(BaseModel):
        enabled: bool = Field(
            default=False,
            description="Enable Atlas prompt middleware for this Open WebUI instance.",
        )
        redact_secrets: bool = Field(
            default=True,
            description="Redact obvious secrets from user messages before they reach LiteLLM.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __user__: dict | None = None) -> dict:
        """Run before Open WebUI sends the request to LiteLLM."""

        if not self.valves.enabled or not self.valves.redact_secrets:
            return body

        updated = copy.deepcopy(body)
        for message in updated.get("messages", []):
            if message.get("role") == "user" and "content" in message:
                message["content"] = _redact_content(message["content"])
        return updated

    async def outlet(self, body: dict, __user__: dict | None = None) -> dict:
        """Pass through model output; LiteLLM/Langfuse own stack-wide tracing."""

        return body
