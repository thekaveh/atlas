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

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class CorpusPathError(ValueError):
    """A mount corpus path that escapes the read-only corpus root."""


class CorpusSizeError(ValueError):
    """A corpus file or aggregate exceeds the configured memory boundary."""


@dataclass
class CorpusFile:
    name: str  # stable identifier used for provenance + object keys
    content: bytes
    content_type: Optional[str] = None


# ─── corpus discovery ────────────────────────────────────────────────

def _corpus_root() -> Path:
    return Path(os.getenv("RAG_INGESTION_CORPUS_ROOT", "/app/corpus")).resolve()


def _size_limit(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise CorpusSizeError(f"{name} must be a positive integer, got {raw!r}") from exc
    if value <= 0:
        raise CorpusSizeError(f"{name} must be a positive integer, got {raw!r}")
    return value


def _corpus_limits() -> tuple[int, int, int]:
    return (
        _size_limit("RAG_INGESTION_MAX_FILE_BYTES", 100 * 1024 * 1024),
        _size_limit("RAG_INGESTION_MAX_CORPUS_BYTES", 1024 * 1024 * 1024),
        _size_limit("RAG_INGESTION_MAX_FILES", 10_000),
    )


def _check_file_count(count: int, max_files: int) -> None:
    if count > max_files:
        raise CorpusSizeError(f"corpus contains more than {max_files} files")


def _check_declared_size(
    name: str,
    size: int | None,
    *,
    total: int,
    max_file_bytes: int,
    max_corpus_bytes: int,
) -> None:
    if size is None:
        return
    if size > max_file_bytes:
        raise CorpusSizeError(
            f"corpus file {name!r} exceeds configured limit of {max_file_bytes} bytes"
        )
    if total + size > max_corpus_bytes:
        raise CorpusSizeError(
            f"corpus exceeds configured limit of {max_corpus_bytes} bytes "
            f"while reading {name!r}"
        )


def _consume_bounded(
    stream: Any,
    name: str,
    *,
    total: int,
    max_file_bytes: int,
    max_corpus_bytes: int,
    on_chunk: Callable[[bytes], None],
) -> int:
    consumed = 0
    while True:
        chunk = stream.read(min(1024 * 1024, max_file_bytes - consumed + 1))
        if not chunk:
            break
        consumed += len(chunk)
        if consumed > max_file_bytes:
            raise CorpusSizeError(
                f"corpus file {name!r} exceeds configured limit of "
                f"{max_file_bytes} bytes"
            )
        if total + consumed > max_corpus_bytes:
            raise CorpusSizeError(
                f"corpus exceeds configured limit of {max_corpus_bytes} bytes "
                f"while reading {name!r}"
            )
        on_chunk(chunk)
    return consumed


def _read_bounded(
    stream: Any,
    name: str,
    *,
    total: int,
    max_file_bytes: int,
    max_corpus_bytes: int,
) -> bytes:
    content = bytearray()
    _consume_bounded(
        stream,
        name,
        total=total,
        max_file_bytes=max_file_bytes,
        max_corpus_bytes=max_corpus_bytes,
        on_chunk=content.extend,
    )
    return bytes(content)


class MountCorpusReader:
    """Reads a consumer-mounted read-only directory. The resolved path MUST stay
    within the corpus root — the security boundary against arbitrary host paths."""

    def _validated_paths(
        self, corpus: Dict[str, Any], override_path: Optional[str] = None
    ) -> tuple[Path, List[Path]]:
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
            return root, []
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
        return root, paths

    def discover(self, corpus: Dict[str, Any], override_path: Optional[str] = None) -> List[CorpusFile]:
        root, paths = self._validated_paths(corpus, override_path)
        max_file_bytes, max_corpus_bytes, max_files = _corpus_limits()
        _check_file_count(len(paths), max_files)
        total = 0
        files: List[CorpusFile] = []
        for path in paths:
            name = str(path.relative_to(root))
            _check_declared_size(
                name,
                path.stat().st_size,
                total=total,
                max_file_bytes=max_file_bytes,
                max_corpus_bytes=max_corpus_bytes,
            )
            with path.open("rb") as stream:
                content = _read_bounded(
                    stream,
                    name,
                    total=total,
                    max_file_bytes=max_file_bytes,
                    max_corpus_bytes=max_corpus_bytes,
                )
            total += len(content)
            files.append(CorpusFile(name=name, content=content))
        return files

    def fingerprint(
        self, corpus: Dict[str, Any], override_path: Optional[str] = None
    ) -> str:
        root, paths = self._validated_paths(corpus, override_path)
        max_file_bytes, max_corpus_bytes, max_files = _corpus_limits()
        _check_file_count(len(paths), max_files)
        total = 0
        manifest = []
        for path in paths:
            name = str(path.relative_to(root))
            size = path.stat().st_size
            _check_declared_size(
                name,
                size,
                total=total,
                max_file_bytes=max_file_bytes,
                max_corpus_bytes=max_corpus_bytes,
            )
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                consumed = _consume_bounded(
                    stream,
                    name,
                    total=total,
                    max_file_bytes=max_file_bytes,
                    max_corpus_bytes=max_corpus_bytes,
                    on_chunk=digest.update,
                )
            total += consumed
            manifest.append((name, digest.hexdigest()))
        return hashlib.sha256(
            json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class MinioCorpusReader:
    """Lists + fetches objects under a bucket/prefix via the MinIO SDK (lazy)."""

    def __init__(self) -> None:
        self._endpoint = os.getenv("MINIO_ENDPOINT", "")

    def available(self) -> bool:
        return bool(self._endpoint.strip())

    @staticmethod
    def _credentials(corpus: Dict[str, Any]) -> tuple[str, str]:
        access_var = str(
            corpus.get("access_key_var") or "MINIO_BACKEND_ACCESS_KEY"
        )
        secret_var = str(
            corpus.get("secret_key_var") or "MINIO_BACKEND_SECRET_KEY"
        )
        return os.getenv(access_var, ""), os.getenv(secret_var, "")

    def discover(self, corpus: Dict[str, Any], override_path: Optional[str] = None) -> List[CorpusFile]:
        from minio import Minio  # lazy — keeps main.py import closure minio-free
        from urllib.parse import urlparse

        parsed = urlparse(self._endpoint if "://" in self._endpoint else f"http://{self._endpoint}")
        access_key, secret_key = self._credentials(corpus)
        client = Minio(
            parsed.netloc,
            access_key=access_key,
            secret_key=secret_key,
            secure=parsed.scheme == "https",
        )
        bucket = str(corpus.get("bucket"))
        prefix = str(corpus.get("prefix"))
        max_file_bytes, max_corpus_bytes, max_files = _corpus_limits()
        total = 0
        files: List[CorpusFile] = []
        for count, obj in enumerate(
            client.list_objects(bucket, prefix=prefix, recursive=True), start=1
        ):
            _check_file_count(count, max_files)
            size = getattr(obj, "size", None)
            _check_declared_size(
                obj.object_name,
                size if isinstance(size, int) else None,
                total=total,
                max_file_bytes=max_file_bytes,
                max_corpus_bytes=max_corpus_bytes,
            )
            resp = client.get_object(bucket, obj.object_name)
            try:
                content = _read_bounded(
                    resp,
                    obj.object_name,
                    total=total,
                    max_file_bytes=max_file_bytes,
                    max_corpus_bytes=max_corpus_bytes,
                )
            finally:
                resp.close()
                resp.release_conn()
            total += len(content)
            files.append(CorpusFile(name=obj.object_name, content=content))
        return files

    def fingerprint(
        self, corpus: Dict[str, Any], override_path: Optional[str] = None
    ) -> str:
        from minio import Minio
        from urllib.parse import urlparse

        parsed = urlparse(self._endpoint if "://" in self._endpoint else f"http://{self._endpoint}")
        access_key, secret_key = self._credentials(corpus)
        client = Minio(
            parsed.netloc,
            access_key=access_key,
            secret_key=secret_key,
            secure=parsed.scheme == "https",
        )
        bucket = str(corpus.get("bucket"))
        prefix = str(corpus.get("prefix"))
        max_file_bytes, max_corpus_bytes, max_files = _corpus_limits()
        total = 0
        manifest = []
        for count, obj in enumerate(
            client.list_objects(bucket, prefix=prefix, recursive=True), start=1
        ):
            _check_file_count(count, max_files)
            size = getattr(obj, "size", None)
            declared_size = size if isinstance(size, int) else None
            _check_declared_size(
                obj.object_name,
                declared_size,
                total=total,
                max_file_bytes=max_file_bytes,
                max_corpus_bytes=max_corpus_bytes,
            )
            total += declared_size or 0
            manifest.append(
                (
                    obj.object_name,
                    getattr(obj, "etag", None),
                    size,
                    str(getattr(obj, "last_modified", "")),
                )
            )
        manifest.sort()
        return hashlib.sha256(
            json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


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

    def fingerprint(
        self, corpus: Dict[str, Any], override_path: Optional[str] = None
    ) -> str:
        if corpus.get("source") == "minio":
            return self._minio.fingerprint(corpus, override_path)
        return self._mount.fingerprint(corpus, override_path)


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
                        content=file.content,
                        filename=file.name,
                        content_type=file.content_type,
                        extractor=parser,
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
                    json={
                        "text": doc["text"],
                        "file_source": self._file_source(doc),
                    },
                )
                if resp.status_code == 409:
                    uploaded += 1
                    continue
                resp.raise_for_status()
                uploaded += 1
        return uploaded

    @staticmethod
    def _file_source(document: Dict[str, str]) -> str:
        identity = json.dumps(
            {
                "source": document.get("source", ""),
                "text": document["text"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"atlas-{digest}.txt"

    async def pipeline_busy(self) -> bool:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._endpoint}/documents/pipeline_status", headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()
        return bool(data.get("busy", False))
