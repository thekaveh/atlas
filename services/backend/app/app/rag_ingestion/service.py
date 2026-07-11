"""RAG ingestion service + phase orchestrator (#413).

``submit`` resolves a declared profile, computes the idempotency key
(consumer + profile + revision + corpus digest), dedups against the store, and
creates a durable record. ``run`` drives the ordered phase state machine
(discover → parse → chunk → embed → vector_write → lightrag_upload → drain →
finalize), recording status/counts/timing and actionable per-file errors, honoring
per-target fail/skip capability semantics and cooperative cancellation.

The orchestrator is async (it reuses the async DocumentExtractor + httpx clients);
the Celery task and tests drive it via ``asyncio.run`` so no event loop or
``pytest-asyncio`` is required.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .clients import (
    CorpusFile,
    CorpusReader,
    Embedder,
    LightRagClient,
    ParsedDocument,
    ParserAdapter,
    ParserError,
    WeaviateClient,
)
from .models import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    IngestionError,
    IngestionRecord,
)
from .profiles import LoadedProfile, ProfileNotFoundError, get_profile
from .store import IngestionStore, default_store


class PhaseFatal(RuntimeError):
    """A phase failure that aborts the remaining phases and fails the job (a
    capability ``on_unavailable: fail`` target, or a drain timeout)."""

    def __init__(self, error: IngestionError):
        super().__init__(error.message)
        self.error = error


class IngestionCancelled(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Deps:
    """Injectable upstream clients. Defaults read env; tests pass fakes."""

    def __init__(
        self,
        corpus: Optional[CorpusReader] = None,
        parser: Optional[ParserAdapter] = None,
        embedder: Optional[Embedder] = None,
        weaviate: Optional[WeaviateClient] = None,
        lightrag: Optional[LightRagClient] = None,
        poll_interval: float = 2.0,
    ) -> None:
        self.corpus = corpus or CorpusReader()
        self.parser = parser or ParserAdapter()
        self.embedder = embedder or Embedder()
        self.weaviate = weaviate or WeaviateClient()
        self.lightrag = lightrag or LightRagClient()
        self.poll_interval = poll_interval


class RagIngestionService:
    def __init__(self, store: Optional[IngestionStore] = None, deps: Optional[Deps] = None, profiles_path: Optional[str] = None) -> None:
        self.store = store or default_store()
        self.deps = deps or Deps()
        self._profiles_path = profiles_path

    # ── submit ───────────────────────────────────────────────────────
    def _resolve_profile(self, name: str) -> LoadedProfile:
        return get_profile(name, self._profiles_path)

    @staticmethod
    def _idempotency_key(profile: LoadedProfile, corpus: Dict[str, Any]) -> str:
        payload = "|".join(
            [
                profile.consumer,
                profile.name,
                profile.revision,
                json.dumps(corpus, sort_keys=True, separators=(",", ":")),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def submit(self, profile_name: str, corpus_path: Optional[str] = None) -> tuple[IngestionRecord, bool]:
        """Create (or dedup to) an ingestion record. Does NOT run it — the caller
        dispatches the Celery task or runs it synchronously. Returns
        ``(record, created)`` where ``created`` is False on an idempotent dedup hit
        (so the caller must NOT dispatch a second run)."""
        profile = self._resolve_profile(profile_name)  # raises ProfileNotFoundError
        corpus = dict(profile.corpus)
        if corpus_path is not None:
            if corpus.get("source") != "mount":
                raise ValueError("corpus_path override is only valid for source=mount profiles")
            corpus = {**corpus, "path": corpus_path}
        key = self._idempotency_key(profile, corpus)

        existing = self.store.find_by_idempotency_key(key)
        if existing is not None and existing.is_dedup_candidate:
            return existing, False  # idempotent: identical corpus+profile → no re-run

        record = IngestionRecord(
            id=str(uuid.uuid4()),
            consumer=profile.consumer,
            profile=profile.name,
            revision=profile.revision,
            idempotency_key=key,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        # Seed the phase list so an observer sees the full lifecycle immediately.
        from .models import PHASES, PhaseRecord

        record.phases = [PhaseRecord(name=name) for name in PHASES]
        record.counts = {
            "files_discovered": 0,
            "documents_parsed": 0,
            "chunks": 0,
            "vectors_written": 0,
            "documents_uploaded": 0,
        }
        self.store.save(record)
        return record, True

    # ── run (orchestrate) ────────────────────────────────────────────
    def _refresh_cancel(self, record: IngestionRecord) -> None:
        latest = self.store.get(record.id)
        if latest is not None and latest.cancel_requested:
            record.cancel_requested = True

    def _persist(self, record: IngestionRecord) -> None:
        record.updated_at = _now_iso()
        self.store.save(record)

    async def run(self, ingestion_id: str) -> IngestionRecord:
        record = self.store.get(ingestion_id)
        if record is None:
            raise KeyError(ingestion_id)
        if record.is_terminal:
            return record
        profile = self._resolve_profile(record.profile)
        corpus = dict(profile.corpus)

        record.status = STATUS_RUNNING
        self._persist(record)

        state: Dict[str, Any] = {"files": [], "docs": [], "chunks": [], "vectors": []}
        try:
            for name in (
                "discover", "parse", "chunk", "embed",
                "vector_write", "lightrag_upload", "drain", "finalize",
            ):
                self._refresh_cancel(record)
                if record.cancel_requested:
                    record.status = STATUS_CANCELLED
                    self._persist(record)
                    return record
                await self._run_phase(name, record, profile, corpus, state)
                self._persist(record)
        except IngestionCancelled:
            record.status = STATUS_CANCELLED
            self._persist(record)
            return record
        except PhaseFatal as fatal:
            record.add_error(fatal.error)
            self._mark_failed_phase(record, fatal.error.phase, fatal.error)
            record.status = STATUS_FAILED
            self._persist(record)
            return record
        except Exception as exc:  # noqa: BLE001 - any unexpected phase error is a
            # recorded, actionable job failure — never a crashed worker.
            running = next(
                (p.name for p in record.phases if p.status == STATUS_RUNNING), "finalize"
            )
            error = IngestionError(phase=running, message=f"unexpected error: {exc}")
            record.add_error(error)
            self._mark_failed_phase(record, running, error)
            record.status = STATUS_FAILED
            self._persist(record)
            return record

        record.status = STATUS_FAILED if record.errors and self._has_fatal_phase(record) else STATUS_COMPLETED
        self._persist(record)
        return record

    def _has_fatal_phase(self, record: IngestionRecord) -> bool:
        return any(p.status == STATUS_FAILED for p in record.phases)

    def _mark_failed_phase(self, record: IngestionRecord, name: str, error: IngestionError) -> None:
        phase = record.phase(name)
        phase.status = STATUS_FAILED
        phase.error = {"message": error.message, "service": error.service, "http_status": error.http_status}

    async def _run_phase(self, name: str, record: IngestionRecord, profile: LoadedProfile, corpus: Dict[str, Any], state: Dict[str, Any]) -> None:
        phase = record.phase(name)
        phase.status = STATUS_RUNNING
        phase.started_at = _now_iso()
        start = time.monotonic()
        try:
            handler = getattr(self, f"_phase_{name}")
            await handler(record, profile, corpus, state)
            if phase.status == STATUS_RUNNING:  # not set to skipped by the handler
                phase.status = STATUS_COMPLETED
        finally:
            phase.ended_at = _now_iso()
            phase.duration_ms = int((time.monotonic() - start) * 1000)

    # ── phases ───────────────────────────────────────────────────────
    async def _phase_discover(self, record, profile, corpus, state):
        files: List[CorpusFile] = self.deps.corpus.discover(corpus, corpus.get("path"))
        state["files"] = files
        record.counts["files_discovered"] = len(files)
        record.phase("discover").counts = {"files": len(files)}
        # Content hash of the discovered manifest (provenance).
        manifest = sorted((f.name, len(f.content)) for f in files)
        record.content_digest = hashlib.sha256(
            json.dumps(manifest).encode("utf-8")
        ).hexdigest()[:16]

    async def _phase_parse(self, record, profile, corpus, state):
        docs: List[ParsedDocument] = []
        parsed_ok = 0
        for file in state["files"]:
            try:
                doc = await self.deps.parser.parse(file, list(profile.parser_order))
                docs.append(doc)
                parsed_ok += 1
            except ParserError as exc:
                # Per-file failure is recorded + isolated; other files still parse.
                record.add_error(
                    IngestionError(
                        phase="parse", file=file.name, message=str(exc),
                        service=getattr(exc, "service", None),
                        http_status=getattr(exc, "http_status", None),
                        body=getattr(exc, "body", None),
                    )
                )
        state["docs"] = docs
        record.counts["documents_parsed"] = parsed_ok
        record.phase("parse").counts = {"parsed": parsed_ok, "failed": len(state["files"]) - parsed_ok}

    async def _phase_chunk(self, record, profile, corpus, state):
        from chunking_service import ChunkRequest, chunk_text

        chunker = profile.chunker or {}
        strategy = chunker.get("strategy", "recursive")
        chunk_size = int(chunker.get("chunk_size", 512))
        overlap = int(chunker.get("overlap", 64))
        chunks: List[Dict[str, Any]] = []
        for doc in state["docs"]:
            if not doc.text.strip():
                continue
            resp = chunk_text(
                ChunkRequest(text=doc.text, strategy=strategy, chunk_size=chunk_size, overlap=overlap)
            )
            for tc in resp.chunks:
                chunks.append({"source": doc.name, "index": tc.index, "content": tc.content})
        state["chunks"] = chunks
        record.counts["chunks"] = len(chunks)
        record.phase("chunk").counts = {"chunks": len(chunks)}

    async def _phase_embed(self, record, profile, corpus, state):
        if not profile.vector_targets:
            record.phase("embed").status = "skipped"
            record.phase("embed").note = "no vector_targets"
            return
        if not state["chunks"]:
            record.phase("embed").note = "no chunks to embed"
            return
        if not self.deps.embedder.available():
            # Embeddings feed the weaviate write; defer fail/skip to vector_write's
            # target policy rather than failing here.
            record.phase("embed").status = "skipped"
            record.phase("embed").note = "embedder unavailable (LITELLM_BASE_URL unset)"
            return
        texts = [c["content"] for c in state["chunks"]]
        vectors = await self.deps.embedder.embed(texts)
        for chunk, vector in zip(state["chunks"], vectors):
            chunk["vector"] = vector
        state["vectors"] = vectors
        record.phase("embed").counts = {"vectors": len(vectors)}

    def _target_unavailable(self, record: IngestionRecord, phase: str, backend: str, service: str, on_unavailable: str, detail: Optional[str] = None) -> None:
        message = detail or f"{backend} target requested but {service} is disabled/unreachable"
        error = IngestionError(phase=phase, service=service, message=message)
        if on_unavailable == "fail":
            raise PhaseFatal(error)
        # skip: a skipped target is visible (phase status + note) but is NOT a job
        # failure, so it does not land in errors[] (which is reserved for real
        # failures). This is the "define fail/skip rather than silently degrade" AC.
        p = record.phase(phase)
        p.status = "skipped"
        p.note = f"{backend} skipped (on_unavailable=skip): {service} disabled"

    async def _phase_vector_write(self, record, profile, corpus, state):
        targets = profile.vector_targets
        if not targets:
            record.phase("vector_write").status = "skipped"
            record.phase("vector_write").note = "no vector_targets"
            return
        if not state["chunks"]:
            record.phase("vector_write").note = "no chunks to write"
            record.counts["vectors_written"] = 0
            return
        total = 0
        for target in targets:
            embedded = all("vector" in c for c in state["chunks"])
            if not self.deps.weaviate.available():
                self._target_unavailable(
                    record, "vector_write", "weaviate", "weaviate",
                    target.get("on_unavailable", "fail"),
                    detail="weaviate target requested but weaviate is disabled/unreachable",
                )
                return
            if not embedded:
                # Vectors were never produced — the embedder (LiteLLM), not
                # Weaviate, is the disabled dependency. Attribute it correctly so
                # the operator investigates the right service.
                self._target_unavailable(
                    record, "vector_write", "weaviate", "embedder",
                    target.get("on_unavailable", "fail"),
                    detail="no embeddings produced — the embedder (LITELLM_BASE_URL) is disabled",
                )
                return
            class_name = f"{target['collection_prefix']}_{profile.name}"
            try:
                await self.deps.weaviate.ensure_class(class_name)
                objects = [
                    {
                        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{class_name}|{c['source']}|{c['index']}")),
                        "properties": {
                            "content": c["content"], "source": c["source"],
                            "profile": profile.name, "chunkIndex": c["index"],
                        },
                        "vector": c["vector"],
                    }
                    for c in state["chunks"]
                ]
                total += await self.deps.weaviate.write_objects(class_name, objects)
            except PhaseFatal:
                raise
            except Exception as exc:  # noqa: BLE001 - upstream write failure is fatal
                raise PhaseFatal(
                    IngestionError(
                        phase="vector_write", service="weaviate", message=str(exc),
                        http_status=getattr(getattr(exc, "response", None), "status_code", None),
                    )
                )
        record.counts["vectors_written"] = total
        record.phase("vector_write").counts = {"written": total}

    async def _phase_lightrag_upload(self, record, profile, corpus, state):
        targets = profile.graph_targets
        if not targets:
            record.phase("lightrag_upload").status = "skipped"
            record.phase("lightrag_upload").note = "no graph_targets"
            return
        uploaded = 0
        for target in targets:
            if not self.deps.lightrag.available():
                self._target_unavailable(
                    record, "lightrag_upload", "lightrag", "lightrag",
                    target.get("on_unavailable", "skip"),
                )
                return
            docs = [{"text": d.text, "source": d.name} for d in state["docs"] if d.text.strip()]
            try:
                uploaded += await self.deps.lightrag.upload(docs)
            except Exception as exc:  # noqa: BLE001
                raise PhaseFatal(
                    IngestionError(
                        phase="lightrag_upload", service="lightrag", message=str(exc),
                        http_status=getattr(getattr(exc, "response", None), "status_code", None),
                    )
                )
        record.counts["documents_uploaded"] = uploaded
        record.phase("lightrag_upload").counts = {"uploaded": uploaded}

    async def _phase_drain(self, record, profile, corpus, state):
        targets = [t for t in profile.graph_targets if t.get("wait_for_extraction", True)]
        if not targets or not self.deps.lightrag.available():
            record.phase("drain").status = "skipped"
            record.phase("drain").note = "no drainable graph target"
            return
        import asyncio

        for target in targets:
            timeout = int(target.get("timeout_seconds", 3600))
            deadline = time.monotonic() + timeout
            while True:
                if not await self.deps.lightrag.pipeline_busy():
                    break
                if time.monotonic() >= deadline:
                    raise PhaseFatal(
                        IngestionError(
                            phase="drain", service="lightrag",
                            message=f"extraction did not drain within {timeout}s",
                        )
                    )
                self._refresh_cancel(record)
                if record.cancel_requested:
                    raise IngestionCancelled()
                await asyncio.sleep(self.deps.poll_interval)
        record.phase("drain").note = "extraction drained"

    async def _phase_finalize(self, record, profile, corpus, state):
        record.phase("finalize").counts = dict(record.counts)
