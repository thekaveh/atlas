"""Upstream I/O for the RAG ingestion engine (#413).

Each client is a thin, injectable adapter so the orchestrator can be driven with
fakes in tests (no live services). Real implementations use ``httpx`` (matching
the existing ``document_extraction``/``memory_store`` conventions); the MinIO SDK
is imported lazily so ``main.py``'s import closure never requires it.

Capability-gating: every client exposes ``available()`` derived from its
``*_ENDPOINT``/``*_URL`` env var (empty = the service's SOURCE is disabled). The
orchestrator turns that into fail/skip semantics per the profile target.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class CorpusPathError(ValueError):
    """A mount corpus path that escapes the read-only corpus root."""


@dataclass
class CorpusFile:
    name: str  # stable identifier used for provenance + object keys
    content: bytes
    content_type: Optional[str] = None


# ─── corpus discovery ────────────────────────────────────────────────

def _corpus_root() -> Path:
    return Path(os.getenv("RAG_INGESTION_CORPUS_ROOT", "/app/corpus")).resolve()


class MountCorpusReader:
    """Reads a consumer-mounted read-only directory. The resolved path MUST stay
    within the corpus root — the security boundary against arbitrary host paths."""

    def discover(self, corpus: Dict[str, Any], override_path: Optional[str] = None) -> List[CorpusFile]:
        rel = override_path or str(corpus.get("path") or "")
        if rel.startswith("/") or rel.startswith("~") or ".." in Path(rel).parts:
            raise CorpusPathError(
                f"corpus path {rel!r} must be relative and may not contain '..'"
            )
        root = _corpus_root()
        target = (root / rel).resolve()
        # Defense in depth: even after the string checks, confirm containment.
        if root != target and root not in target.parents:
            raise CorpusPathError(f"corpus path {rel!r} escapes the corpus root {root}")
        if not target.exists():
            return []
        files: List[CorpusFile] = []
        paths = [target] if target.is_file() else sorted(
            p for p in target.rglob("*") if p.is_file()
        )
        for path in paths:
            # Re-verify containment on the RESOLVED real path of every discovered
            # file: rglob + read_bytes follow symlinks, so a symlink planted inside
            # a consumer-controlled mount (e.g. ``docs/leak -> /app/.env``) would
            # otherwise escape the corpus root and exfiltrate host/container files.
            # The top-level check above only covers the declared directory.
            resolved = path.resolve()
            if root != resolved and root not in resolved.parents:
                raise CorpusPathError(
                    f"corpus file {path.relative_to(root)!s} resolves outside the corpus "
                    f"root {root} (symlink escape) — refusing to ingest"
                )
            files.append(CorpusFile(name=str(path.relative_to(root)), content=path.read_bytes()))
        return files


class MinioCorpusReader:
    """Lists + fetches objects under a bucket/prefix via the MinIO SDK (lazy)."""

    def __init__(self) -> None:
        self._endpoint = os.getenv("MINIO_ENDPOINT", "")

    def available(self) -> bool:
        return bool(self._endpoint.strip())

    def discover(self, corpus: Dict[str, Any], override_path: Optional[str] = None) -> List[CorpusFile]:
        from minio import Minio  # lazy — keeps main.py import closure minio-free
        from urllib.parse import urlparse

        parsed = urlparse(self._endpoint if "://" in self._endpoint else f"http://{self._endpoint}")
        client = Minio(
            parsed.netloc,
            access_key=os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY", "")),
            secret_key=os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY", "")),
            secure=parsed.scheme == "https",
        )
        bucket = str(corpus.get("bucket"))
        prefix = str(corpus.get("prefix"))
        files: List[CorpusFile] = []
        for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
            resp = client.get_object(bucket, obj.object_name)
            try:
                files.append(CorpusFile(name=obj.object_name, content=resp.read()))
            finally:
                resp.close()
                resp.release_conn()
        return files


class CorpusReader:
    """Dispatches to the mount or minio reader by ``corpus.source``."""

    def __init__(self, mount: Optional[MountCorpusReader] = None, minio: Optional[MinioCorpusReader] = None) -> None:
        self._mount = mount or MountCorpusReader()
        self._minio = minio or MinioCorpusReader()

    def discover(self, corpus: Dict[str, Any], override_path: Optional[str] = None) -> List[CorpusFile]:
        source = corpus.get("source")
        if source == "minio":
            return self._minio.discover(corpus, override_path)
        return self._mount.discover(corpus, override_path)


# ─── parsing ─────────────────────────────────────────────────────────

@dataclass
class ParsedDocument:
    name: str
    text: str
    parser: str


class ParserError(RuntimeError):
    def __init__(self, message: str, *, service: Optional[str] = None, http_status: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.service = service
        self.http_status = http_status
        self.body = body


class ParserAdapter:
    """Selects the first parser in ``parser_order`` that succeeds. ``plain_text``
    is always available (decode bytes); ``docling``/``tika`` route through the
    existing DocumentExtractor and are skipped when their endpoint is unset."""

    def __init__(self, extractor: Any = None) -> None:
        self._extractor = extractor  # a DocumentExtractor or a fake; None → lazy

    def _get_extractor(self) -> Any:
        if self._extractor is None:
            from document_extraction import DocumentExtractor

            self._extractor = DocumentExtractor()
        return self._extractor

    async def parse(self, file: CorpusFile, parser_order: List[str]) -> ParsedDocument:
        last_error: Optional[Exception] = None
        for parser in parser_order:
            try:
                if parser == "plain_text":
                    return ParsedDocument(
                        name=file.name,
                        text=file.content.decode("utf-8", errors="replace"),
                        parser="plain_text",
                    )
                if parser in ("docling", "tika"):
                    extractor = self._get_extractor()
                    result = await extractor.extract(
                        file.content, filename=file.name, content_type=file.content_type
                    )
                    text = getattr(result, "content", None)
                    if text is None and isinstance(result, dict):
                        text = result.get("content")
                    if text:
                        return ParsedDocument(name=file.name, text=str(text), parser=parser)
                    last_error = ParserError(f"{parser} returned empty content", service=parser)
                    continue
                # crawl4ai and any future parser: not wired yet → fall through.
                last_error = ParserError(f"parser {parser!r} not available", service=parser)
            except Exception as exc:  # noqa: BLE001 - try the next parser
                last_error = exc
                continue
        raise ParserError(
            f"no parser in {parser_order} could extract {file.name!r}: {last_error}"
        )


# ─── embedding ───────────────────────────────────────────────────────

class Embedder:
    """Client-side embeddings via the LiteLLM OpenAI-compatible endpoint."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self._base_url = (base_url if base_url is not None else os.getenv("LITELLM_BASE_URL", "")).rstrip("/")
        self._api_key = api_key if api_key is not None else os.getenv("LITELLM_API_KEY", "")
        self._model = model or os.getenv("LITELLM_EMBEDDING_MODEL", "ollama/nomic-embed-text")

    def available(self) -> bool:
        return bool(self._base_url.strip())

    async def embed(self, texts: List[str]) -> List[List[float]]:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/embeddings",
                headers=headers,
                json={"model": self._model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        return [row["embedding"] for row in data.get("data", [])]


# ─── vector store (Weaviate) ─────────────────────────────────────────

class WeaviateClient:
    """Idempotent class creation + object writes, mirroring memory_store's REST
    shapes. Vectors are supplied client-side (class vectorizer = ``none``)."""

    def __init__(self, url: Optional[str] = None) -> None:
        self._url = (url if url is not None else os.getenv("WEAVIATE_URL", "")).rstrip("/")

    def available(self) -> bool:
        return bool(self._url.strip())

    async def ensure_class(self, class_name: str) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            existing = await client.get(f"{self._url}/v1/schema/{class_name}")
            if existing.status_code == 200:
                return
            resp = await client.post(
                f"{self._url}/v1/schema",
                json={
                    "class": class_name,
                    "vectorizer": "none",
                    "properties": [
                        {"name": "content", "dataType": ["text"]},
                        {"name": "source", "dataType": ["text"]},
                        {"name": "profile", "dataType": ["text"]},
                        {"name": "chunkIndex", "dataType": ["int"]},
                    ],
                },
            )
            # 422 with "already exists" is a benign race, not a failure.
            if resp.status_code not in (200, 422):
                resp.raise_for_status()

    async def write_objects(self, class_name: str, objects: List[Dict[str, Any]]) -> int:
        import httpx

        written = 0
        async with httpx.AsyncClient(timeout=60.0) as client:
            for obj in objects:
                resp = await client.post(
                    f"{self._url}/v1/objects",
                    json={
                        "class": class_name,
                        "id": obj["id"],  # deterministic uuid → idempotent upsert
                        "properties": obj["properties"],
                        "vector": obj["vector"],
                    },
                )
                if resp.status_code in (200, 201):
                    written += 1
                elif resp.status_code == 422:
                    # Object already exists (idempotent re-run) — count it.
                    written += 1
                else:
                    resp.raise_for_status()
        return written


# ─── graph RAG (LightRAG) ────────────────────────────────────────────

class LightRagClient:
    """Upload documents + drain the extraction pipeline with a timeout.

    Endpoint paths follow the LightRAG server API. A live round-trip is an
    OPTIONAL live test; the upload loop, drain-poll-with-timeout, and idempotency
    are what the unit suite exercises with a fake.
    """

    def __init__(self, endpoint: Optional[str] = None, api_key: Optional[str] = None) -> None:
        self._endpoint = (endpoint if endpoint is not None else os.getenv("LIGHTRAG_ENDPOINT", "")).rstrip("/")
        self._api_key = api_key if api_key is not None else os.getenv("LIGHTRAG_API_KEY", "")

    def available(self) -> bool:
        return bool(self._endpoint.strip())

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    async def upload(self, documents: List[Dict[str, str]]) -> int:
        import httpx

        uploaded = 0
        async with httpx.AsyncClient(timeout=120.0) as client:
            for doc in documents:
                resp = await client.post(
                    f"{self._endpoint}/documents/text",
                    headers=self._headers(),
                    json={"text": doc["text"], "file_source": doc.get("source", "")},
                )
                resp.raise_for_status()
                uploaded += 1
        return uploaded

    async def pipeline_busy(self) -> bool:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._endpoint}/documents/pipeline_status", headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()
        return bool(data.get("busy", False))
