"""Authenticated client for Docling's internal LightRAG bundle route."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx2 as httpx


class UpstreamConversionError(RuntimeError):
    pass


class DoclingUpstream:
    def __init__(
        self,
        *,
        endpoint: str,
        token: str,
        transport: Any = None,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        bundle_path = "/internal/lightrag/bundle"
        endpoint = endpoint.rstrip("/")
        self.endpoint = endpoint if endpoint.endswith(bundle_path) else endpoint + bundle_path
        self.token = token
        self.transport = transport
        self.retry_delay_seconds = retry_delay_seconds

    async def convert(
        self, upload_path: Path, upload_name: str, timeout_seconds: int
    ) -> bytes:
        if not self.token:
            raise UpstreamConversionError("Docling provider credential is unavailable")
        try:
            return await asyncio.wait_for(
                self._convert_with_retry(upload_path, upload_name),
                timeout=timeout_seconds,
            )
        except (asyncio.TimeoutError, httpx.HTTPError) as exc:
            raise UpstreamConversionError("Docling conversion failed") from exc

    async def _convert_with_retry(self, upload_path: Path, upload_name: str) -> bytes:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(transport=self.transport) as client:
            while True:
                with upload_path.open("rb") as stream:
                    response = await client.post(
                        self.endpoint,
                        headers=headers,
                        files={"file": (upload_name, stream, "application/octet-stream")},
                    )
                if response.status_code != 429:
                    break
                await asyncio.sleep(self.retry_delay_seconds)
        if response.status_code != 200:
            raise UpstreamConversionError("Docling conversion failed")
        if "zip" not in response.headers.get("content-type", "").lower():
            raise UpstreamConversionError("Docling conversion returned an invalid result")
        return response.content
