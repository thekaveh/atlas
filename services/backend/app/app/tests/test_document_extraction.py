from __future__ import annotations

import asyncio
import sys
import types

import httpx
import pytest

from document_extraction import (
    DocumentExtractionError,
    DocumentExtractor,
    DocumentExtractorConfig,
    DocumentTooLargeError,
    ExtractionUnavailableError,
)


if "ray" not in sys.modules:
    _ray_stub = types.ModuleType("ray")
    _ray_job_stub = types.ModuleType("ray.job_submission")
    _ray_job_stub.JobSubmissionClient = None
    _ray_stub.job_submission = _ray_job_stub
    sys.modules["ray"] = _ray_stub
    sys.modules["ray.job_submission"] = _ray_job_stub


class FakeResponse:
    def __init__(self, status_code: int, *, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


class FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected POST to {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def put(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected PUT to {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("value", ("bad", "nan", "inf", "0", "-1", "3601"))
def test_document_extractor_rejects_malformed_or_unbounded_timeout(
    monkeypatch, value
) -> None:
    monkeypatch.setenv("TIKA_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="TIKA_TIMEOUT_SECONDS"):
        DocumentExtractorConfig.from_env()


def test_docling_success_does_not_call_tika() -> None:
    client = FakeAsyncClient(
        [
            FakeResponse(
                200,
                json_data={
                    "content": "# Parsed by Docling",
                    "format": "markdown",
                    "metadata": {
                        "pages": 1,
                        "tables": 0,
                        "images": 0,
                        "formulas": 0,
                        "processing_time": 0.2,
                        "source_format": "pdf",
                        "file_size": 7,
                    },
                    "chunks": [],
                },
            )
        ]
    )
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            tika_endpoint="http://tika:9998",
        ),
        http_client=client,
    )

    result = _run(
        extractor.extract(
            content=b"%PDF-1",
            filename="paper.pdf",
            content_type="application/pdf",
        )
    )

    assert result.extractor == "docling"
    assert result.content == "# Parsed by Docling"
    assert result.fallback_reason is None
    assert result.degraded is False
    assert result.provenance["filename"] == "paper.pdf"
    assert result.provenance["content_type"] == "application/pdf"
    assert result.provenance["file_size"] == 6
    assert [call[0] for call in client.calls] == [
        "http://docling-gpu:8000/v1/document/convert"
    ]


def test_docling_unsupported_falls_back_to_tika_with_provenance() -> None:
    client = FakeAsyncClient(
        [
            FakeResponse(415, json_data={"detail": "unsupported-format: rtf"}),
            FakeResponse(200, text="Plain text from Tika"),
        ]
    )
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            tika_endpoint="http://tika:9998",
        ),
        http_client=client,
    )

    result = _run(
        extractor.extract(
            content=b"opaque-binary",
            filename="notes.unknown",
            content_type="application/octet-stream",
        )
    )

    assert result.extractor == "tika"
    assert result.content == "Plain text from Tika"
    assert result.fallback_reason == "docling-unsupported"
    assert result.degraded is True
    assert result.metadata["source_format"] == "unknown"
    assert result.provenance == {
        "filename": "notes.unknown",
        "content_type": "application/octet-stream",
        "file_size": 13,
        "source_extractor": "tika",
    }
    assert [call[0] for call in client.calls] == [
        "http://docling-gpu:8000/v1/document/convert",
        "http://tika:9998/tika/text",
    ]


def test_long_tail_extension_routes_to_tika_without_docling() -> None:
    client = FakeAsyncClient([FakeResponse(200, text="Email body")])
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            tika_endpoint="http://tika:9998",
        ),
        http_client=client,
    )

    result = _run(
        extractor.extract(
            content=b"From: a@example.test\n\nHello",
            filename="message.eml",
            content_type="message/rfc822",
        )
    )

    assert result.extractor == "tika"
    assert result.fallback_reason == "long-tail-format"
    assert result.degraded is True
    assert [call[0] for call in client.calls] == ["http://tika:9998/tika/text"]


def test_explicit_tika_selection_bypasses_docling() -> None:
    client = FakeAsyncClient([FakeResponse(200, text="Forced Tika text")])
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            tika_endpoint="http://tika:9998",
        ),
        http_client=client,
    )

    result = _run(
        extractor.extract(
            content=b"plain bytes",
            filename="notes.txt",
            content_type="text/plain",
            extractor="tika",
        )
    )

    assert result.extractor == "tika"
    assert result.content == "Forced Tika text"
    assert [call[0] for call in client.calls] == ["http://tika:9998/tika/text"]


def test_explicit_docling_selection_does_not_hide_unsupported_response() -> None:
    client = FakeAsyncClient(
        [FakeResponse(415, json_data={"detail": "unsupported format"})]
    )
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            tika_endpoint="http://tika:9998",
        ),
        http_client=client,
    )

    with pytest.raises(DocumentExtractionError, match="Docling.*415"):
        _run(
            extractor.extract(
                content=b"opaque",
                filename="notes.unknown",
                content_type="application/octet-stream",
                extractor="docling",
            )
        )

    assert [call[0] for call in client.calls] == [
        "http://docling-gpu:8000/v1/document/convert"
    ]


def test_disabled_tika_after_docling_unsupported_is_clear_error() -> None:
    client = FakeAsyncClient(
        [FakeResponse(415, json_data={"detail": "unsupported format"})]
    )
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            tika_endpoint="",
        ),
        http_client=client,
    )

    with pytest.raises(ExtractionUnavailableError, match="Tika fallback is disabled"):
        _run(
            extractor.extract(
                content=b"opaque-binary",
                filename="notes.unknown",
                content_type="application/octet-stream",
            )
        )


def test_size_guard_rejects_before_network_call() -> None:
    client = FakeAsyncClient([])
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            tika_endpoint="http://tika:9998",
            max_file_size=4,
        ),
        http_client=client,
    )

    with pytest.raises(DocumentTooLargeError, match="exceeds maximum extraction size"):
        _run(
            extractor.extract(
                content=b"12345",
                filename="too-big.txt",
                content_type="text/plain",
            )
        )
    assert client.calls == []


def test_docling_transport_error_is_mapped_to_extraction_error() -> None:
    client = FakeAsyncClient([httpx.TimeoutException("slow")])
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            tika_endpoint="http://tika:9998",
            timeout_seconds=0.1,
        ),
        http_client=client,
    )

    with pytest.raises(DocumentExtractionError, match="Docling extraction request timed out"):
        _run(
            extractor.extract(
                content=b"%PDF-1",
                filename="paper.pdf",
                content_type="application/pdf",
            )
        )


def test_tika_transport_error_is_mapped_to_extraction_error() -> None:
    request = httpx.Request("PUT", "http://tika:9998/tika/text")
    client = FakeAsyncClient([httpx.ConnectError("refused", request=request)])
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            tika_endpoint="http://tika:9998",
        ),
        http_client=client,
    )

    with pytest.raises(DocumentExtractionError, match="Tika extraction request failed"):
        _run(
            extractor.extract(
                content=b"From: a@example.test\n\nHello",
                filename="message.eml",
                content_type="message/rfc822",
            )
        )


def test_extract_route_maps_document_extraction_error_to_502(
    fastapi_client,
    monkeypatch,
) -> None:
    import main

    class FailingExtractor:
        async def extract(self, **_kwargs):
            raise DocumentExtractionError("Docling extraction request failed: refused")

    monkeypatch.setattr(main, "document_extractor", FailingExtractor())

    response = fastapi_client.post(
        "/documents/extract",
        files={"file": ("paper.pdf", b"%PDF-1", "application/pdf")},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Docling extraction request failed: refused"
    }
