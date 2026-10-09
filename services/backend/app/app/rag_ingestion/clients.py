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
import math
import codecs
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


def _bounded_walk(target: Path, max_files: int) -> List[Path]:
    """Sorted files under ``target``; stops at the first file past the limit
    instead of listing (and later resolving) an arbitrarily large tree."""
    found: List[Path] = []
    for path in target.rglob("*"):
        if path.is_file():
            found.append(path)
            _check_file_count(len(found), max_files)
    return sorted(found)


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
            # Not an empty corpus: reconciling against zero files deletes
            # every vector of the profile. A typo'd override or a corpus
            # mounted into backend but not celery-worker lands here.
            raise CorpusPathError(
                f"corpus path {rel!r} does not exist under {root} "
                "(is the corpus mounted into this container?)"
            )
        paths = [target] if target.is_file() else _bounded_walk(target, _corpus_limits()[2])
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


# Byte-order marks of the encodings decoded as text despite their NUL bytes.
_TEXT_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
)
# Above this share of control characters (other than whitespace) the decoded
# bytes are not text.
_MAX_CONTROL_RATIO = 0.01


def _decode_text(content: bytes) -> Optional[str]:
    for bom, encoding in _TEXT_BOMS:
        if content.startswith(bom):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                return None
    if b"\x00" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        # Windows-1252 text (curly quotes, accented letters) is common in
        # exported corpora; it ingested before the binary check existed.
        return content.decode("cp1252", errors="replace")


def _plain_text(file: CorpusFile) -> str:
    """Decode a text file; refuse binary content.

    ``plain_text`` is the last entry of every parser order. Decoding any
    bytes with ``errors="replace"`` meant a Docling/Tika failure on a PDF
    silently indexed the PDF's raw bytes as text and reported success,
    instead of preserving the source for the next run.
    """
    text = _decode_text(file.content)
    controls = sum(1 for ch in text or "" if ord(ch) < 32 and ch not in "\t\n\r\f\v")
    if text is None or (text and controls / len(text) > _MAX_CONTROL_RATIO):
        raise ParserError(
            f"{file.name!r} is not text; plain_text cannot extract it", service="plain_text"
        )
    return text


class ParserAdapter:
    """Selects the first parser in ``parser_order`` that succeeds. ``plain_text``
    is always available (decode bytes); ``docling``/``tika`` route through the
    existing DocumentExtractor and are skipped when their endpoint is unset."""

    def __init__(self, extractor: Any = None) -> None:
        self._extractor = extractor  # a DocumentExtractor or a fake; None → lazy

    def _get_extractor(self) -> Any:
        if self._extractor is None:
            from dataclasses import replace

            from document_extraction import DocumentExtractor, DocumentExtractorConfig

            # Background ingestion can wait longer for a busy Docling than the
            # HTTP route before falling back to the next parser.
            config = replace(DocumentExtractorConfig.from_env(), docling_busy_wait_seconds=120.0)
            self._extractor = DocumentExtractor(config)
        return self._extractor

    async def parse(self, file: CorpusFile, parser_order: List[str]) -> ParsedDocument:
        # Every parser's reason, not only the last one: a Docling/Tika size
        # limit used to surface as plain_text's "not text".
        errors: List[str] = []
        for parser in parser_order:
            try:
                if parser == "plain_text":
                    return ParsedDocument(
                        name=file.name, text=_plain_text(file), parser="plain_text",
                    )
                if parser in ("docling", "tika"):
                    extractor = self._get_extractor()
                    result = await extractor.extract(
                        content=file.content,
                        filename=file.name,
                        content_type=file.content_type,
                        extractor=parser,
                        chunking=False,  # re-chunked by Chonkie below
                    )
                    text = getattr(result, "content", None)
                    if text is None and isinstance(result, dict):
                        text = result.get("content")
                    if text:
                        return ParsedDocument(name=file.name, text=str(text), parser=parser)
                    errors.append(f"{parser}: returned empty content")
                    continue
                # crawl4ai and any future parser: not wired yet → fall through.
                errors.append(f"{parser}: not available")
            except Exception as exc:  # noqa: BLE001 - try the next parser
                errors.append(f"{parser}: {exc}")
                continue
        raise ParserError(
            f"no parser in {parser_order} could extract {file.name!r}: {'; '.join(errors)}"
        )


# ─── embedding ───────────────────────────────────────────────────────

class EmbedderUnavailable(ConnectionError):
    """A 5xx, 408 or 429 from the embedding endpoint: it clears by itself
    (a model loading, a rate limit), so it is retried like a connection
    error. As a plain HTTPStatusError the job failed on its first attempt
    (2026-10-08 run, cycle 50)."""


def _raise_for_embedding_status(resp) -> None:
    status = resp.status_code
    if status >= 500 or status in (408, 429):
        raise EmbedderUnavailable(f"embedding endpoint answered HTTP {status}")
    resp.raise_for_status()


class Embedder:
    """Client-side embeddings via the LiteLLM OpenAI-compatible endpoint."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self._base_url = (base_url if base_url is not None else os.getenv("LITELLM_BASE_URL", "")).rstrip("/")
        self._api_key = api_key if api_key is not None else os.getenv("LITELLM_API_KEY", "")
        self._model = model or os.getenv("LITELLM_EMBEDDING_MODEL", "ollama/nomic-embed-text")

    def available(self) -> bool:
        return bool(self._base_url.strip())

    @property
    def model(self) -> str:
        return self._model

    # One request per batch: a whole corpus in one call outran the 60s
    # timeout on CPU embedders and was retried in full each time.
    _BATCH_SIZE = 128

    async def embed(self, texts: List[str]) -> List[List[float]]:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        vectors: List[List[float]] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for start in range(0, len(texts), self._BATCH_SIZE):
                batch = texts[start:start + self._BATCH_SIZE]
                resp = await client.post(
                    f"{self._base_url}/embeddings",
                    headers=headers,
                    json={"model": self._model, "input": batch},
                )
                _raise_for_embedding_status(resp)
                rows = resp.json().get("data", [])
                if len(rows) != len(batch):
                    # zip() downstream would silently drop the missing chunks.
                    raise RuntimeError(
                        f"embedding endpoint returned {len(rows)} vectors "
                        f"for {len(batch)} inputs"
                    )
                # OpenAI-compatible rows carry their input position.
                rows = sorted(rows, key=lambda row: row.get("index", 0))
                vectors.extend(row["embedding"] for row in rows)
        return vectors


# ─── vector store (Weaviate) ─────────────────────────────────────────

class WeaviateClient:
    """Idempotent class creation + object writes, mirroring memory_store's REST
    shapes. Vectors are supplied client-side (class vectorizer = ``none``)."""

    def __init__(self, url: Optional[str] = None) -> None:
        self._url = (url if url is not None else os.getenv("WEAVIATE_URL", "")).rstrip("/")

    def available(self) -> bool:
        return bool(self._url.strip())

    @staticmethod
    def embedding_identity(model: str, dimension: int) -> str:
        """Class ``description`` naming the vectors it holds (#1364)."""
        return f"atlas-rag-ingestion embedding_model={model} dimension={dimension}"

    async def _adopt_legacy_class(self, client, schema: dict, dimension: int) -> bool:
        """Record the embedding identity on a class created before Atlas
        recorded one, when its vectors already have this run's size (or it
        has none). Returns False when the sizes differ and it must be rebuilt.
        Atlas cannot tell which model produced same-size legacy vectors."""
        class_name = schema["class"]
        sample = await client.get(
            f"{self._url}/v1/objects", params={"class": class_name, "limit": 1, "include": "vector"}
        )
        sample.raise_for_status()
        objects = sample.json().get("objects") or []
        if objects and len(objects[0].get("vector") or []) != dimension:
            return False
        updated = await client.put(f"{self._url}/v1/schema/{class_name}", json=schema)
        updated.raise_for_status()
        return True

    async def _keep_existing_class(
        self, client, schema: dict, embedding: Optional[tuple[str, int]]
    ) -> bool:
        """True to reuse the class as is; otherwise it has been dropped."""
        class_name = schema["class"]
        description = self.embedding_identity(*embedding) if embedding else None
        if description is None or schema.get("description") == description:
            return True
        if not schema.get("description") and await self._adopt_legacy_class(
            client, {**schema, "description": description}, embedding[1]
        ):
            return True
        dropped = await client.delete(f"{self._url}/v1/schema/{class_name}")
        if dropped.status_code not in (200, 404):
            dropped.raise_for_status()
        return False

    async def ensure_class(
        self, class_name: str, embedding: Optional[tuple[str, int]] = None
    ) -> bool:
        """Create ``class_name`` if absent. With ``embedding`` (model,
        dimension), a class recorded for other vectors (or created before the
        identity was recorded) is dropped and recreated: appending vectors of
        another model or size failed or mixed incompatible spaces (#1364). A
        legacy class whose vectors match this run's size is adopted instead.
        Returns True when an existing class was dropped: its objects, including
        those of sources that fail this run, are gone."""
        import httpx

        description = self.embedding_identity(*embedding) if embedding else None
        async with httpx.AsyncClient(timeout=30.0) as client:
            existing = await client.get(f"{self._url}/v1/schema/{class_name}")
            if existing.status_code == 200 and await self._keep_existing_class(
                client, {"class": class_name, **existing.json()}, embedding
            ):
                return False
            resp = await client.post(
                f"{self._url}/v1/schema",
                json={
                    "class": class_name,
                    **({"description": description} if description else {}),
                    "vectorizer": "none",
                    "properties": [
                        {"name": "content", "dataType": ["text"]},
                        {"name": "source", "dataType": ["text"]},
                        {"name": "profile", "dataType": ["text"]},
                        {"name": "chunkIndex", "dataType": ["int"]},
                    ],
                },
            )
            if resp.status_code == 422:
                # Creation can race another worker, but 422 also represents
                # malformed schemas. Re-read to distinguish those outcomes.
                confirmed = await client.get(
                    f"{self._url}/v1/schema/{class_name}"
                )
                if confirmed.status_code != 200:
                    resp.raise_for_status()
            elif resp.status_code != 200:
                resp.raise_for_status()
        return existing.status_code == 200

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
                    # Weaviate uses 422 for duplicate IDs and invalid payloads.
                    # Existence permits a full replacement; the replacement
                    # still validates current properties and vector data.
                    existing = await client.head(
                        f"{self._url}/v1/objects/{class_name}/{obj['id']}"
                    )
                    if existing.status_code in (200, 204):
                        replacement = await client.put(
                            f"{self._url}/v1/objects/{class_name}/{obj['id']}",
                            json={
                                "class": class_name,
                                "properties": obj["properties"],
                                "vector": obj["vector"],
                            },
                        )
                        replacement.raise_for_status()
                        written += 1
                    else:
                        resp.raise_for_status()
                else:
                    resp.raise_for_status()
        return written

    async def _fetch_reconcilable_ids(
        self, client, class_name: str, profile_name: str, keep_sources: set
    ) -> set:
        """Object ids for this profile, EXCLUDING preserved sources.

        A preserved source is one this run could not process; its objects are
        absent from `desired_ids` for a reason that is not staleness.

        Pages with Weaviate's `after` cursor: `offset` paging fails once
        offset + limit passes QUERY_MAXIMUM_RESULTS (default 10,000), which
        failed every ingestion of a profile past 10k chunks. The cursor API
        rejects `where`, so the profile filter runs here.
        """
        existing_ids = set()
        page_size = 1000
        after = None
        while True:
            cursor = f', after: "{after}"' if after else ""
            response = await client.post(
                f"{self._url}/v1/graphql",
                json={
                    "query": f"""{{
                        Get {{
                            {class_name}(limit: {page_size}{cursor}) {{
                                profile source _additional {{ id }}
                            }}
                        }}
                    }}"""
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(
                    "Weaviate profile reconciliation lookup failed"
                )
            page = (
                payload.get("data", {})
                .get("Get", {})
                .get(class_name, [])
            )
            for obj in page:
                object_id = obj.get("_additional", {}).get("id")
                if object_id is None:
                    continue
                after = object_id
                if obj.get("profile") != profile_name:
                    continue
                if obj.get("source") in keep_sources:
                    continue  # this run could not produce it; not stale
                existing_ids.add(object_id)
            if len(page) < page_size:
                break
        return existing_ids

    async def reconcile_objects(
        self,
        class_name: str,
        profile_name: str,
        desired_ids: List[str],
        preserve_sources: Optional[List[str]] = None,
    ) -> int:
        """Delete objects from older corpus generations for this profile.

        `preserve_sources` names documents this run could NOT process. Their
        objects are absent from `desired_ids` for a reason that is not
        staleness, so deleting them would destroy good data on a transient
        parser blip.
        """
        import httpx

        keep_sources = set(preserve_sources or ())
        async with httpx.AsyncClient(timeout=60.0) as client:
            existing_ids = await self._fetch_reconcilable_ids(
                client, class_name, profile_name, keep_sources
            )
            stale_ids = existing_ids - set(desired_ids) - {None}
            for object_id in sorted(stale_ids):
                deleted = await client.delete(
                    f"{self._url}/v1/objects/{class_name}/{object_id}"
                )
                if deleted.status_code not in (200, 204, 404):
                    deleted.raise_for_status()
        return len(stale_ids)


# ─── graph RAG (LightRAG) ────────────────────────────────────────────

_DEFAULT_PIPELINE_STATUS_TIMEOUT = 30.0
_MAX_PIPELINE_STATUS_TIMEOUT = 3600.0


def _validated_pipeline_status_timeout(value: object) -> Optional[float]:
    """Return a supported finite duration, or ``None`` for invalid input."""
    if isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except Exception:
        # Custom numeric objects can raise arbitrary ordinary exceptions from
        # __float__. Keep the guard scoped to coercion; BaseException-derived
        # process-control signals still propagate.
        return None
    if math.isfinite(converted) and 0 < converted <= _MAX_PIPELINE_STATUS_TIMEOUT:
        return converted
    return None


def _resolve_pipeline_status_timeout(explicit: object | None) -> float:
    """Per-request `pipeline_status` timeout: explicit arg > env > default.

    ``LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS`` is the operator knob. A
    missing, blank, malformed, non-finite, non-positive, or over-limit value
    falls back to the 30 s default so a bad override can never disable the
    timeout (which would hang a drain poll).

    Numeric strings remain accepted for compatibility with environment-style
    callers. Booleans and values that cannot be safely coerced are invalid.
    """
    if explicit is not None:
        value = _validated_pipeline_status_timeout(explicit)
        if value is not None:
            return value
    raw = os.getenv("LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS", "").strip()
    if raw:
        value = _validated_pipeline_status_timeout(raw)
        if value is not None:
            return value
    return _DEFAULT_PIPELINE_STATUS_TIMEOUT


class LightRagClient:
    """Upload documents + drain the extraction pipeline with a timeout.

    Endpoint paths follow the LightRAG server API. A live round-trip is an
    OPTIONAL live test; the upload loop, drain-poll-with-timeout, and idempotency
    are what the unit suite exercises with a fake.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        pipeline_status_timeout: Optional[float] = None,
    ) -> None:
        self._endpoint = (endpoint if endpoint is not None else os.getenv("LIGHTRAG_ENDPOINT", "")).rstrip("/")
        self._api_key = api_key if api_key is not None else os.getenv("LIGHTRAG_API_KEY", "")
        # Per-request timeout for the `pipeline_status` drain poll. Kept short
        # and configurable: LightRAG can briefly stop servicing the endpoint
        # during a long extraction/merge, and the drain loop retries these
        # transient timeouts within its own deadline rather than failing a
        # healthy ingestion (#673).
        self._pipeline_status_timeout = _resolve_pipeline_status_timeout(
            pipeline_status_timeout
        )

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

        async with httpx.AsyncClient(timeout=self._pipeline_status_timeout) as client:
            resp = await client.get(
                f"{self._endpoint}/documents/pipeline_status", headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()
        return bool(data.get("busy", False))
