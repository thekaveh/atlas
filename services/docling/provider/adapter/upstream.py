"""Authenticated client for Docling's internal LightRAG bundle route."""

from __future__ import annotations

import asyncio
import os
import tempfile
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
        max_capacity_retries: int = 2,
        result_root: Path | None = None,
        max_result_bytes: int = 104_857_600,
    ) -> None:
        bundle_path = "/internal/lightrag/bundle"
        endpoint = endpoint.rstrip("/")
        self.endpoint = endpoint if endpoint.endswith(bundle_path) else endpoint + bundle_path
        self.token = token
        self.transport = transport
        self.retry_delay_seconds = retry_delay_seconds
        self.max_capacity_retries = max_capacity_retries
        self.result_root = result_root
        self.max_result_bytes = max_result_bytes
        if max_capacity_retries < 0:
            raise ValueError("max_capacity_retries must be non-negative")
        if max_result_bytes < 1:
            raise ValueError("max_result_bytes must be positive")

    async def convert(
        self, upload_path: Path, upload_name: str, timeout_seconds: int
    ) -> Path:
        if not self.token:
            raise UpstreamConversionError("Docling provider credential is unavailable")
        try:
            return await asyncio.wait_for(
                self._convert_with_retry(upload_path, upload_name),
                timeout=timeout_seconds,
            )
        except (asyncio.TimeoutError, httpx.HTTPError) as exc:
            raise UpstreamConversionError("Docling conversion failed") from exc

    async def _convert_with_retry(self, upload_path: Path, upload_name: str) -> Path:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(transport=self.transport) as client:
            for attempt in range(self.max_capacity_retries + 1):
                with upload_path.open("rb") as stream:
                    async with client.stream(
                        "POST",
                        self.endpoint,
                        headers=headers,
                        files={"file": (upload_name, stream, "application/octet-stream")},
                    ) as response:
                        if response.status_code == 429:
                            if attempt >= self.max_capacity_retries:
                                raise UpstreamConversionError(
                                    "Docling conversion capacity remained unavailable"
                                )
                        elif response.status_code != 200:
                            raise UpstreamConversionError("Docling conversion failed")
                        elif "zip" not in response.headers.get(
                            "content-type", ""
                        ).lower():
                            raise UpstreamConversionError(
                                "Docling conversion returned an invalid result"
                            )
                        else:
                            content_length = response.headers.get("content-length")
                            if content_length:
                                try:
                                    declared_size = int(content_length)
                                except ValueError as exc:
                                    raise UpstreamConversionError(
                                        "Docling conversion returned invalid metadata"
                                    ) from exc
                                if declared_size < 0:
                                    raise UpstreamConversionError(
                                        "Docling conversion returned invalid metadata"
                                    )
                                if declared_size > self.max_result_bytes:
                                    raise UpstreamConversionError(
                                        "Docling conversion result is too large"
                                    )
                            return await self._stream_result(response)
                await asyncio.sleep(self.retry_delay_seconds)
        raise UpstreamConversionError("Docling conversion failed")

    async def _stream_result(self, response) -> Path:
        fd, raw_path = tempfile.mkstemp(suffix=".zip", dir=self.result_root)
        os.close(fd)
        path = Path(raw_path)
        size = 0
        try:
            with path.open("wb") as stream:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_result_bytes:
                        raise UpstreamConversionError(
                            "Docling conversion result is too large"
                        )
                    await asyncio.to_thread(stream.write, chunk)
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise
