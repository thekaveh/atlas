"""
title: Memory Auto-Extraction
author: Atlas
author_url: https://github.com/thekaveh/atlas
description: Automatically extracts and stores memories from conversations
required_open_webui_version: 0.4.4
requirements: requests
version: 1.0.0
license: MIT
type: filter
"""

import sys
import threading
import os
import requests
from pydantic import BaseModel, Field
from typing import Optional


class Filter:
    class Valves(BaseModel):
        backend_url: str = Field(
            default="http://backend:8000", description="Backend API URL"
        )
        enabled: bool = Field(
            default=True, description="Enable automatic memory extraction"
        )
        min_messages: int = Field(
            default=4,
            description="Minimum number of messages before extraction triggers",
        )
        timeout: int = Field(default=120, description="Request timeout in seconds")

    def __init__(self):
        self.valves = self.Valves()

    @staticmethod
    def _backend_headers() -> dict[str, str]:
        token = (os.getenv("BACKEND_OPEN_WEBUI_API_TOKEN") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Pre-request hook: pass through without modification."""
        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Post-response hook: extract memories from conversation asynchronously."""
        if not self.valves.enabled:
            return body

        messages = body.get("messages", [])

        # Only trigger extraction after enough conversation has accumulated
        if len(messages) < self.valves.min_messages:
            return body

        user_id = ""
        if __user__:
            user_id = __user__.get("id", "")
        if not user_id:
            return body  # No valid user ID, skip extraction

        # Fire-and-forget: extract memories in a background thread
        # so we don't block the chat response
        def _extract():
            try:
                # Send the last few messages for extraction
                recent_messages = messages[-self.valves.min_messages :]
                formatted = [
                    {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                    for msg in recent_messages
                    if msg.get("content")
                ]

                if not formatted:
                    return

                requests.post(
                    f"{self.valves.backend_url}/memory/extract",
                    headers=self._backend_headers(),
                    json={
                        "user_id": user_id,
                        "messages": formatted,
                        "namespace": "default",
                    },
                    timeout=self.valves.timeout,
                )
            except Exception as e:
                print(f"memory_filter: extraction failed: {e}", file=sys.stderr)

        thread = threading.Thread(target=_extract, daemon=True)
        thread.start()

        return body
