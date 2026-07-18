from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping
import logging
import math
import os

import httpx


logger = logging.getLogger(__name__)

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
MAX_TIMEOUT_SECONDS = 3600.0


def _timeout_from_env() -> float:
    raw = os.getenv("TIKA_TIMEOUT_SECONDS", "30")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("TIKA_TIMEOUT_SECONDS must be a finite number") from exc
    if not math.isfinite(value) or value <= 0 or value > MAX_TIMEOUT_SECONDS:
        raise ValueError(
            "TIKA_TIMEOUT_SECONDS must be finite, greater than 0, and at most "
            "3600 seconds"
        )
    return value


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
            timeout_seconds=_timeout_from_env(),
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

        logger.warning(
            "Docling extraction failed with HTTP %s: %s",
            response.status_code,
            self._safe_response_text(response),
        )
        raise DocumentExtractionError(
            f"Docling extraction failed with HTTP {response.status_code}"
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

        # Plain-text extraction is the BARE `PUT /tika` endpoint (getText,
        # @Produces text/plain) on the pinned apache/tika 3.3.1. `/tika/text`
        # path-matches getJson (@Produces application/json) instead, so with an
        # `Accept: text/plain` header it returns 406 → the whole Tika fallback
        # fails. (Tika 4.x adds explicit `/tika/text`, but `Accept: text/plain`
        # selects getText on both, so the bare path is version-safe.)
        url = f"{self.config.tika_endpoint.rstrip('/')}/tika"
        response = await self._put(
            url,
            content=content,
            headers={
                "Accept": "text/plain",
                "Content-Type": content_type or "application/octet-stream",
            },
        )
        if response.status_code >= 400:
            logger.warning(
                "Tika extraction failed with HTTP %s: %s",
                response.status_code,
                self._safe_response_text(response),
            )
            raise DocumentExtractionError(
                f"Tika extraction failed with HTTP {response.status_code}"
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
            logger.warning("Docling extraction request failed", exc_info=True)
            raise DocumentExtractionError(
                "Docling extraction request failed"
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
            logger.warning("Tika extraction request failed", exc_info=True)
            raise DocumentExtractionError("Tika extraction request failed") from exc

    def _docling_result(
        self,
        response: Any,
        filename: str,
        content_type: str | None,
        file_size: int,
    ) -> DocumentExtractionResult:
        try:
            payload = response.json()
        except Exception as exc:
            logger.warning("Docling returned non-JSON success response", exc_info=True)
            raise DocumentExtractionError("Docling returned an invalid response") from exc
        if not isinstance(payload, Mapping):
            raise DocumentExtractionError("Docling returned an invalid response")
        content = payload.get("content")
        output_format = payload.get("format")
        raw_metadata = payload.get("metadata")
        chunks = payload.get("chunks")
        if (
            not isinstance(content, str)
            or not isinstance(output_format, str)
            or not output_format.strip()
            or not isinstance(raw_metadata, Mapping)
            or (chunks is not None and not isinstance(chunks, list))
            or (
                isinstance(chunks, list)
                and any(not isinstance(chunk, Mapping) for chunk in chunks)
            )
        ):
            logger.warning("Docling returned malformed success payload")
            raise DocumentExtractionError("Docling returned an invalid response")
        metadata = dict(raw_metadata)
        metadata.setdefault("file_size", file_size)
        metadata.setdefault("content_type", content_type or "")
        return DocumentExtractionResult(
            content=content,
            extractor="docling",
            format=output_format,
            chunks=[dict(chunk) for chunk in chunks] if chunks is not None else None,
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
