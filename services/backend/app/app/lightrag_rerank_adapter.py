"""LightRAG → TEI rerank adapter (#415).

LightRAG's built-in Jina/Cohere rerank clients POST a payload shaped like
``{"model", "query", "documents", "top_n"?}`` and read back
``response_json["results"]`` where each item exposes ``index`` and
``relevance_score``. Atlas's TEI (text-embeddings-inference) ``/rerank``
endpoint speaks a *different* wire shape: it wants ``{"query", "texts"}`` and
returns a *top-level array* of ``{"index", "score", "text"?}`` sorted by score
descending. The two are not wire-compatible, which is why Atlas historically
kept ``RERANK_BINDING=null`` and rejected ``enable_rerank=true`` query profiles
(#414).

This module is the translation seam that closes that gap. It is deliberately a
thin backend route rather than a new always-on service: it owns no model, holds
no state, and simply rewrites request/response fields between the two
contracts. The route is auth-gated by a generated bearer token and is only
wired into LightRAG when the operator opts in
(``LIGHTRAG_RERANK_ADAPTER_ENABLED=true``) with TEI enabled; direct
LightRAG→TEI wiring stays forbidden.

Contracts pinned against upstream source (2026-07-11):
  * LightRAG ``lightrag/rerank.py::generic_rerank_api`` — request/response.
  * TEI ``docs/openapi.json`` — ``/rerank`` RerankRequest/RerankResponse.
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import List, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ─── Bounds ──────────────────────────────────────────────────────────
# Bound the request so a hostile or buggy caller cannot make the backend
# buffer an unbounded rerank batch into memory or hold a socket open
# forever. LightRAG only ever sends its retrieved top_k chunks, so these
# ceilings are generous relative to real traffic.
MAX_QUERY_CHARS = 8000
MAX_DOCUMENTS = 512
MAX_DOCUMENT_CHARS = 32000
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 3600.0
DEFAULT_BATCH_SIZE = 32


logger = logging.getLogger(__name__)


class RerankAdapterError(RuntimeError):
    """Base class for adapter failures (never leaks upstream secrets)."""


class RerankAdapterDependencyError(RerankAdapterError):
    """TEI reranker is not configured/reachable → surfaced as HTTP 503."""


class RerankAdapterTimeoutError(RerankAdapterError):
    """TEI did not respond within the timeout → surfaced as HTTP 504."""


class RerankAdapterUpstreamError(RerankAdapterError):
    """TEI returned an error status or a malformed body → HTTP 502."""


class RerankAdapterRequest(BaseModel):
    """LightRAG's Jina/Cohere-style rerank request.

    ``extra="ignore"`` keeps the adapter forward-compatible: LightRAG may add
    optional knobs (``return_documents``, ``extra_body`` keys) across versions
    and the adapter should not 422 on fields it does not consume.
    """

    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    # Bounded, but may legitimately be empty (LightRAG can call rerank with
    # zero retrieved chunks) — an empty list short-circuits to empty results.
    documents: List[str] = Field(default_factory=list, max_length=MAX_DOCUMENTS)
    # LightRAG passes ``top_n`` to cap the returned list. TEI's /rerank has no
    # top_n param, so the adapter applies it after receiving TEI's sorted list.
    top_n: Optional[int] = Field(default=None, ge=1, le=MAX_DOCUMENTS)
    # Model name reference only (echoed/ignored — TEI serves its configured
    # model). Bounded so it cannot be used as an unbounded field.
    model: Optional[str] = Field(default=None, max_length=256)
    # When true, echo each ranked document's text back (LightRAG ignores it,
    # but a direct caller may want it). Off by default to keep responses lean.
    return_documents: bool = False

    @field_validator("documents")
    @classmethod
    def _bound_document_lengths(cls, value: List[str]) -> List[str]:
        for index, doc in enumerate(value):
            if len(doc) > MAX_DOCUMENT_CHARS:
                raise ValueError(
                    f"documents[{index}] exceeds {MAX_DOCUMENT_CHARS} chars"
                )
        return value


class RerankResultItem(BaseModel):
    # ``index`` is the position in the ORIGINAL documents list; ``relevance_score``
    # is TEI's score renamed to the field LightRAG reads. ``document`` is only
    # populated when the caller asked for return_documents.
    index: int
    relevance_score: float
    document: Optional[str] = None


class RerankAdapterResponse(BaseModel):
    results: List[RerankResultItem]


def _tei_endpoint() -> str:
    return (os.getenv("TEI_RERANKER_ENDPOINT") or "").strip().rstrip("/")


def _timeout_seconds() -> float:
    raw = (os.getenv("LIGHTRAG_RERANK_ADAPTER_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            "LightRAG rerank adapter timeout must be a finite number"
        ) from exc
    if not math.isfinite(value) or value <= 0 or value > MAX_TIMEOUT_SECONDS:
        raise ValueError(
            "LightRAG rerank adapter timeout must be finite, greater than 0, "
            "and at most 3600 seconds"
        )
    return value


def _batch_size() -> int:
    raw = (os.getenv("TEI_RERANKER_MAX_CLIENT_BATCH_SIZE") or "").strip()
    if not raw:
        return DEFAULT_BATCH_SIZE
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "LightRAG rerank adapter batch size must be a positive integer"
        ) from exc
    if value <= 0:
        raise ValueError(
            "LightRAG rerank adapter batch size must be a positive integer"
        )
    # The request model already limits the complete input to MAX_DOCUMENTS.
    return min(value, MAX_DOCUMENTS)


def validate_rerank_adapter_config() -> None:
    _timeout_seconds()
    _batch_size()


def _parse_tei_items(payload: object, doc_count: int) -> List[dict]:
    """Validate TEI's response is the pinned top-level array of ranked items.

    Guards against a TEI version drift or a proxy returning HTML/an object so a
    malformed upstream body becomes a clean 502 rather than a KeyError 500.
    """
    if not isinstance(payload, list):
        raise RerankAdapterUpstreamError(
            "TEI /rerank returned an unexpected body (expected a JSON array)"
        )
    if len(payload) > doc_count:
        raise RerankAdapterUpstreamError(
            "TEI /rerank returned more results than submitted documents"
        )
    items: List[dict] = []
    seen_indexes: set[int] = set()
    for entry in payload:
        if not isinstance(entry, dict) or "index" not in entry or "score" not in entry:
            raise RerankAdapterUpstreamError(
                "TEI /rerank item missing required 'index'/'score' fields"
            )
        index = entry["index"]
        score = entry["score"]
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
        ):
            raise RerankAdapterUpstreamError(
                "TEI /rerank item has invalid 'index'/'score' types"
            )
        score = float(score)
        # A returned index outside the input range means the two sides
        # disagree about the batch — refuse to map it to a wrong document.
        if index < 0 or index >= doc_count:
            raise RerankAdapterUpstreamError(
                f"TEI /rerank returned out-of-range index {index} for {doc_count} documents"
            )
        if index in seen_indexes:
            raise RerankAdapterUpstreamError(
                f"TEI /rerank returned duplicate index {index}"
            )
        seen_indexes.add(index)
        items.append({"index": index, "score": score})
    return items


def rerank_via_tei(request: RerankAdapterRequest) -> RerankAdapterResponse:
    """Translate a LightRAG rerank request through TEI and back.

    Synchronous by design so the FastAPI route can offload it with
    ``asyncio.to_thread`` (matching the /api/rag/evaluate + /api/chunk pattern)
    and so unit tests can drive it without an event loop.
    """
    documents = list(request.documents)
    # No documents → nothing to rank. Short-circuit without a TEI round-trip so
    # an empty retrieval never 5xxs or wastes a call.
    if not documents:
        return RerankAdapterResponse(results=[])

    endpoint = _tei_endpoint()
    if not endpoint:
        raise RerankAdapterDependencyError(
            "TEI reranker is not configured (TEI_RERANKER_ENDPOINT is empty); "
            "enable TEI_RERANKER_SOURCE to use the LightRAG rerank adapter"
        )

    # TEI's contract: {query, texts} → sorted [{index, score, text?}].
    # Its client batch limit is lower than this adapter's bounded request size,
    # so preserve original indexes while ranking each safe-sized slice.
    batch_size = _batch_size()
    timeout_seconds = _timeout_seconds()
    deadline = time.monotonic() + timeout_seconds
    items: List[dict] = []
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            for offset in range(0, len(documents), batch_size):
                batch = documents[offset : offset + batch_size]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RerankAdapterTimeoutError(
                        "TEI /rerank timed out before all batches completed"
                    )
                response = client.post(
                    f"{endpoint}/rerank",
                    json={"query": request.query, "texts": batch},
                    timeout=remaining,
                )
                if response.status_code >= 400:
                    raise RerankAdapterUpstreamError(
                        f"TEI /rerank responded with HTTP {response.status_code}"
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise RerankAdapterUpstreamError(
                        "TEI /rerank returned a non-JSON body"
                    ) from exc
                for item in _parse_tei_items(payload, len(batch)):
                    items.append(
                        {"index": offset + item["index"], "score": item["score"]}
                    )
    except httpx.TimeoutException as exc:
        raise RerankAdapterTimeoutError(
            "TEI /rerank timed out before responding"
        ) from exc
    except httpx.HTTPError as exc:
        # Connection refused / DNS / transport error — TEI is unreachable.
        raise RerankAdapterDependencyError(
            "Failed to reach the TEI reranker endpoint"
        ) from exc
    logger.info(
        "LightRAG rerank completed: documents=%d batches=%d batch_size=%d",
        len(documents),
        math.ceil(len(documents) / batch_size),
        batch_size,
    )
    # TEI already sorts by score descending, but sort defensively so the
    # adapter's contract (best-first, deterministic tie-break by index) holds
    # regardless of upstream ordering guarantees.
    items.sort(key=lambda item: (-item["score"], item["index"]))
    if request.top_n is not None:
        items = items[: request.top_n]

    results = [
        RerankResultItem(
            index=item["index"],
            relevance_score=item["score"],
            document=documents[item["index"]] if request.return_documents else None,
        )
        for item in items
    ]
    return RerankAdapterResponse(results=results)
