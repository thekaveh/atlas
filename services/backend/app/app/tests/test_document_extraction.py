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


TEST_DOCLING_TOKEN = "docling-test-token"


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


@pytest.mark.parametrize("value", ("bad", "0", "-1"))
def test_document_extractor_rejects_non_positive_size(monkeypatch, value) -> None:
    monkeypatch.setenv("TIKA_MAX_FILE_SIZE", value)
    with pytest.raises(ValueError, match="TIKA_MAX_FILE_SIZE"):
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
            docling_api_token=TEST_DOCLING_TOKEN,
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


def test_docling_request_uses_bearer_without_logging_token(caplog) -> None:
    token = "provider-token-that-must-not-be-logged"
    client = FakeAsyncClient(
        [
            FakeResponse(
                200,
                json_data={
                    "content": "converted",
                    "format": "markdown",
                    "metadata": {},
                    "chunks": [],
                },
            )
        ]
    )
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            docling_api_token=token,
        ),
        http_client=client,
    )

    _run(
        extractor.extract(
            content=b"document",
            filename="paper.pdf",
            content_type="application/pdf",
        )
    )

    _, kwargs = client.calls[0]
    assert kwargs["headers"] == {"Authorization": f"Bearer {token}"}
    assert token not in caplog.text


def test_docling_endpoint_without_token_fails_before_network() -> None:
    client = FakeAsyncClient([])
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            docling_api_token="",
        ),
        http_client=client,
    )

    with pytest.raises(ExtractionUnavailableError, match="credential"):
        _run(
            extractor.extract(
                content=b"document",
                filename="paper.pdf",
                content_type="application/pdf",
            )
        )
    assert client.calls == []


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"content": "text", "format": "markdown"},
        {"content": 1, "format": "markdown", "metadata": {}},
        {"content": "text", "format": "", "metadata": {}},
        {"content": "text", "format": "markdown", "metadata": [], "chunks": []},
        {"content": "text", "format": "markdown", "metadata": {}, "chunks": {}},
    ),
)
def test_docling_malformed_success_body_is_rejected(payload) -> None:
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            docling_api_token=TEST_DOCLING_TOKEN,
        ),
        http_client=FakeAsyncClient([FakeResponse(200, json_data=payload)]),
    )

    with pytest.raises(DocumentExtractionError, match="invalid response"):
        _run(
            extractor.extract(
                content=b"document",
                filename="paper.pdf",
                content_type="application/pdf",
            )
        )


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
            docling_api_token=TEST_DOCLING_TOKEN,
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
        "http://tika:9998/tika",
    ]


def test_long_tail_extension_routes_to_tika_without_docling() -> None:
    client = FakeAsyncClient([FakeResponse(200, text="Email body")])
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            docling_api_token=TEST_DOCLING_TOKEN,
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
    assert [call[0] for call in client.calls] == ["http://tika:9998/tika"]


def test_explicit_tika_selection_bypasses_docling() -> None:
    client = FakeAsyncClient([FakeResponse(200, text="Forced Tika text")])
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            docling_api_token=TEST_DOCLING_TOKEN,
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
    assert [call[0] for call in client.calls] == ["http://tika:9998/tika"]


def test_explicit_docling_selection_does_not_hide_unsupported_response() -> None:
    client = FakeAsyncClient(
        [FakeResponse(415, json_data={"detail": "unsupported format"})]
    )
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            docling_api_token=TEST_DOCLING_TOKEN,
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
            docling_api_token=TEST_DOCLING_TOKEN,
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
            docling_api_token=TEST_DOCLING_TOKEN,
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
            docling_api_token=TEST_DOCLING_TOKEN,
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
    request = httpx.Request("PUT", "http://tika:9998/tika")
    client = FakeAsyncClient([httpx.ConnectError("refused", request=request)])
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            docling_api_token=TEST_DOCLING_TOKEN,
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
        "detail": "Document extraction failed"
    }


def test_upstream_failure_body_is_not_exposed() -> None:
    extractor = DocumentExtractor(
        DocumentExtractorConfig(
            docling_endpoint="http://docling-gpu:8000",
            docling_api_token=TEST_DOCLING_TOKEN,
        ),
        http_client=FakeAsyncClient(
            [FakeResponse(500, text="postgresql://admin:secret@db/internal")]
        ),
    )

    with pytest.raises(DocumentExtractionError) as captured:
        _run(
            extractor.extract(
                content=b"document",
                filename="paper.pdf",
                content_type="application/pdf",
            )
        )

    assert "secret" not in str(captured.value)


def test_docling_uses_its_own_timeout_and_legacy_formats_go_to_tika(monkeypatch) -> None:
    """TIKA_TIMEOUT_SECONDS (30 s) cut Docling's cold-start/large-PDF runs, and
    Atlas's Docling answers unsupported formats with 500, never 415."""
    import document_extraction as module

    monkeypatch.setenv("DOCLING_INFERENCE_TIMEOUT_SECONDS", "600")
    config = DocumentExtractorConfig.from_env()
    assert config.timeout_seconds == 30.0
    assert config.docling_timeout_seconds == 630.0
    for name in ("old.doc", "sheet.xls", "deck.ppt", "book.epub"):
        assert DocumentExtractor(config)._is_long_tail(name, None), name
    assert not DocumentExtractor(config)._is_long_tail("paper.pdf", "application/pdf")
    # Markdown/CSV/HTML often arrive labelled text/plain; Docling handles them,
    # and Tika is disabled by default, so text/plain must not reroute them.
    assert not DocumentExtractor(config)._is_long_tail("notes.md", "text/plain")
    assert not DocumentExtractor(config)._is_long_tail("data.csv", "application/vnd.ms-excel")
    assert module.DOCLING_TIMEOUT_MARGIN_SECONDS == 30.0


def _docling_ok():
    return FakeResponse(200, json_data={
        "content": "# ok", "format": "markdown", "chunks": [],
        "metadata": {"pages": 1, "tables": 0, "images": 0, "formulas": 0,
                     "processing_time": 0.1, "source_format": "pdf", "file_size": 6},
    })


def _busy_extractor(responses, busy_wait=30.0):
    client = FakeAsyncClient(responses)
    extractor = DocumentExtractor(
        DocumentExtractorConfig(docling_endpoint="http://docling-gpu:8000",
                                docling_api_token=TEST_DOCLING_TOKEN, tika_endpoint="http://tika:9998",
                                docling_busy_wait_seconds=busy_wait),
        http_client=client,
    )
    return client, extractor


def test_docling_busy_429_is_retried(monkeypatch) -> None:
    # Docling converts one document at a time; Celery runs two jobs.
    import document_extraction
    slept = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(document_extraction.asyncio, "sleep", no_sleep)
    client, extractor = _busy_extractor([FakeResponse(429), FakeResponse(429), _docling_ok()])
    result = _run(extractor.extract(content=b"%PDF-1", filename="a.pdf", content_type="application/pdf"))
    assert result.extractor == "docling"
    assert len(client.calls) == 3 and slept == [1.0, 2.0]  # growing backoff


def test_docling_still_busy_maps_to_unavailable(monkeypatch) -> None:
    import document_extraction

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(document_extraction.asyncio, "sleep", no_sleep)
    _client, extractor = _busy_extractor([FakeResponse(429)], busy_wait=0.0)
    with pytest.raises(ExtractionUnavailableError):
        _run(extractor.extract(content=b"%PDF-1", filename="a.pdf", content_type="application/pdf"))


def test_docling_chunking_can_be_disabled_for_rechunking_callers() -> None:
    client, extractor = _busy_extractor([_docling_ok()])
    _run(extractor.extract(content=b"%PDF-1", filename="a.pdf", content_type="application/pdf", chunking=False))
    assert client.calls[0][1]["data"]["enable_chunking"] == "false"


def test_docling_busy_sleep_is_clamped_to_the_remaining_budget(monkeypatch) -> None:
    import document_extraction
    slept = []
    real_sleep = asyncio.sleep

    async def recording_sleep(seconds):
        slept.append(seconds)
        await real_sleep(seconds)  # real clock, so the budget runs out

    monkeypatch.setattr(document_extraction.asyncio, "sleep", recording_sleep)
    _client, extractor = _busy_extractor([FakeResponse(429)] * 50, busy_wait=0.25)
    with pytest.raises(ExtractionUnavailableError):
        _run(extractor.extract(content=b"%PDF-1", filename="a.pdf", content_type="application/pdf"))
    assert slept and all(seconds <= 0.25 for seconds in slept)


def test_ingestion_parser_waits_longer_for_busy_docling() -> None:
    from rag_ingestion.clients import ParserAdapter

    assert ParserAdapter()._get_extractor().config.docling_busy_wait_seconds == 120.0
