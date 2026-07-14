from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping
import os

import httpx


LONG_TAIL_EXTENSIONS = {
    ".eml",
    ".msg",
    ".rtf",
    ".odt",
    ".ods",
    ".odp",
    ".zip",
    ".tar",
    ".gz",
    ".gzip",
    ".bz2",
}

LONG_TAIL_CONTENT_TYPES = {
    "message/rfc822",
    "application/vnd.ms-outlook",
    "application/rtf",
    "text/rtf",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "application/zip",
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
    "application/x-bzip2",
}

UNSUPPORTED_MARKERS = (
    "unsupported-format",
    "unsupported format",
    "unsupported_media_type",
    "unsupported media type",
)


class DocumentExtractionError(RuntimeError):
    """Base error for document extraction failures."""


class DocumentTooLargeError(DocumentExtractionError):
    """Raised before any network call when an upload exceeds the size cap."""


class ExtractionUnavailableError(DocumentExtractionError):
    """Raised when the selected extractor path cannot run."""


@dataclass(frozen=True)
class DocumentExtractorConfig:
    docling_endpoint: str = ""
    tika_endpoint: str = ""
    max_file_size: int = 50 * 1024 * 1024
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "DocumentExtractorConfig":
        return cls(
            docling_endpoint=os.getenv("DOCLING_ENDPOINT", ""),
            tika_endpoint=os.getenv("TIKA_ENDPOINT", ""),
            max_file_size=int(os.getenv("TIKA_MAX_FILE_SIZE", str(50 * 1024 * 1024))),
            timeout_seconds=float(os.getenv("TIKA_TIMEOUT_SECONDS", "30")),
        )


@dataclass(frozen=True)
class DocumentExtractionResult:
    content: str
    extractor: str
    metadata: dict[str, Any]
    provenance: dict[str, Any]
    format: str = "markdown"
    chunks: list[dict[str, Any]] | None = None
    fallback_reason: str | None = None
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DocumentExtractor:
    def __init__(
        self,
        config: DocumentExtractorConfig | None = None,
        *,
        http_client: Any | None = None,
    ) -> None:
        self.config = config or DocumentExtractorConfig.from_env()
        self._http_client = http_client

    async def extract(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str | None,
        extractor: str | None = None,
    ) -> DocumentExtractionResult:
        if len(content) > self.config.max_file_size:
            raise DocumentTooLargeError(
                f"{filename} exceeds maximum extraction size of "
                f"{self.config.max_file_size} bytes"
            )

        if extractor not in {None, "docling", "tika"}:
            raise ValueError(f"Unsupported document extractor: {extractor}")
        if extractor == "tika":
            return await self._extract_with_tika(
                content=content,
                filename=filename,
                content_type=content_type,
                fallback_reason="parser-order",
            )

        if extractor is None and self._is_long_tail(filename, content_type):
            return await self._extract_with_tika(
                content=content,
                filename=filename,
                content_type=content_type,
                fallback_reason="long-tail-format",
            )

        if not self.config.docling_endpoint:
            raise ExtractionUnavailableError("Docling endpoint is disabled")

        response = await self._post_docling(content, filename, content_type)
        if response.status_code == 200:
            return self._docling_result(response, filename, content_type, len(content))

        if extractor is None and self._is_docling_unsupported(response):
            return await self._extract_with_tika(
                content=content,
                filename=filename,
                content_type=content_type,
                fallback_reason="docling-unsupported",
            )

        raise DocumentExtractionError(
            f"Docling extraction failed with HTTP {response.status_code}: "
            f"{self._safe_response_text(response)}"
        )

    async def _post_docling(
        self,
        content: bytes,
        filename: str,
        content_type: str | None,
    ) -> Any:
        url = f"{self.config.docling_endpoint.rstrip('/')}/v1/document/convert"
        files = {
            "file": (
                filename,
                content,
                content_type or "application/octet-stream",
            )
        }
        data = {
            "output_format": "markdown",
            "enable_chunking": "true",
        }
        return await self._post(url, files=files, data=data)

    async def _extract_with_tika(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str | None,
        fallback_reason: str,
    ) -> DocumentExtractionResult:
        if not self.config.tika_endpoint:
            raise ExtractionUnavailableError(
                "Tika fallback is disabled; set TIKA_SOURCE=container or "
                "TIKA_SOURCE=tika-localhost"
            )

        url = f"{self.config.tika_endpoint.rstrip('/')}/tika/text"
        response = await self._put(
            url,
            content=content,
            headers={
                "Accept": "text/plain",
                "Content-Type": content_type or "application/octet-stream",
            },
        )
        if response.status_code >= 400:
            raise DocumentExtractionError(
                f"Tika extraction failed with HTTP {response.status_code}: "
                f"{self._safe_response_text(response)}"
            )

        source_format = self._source_format(filename, content_type)
        return DocumentExtractionResult(
            content=response.text,
            extractor="tika",
            format="text",
            fallback_reason=fallback_reason,
            degraded=True,
            metadata={
                "source_format": source_format,
                "file_size": len(content),
                "content_type": content_type or "",
            },
            provenance=self._provenance(
                filename=filename,
                content_type=content_type,
                file_size=len(content),
                source_extractor="tika",
            ),
        )

    async def _post(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self.config.timeout_seconds)
        try:
            if self._http_client is not None:
                return await self._http_client.post(url, **kwargs)
            async with httpx.AsyncClient() as client:
                return await client.post(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise DocumentExtractionError(
                f"Docling extraction request timed out after {self.config.timeout_seconds} seconds"
            ) from exc
        except httpx.RequestError as exc:
            raise DocumentExtractionError(
                f"Docling extraction request failed: {exc}"
            ) from exc

    async def _put(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self.config.timeout_seconds)
        try:
            if self._http_client is not None:
                return await self._http_client.put(url, **kwargs)
            async with httpx.AsyncClient() as client:
                return await client.put(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise DocumentExtractionError(
                f"Tika extraction request timed out after {self.config.timeout_seconds} seconds"
            ) from exc
        except httpx.RequestError as exc:
            raise DocumentExtractionError(
                f"Tika extraction request failed: {exc}"
            ) from exc

    def _docling_result(
        self,
        response: Any,
        filename: str,
        content_type: str | None,
        file_size: int,
    ) -> DocumentExtractionResult:
        payload = response.json()
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault("file_size", file_size)
        metadata.setdefault("content_type", content_type or "")
        return DocumentExtractionResult(
            content=payload.get("content", ""),
            extractor="docling",
            format=payload.get("format", "markdown"),
            chunks=payload.get("chunks"),
            metadata=metadata,
            provenance=self._provenance(
                filename=filename,
                content_type=content_type,
                file_size=file_size,
                source_extractor="docling",
            ),
        )

    def _is_long_tail(self, filename: str, content_type: str | None) -> bool:
        suffix = Path(filename or "").suffix.lower()
        normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
        return suffix in LONG_TAIL_EXTENSIONS or normalized_type in LONG_TAIL_CONTENT_TYPES

    def _is_docling_unsupported(self, response: Any) -> bool:
        if response.status_code == 415:
            return True
        text = self._safe_response_text(response).lower()
        try:
            data = response.json()
        except Exception:
            data = None
        if isinstance(data, Mapping):
            text += " " + " ".join(str(value).lower() for value in data.values())
        return any(marker in text for marker in UNSUPPORTED_MARKERS)

    def _source_format(self, filename: str, content_type: str | None) -> str:
        suffix = Path(filename or "").suffix.lower().lstrip(".")
        if suffix:
            return suffix
        normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
        return normalized_type or "unknown"

    def _provenance(
        self,
        *,
        filename: str,
        content_type: str | None,
        file_size: int,
        source_extractor: str,
    ) -> dict[str, Any]:
        return {
            "filename": filename,
            "content_type": content_type or "",
            "file_size": file_size,
            "source_extractor": source_extractor,
        }

    def _safe_response_text(self, response: Any) -> str:
        return (getattr(response, "text", "") or "")[:500]
