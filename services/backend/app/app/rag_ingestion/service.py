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

import asyncio
from contextlib import suppress
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

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
    STATUS_PENDING,
    STATUS_RUNNING,
    IngestionError,
    IngestionRecord,
)
from .profiles import LoadedProfile, get_profile
from .store import IngestionStore, default_store


logger = logging.getLogger(__name__)


def weaviate_class_name(collection_prefix: str, profile_name: str) -> str:
    """Compose a valid Weaviate class name from a (validated uppercase-first)
    collection_prefix and a profile name.

    Weaviate class names must match ``^[A-Z][_0-9A-Za-z]*$``, but a profile name
    may legitimately contain ``.``/``-`` (e.g. ``showcase-default``). Left raw,
    ``{prefix}_{name}`` would 422 on ensure_class and the case-sensitive
    reconcile ``Get {{ <class> }}`` query would 404. Sanitize every non-alnum
    char in the name to ``_`` so the derived class name is always valid and the
    write + reconcile paths agree.
    """
    safe_name = re.sub(r"[^0-9A-Za-z]", "_", profile_name)
    return f"{collection_prefix}_{safe_name}"


class PhaseFatal(RuntimeError):
    """A phase failure that aborts the remaining phases and fails the job (a
    capability ``on_unavailable: fail`` target, or a drain timeout)."""

    def __init__(self, error: IngestionError):
        super().__init__(error.message)
        self.error = error


class IngestionCancelled(RuntimeError):
    pass


class IngestionExecutionBusy(RuntimeError):
    """Raised when another worker owns the execution lease."""


class IngestionExecutionLeaseLost(RuntimeError):
    """Raised when a worker can no longer persist under its execution lease."""


def _validate_execution_lease_seconds(value: Any) -> int:
    name = "RAG_INGESTION_EXECUTION_LEASE_SECONDS"
    if isinstance(value, bool) or not isinstance(value, int) or not 10 <= value <= 300:
        raise ValueError(f"{name} must be an integer from 10 through 300")
    return value


def ingestion_execution_lease_seconds() -> int:
    name = "RAG_INGESTION_EXECUTION_LEASE_SECONDS"
    try:
        value = int(os.getenv(name, "30"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer from 10 through 300") from exc
    return _validate_execution_lease_seconds(value)


TRANSIENT_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.NetworkError,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_error_details(exc: Exception) -> tuple[Optional[int], Optional[str]]:
    response = getattr(exc, "response", None)
    if response is None:
        return None, None
    status = getattr(response, "status_code", None)
    body = (getattr(response, "text", "") or "")[:500] or None
    return status, body


def _describe_exc(exc: BaseException) -> str:
    """Render an exception with at least its class name.

    Some transport exceptions (notably ``httpx.ReadTimeout``) have an empty
    ``str()``, which would otherwise record a blank, non-actionable error such
    as ``unexpected error:`` (#673). Always prefix the class name so the
    failure is diagnosable even when the message is empty.
    """
    message = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


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
        drain_backoff_base: float = 1.0,
        drain_backoff_max: float = 15.0,
    ) -> None:
        self.corpus = corpus or CorpusReader()
        self.parser = parser or ParserAdapter()
        self.embedder = embedder or Embedder()
        self.weaviate = weaviate or WeaviateClient()
        self.lightrag = lightrag or LightRagClient()
        self.poll_interval = poll_interval
        # Bounded exponential backoff (seconds) applied between consecutive
        # transient `pipeline_status` failures during drain (#673). Steady-state
        # polling of a *reachable* busy pipeline still uses ``poll_interval``.
        self.drain_backoff_base = drain_backoff_base
        self.drain_backoff_max = drain_backoff_max


class RagIngestionService:
    def __init__(self, store: Optional[IngestionStore] = None, deps: Optional[Deps] = None, profiles_path: Optional[str] = None) -> None:
        self.store = store or default_store()
        self.deps = deps or Deps()
        self._profiles_path = profiles_path

    # ── submit ───────────────────────────────────────────────────────
    def _resolve_profile(self, name: str) -> LoadedProfile:
        return get_profile(name, self._profiles_path)

    @staticmethod
    def _idempotency_key(
        profile: LoadedProfile, corpus: Dict[str, Any], corpus_fingerprint: str
    ) -> str:
        payload = "|".join(
            [
                profile.consumer,
                profile.name,
                profile.revision,
                json.dumps(corpus, sort_keys=True, separators=(",", ":")),
                corpus_fingerprint,
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
        corpus_fingerprint = self.deps.corpus.fingerprint(
            corpus, corpus.get("path")
        )
        key = self._idempotency_key(profile, corpus, corpus_fingerprint)

        record = IngestionRecord(
            id=str(uuid.uuid4()),
            consumer=profile.consumer,
            profile=profile.name,
            revision=profile.revision,
            idempotency_key=key,
            content_digest=corpus_fingerprint[:16],
            profile_snapshot=profile.to_dict(corpus=corpus),
            corpus=dict(corpus),
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
        return self.store.create_if_absent(record)

    def mark_dispatch_failed(
        self, ingestion_id: str, message: str
    ) -> Optional[IngestionRecord]:
        error = IngestionError(phase="dispatch", message=message)
        return self.store.fail_pending_dispatch(
            ingestion_id,
            {
                "phase": error.phase,
                "message": error.message,
                "file": error.file,
                "service": error.service,
                "http_status": error.http_status,
                "body": error.body,
            },
            _now_iso(),
        )

    # ── run (orchestrate) ────────────────────────────────────────────
    async def _refresh_cancel(self, record: IngestionRecord) -> None:
        latest = await asyncio.to_thread(self.store.get, record.id)
        if latest is not None and latest.cancel_requested:
            record.cancel_requested = True

    async def _persist(self, record: IngestionRecord, owner: str) -> None:
        record.updated_at = _now_iso()
        saved = await asyncio.to_thread(self.store.save_claimed, record, owner)
        if not saved:
            raise IngestionExecutionLeaseLost(
                f"Execution lease lost for RAG ingestion {record.id}"
            )

    async def _heartbeat_execution(
        self,
        ingestion_id: str,
        owner: str,
        lease_seconds: int,
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        interval = max(1.0, lease_seconds / 3)
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                try:
                    renewed = await asyncio.to_thread(
                        self.store.renew_execution,
                        ingestion_id,
                        owner,
                        lease_seconds,
                    )
                except Exception:
                    logger.exception(
                        "RAG execution lease renewal failed for ingestion %s",
                        ingestion_id,
                    )
                    lease_lost.set()
                    return
                if not renewed:
                    logger.warning(
                        "RAG execution lease ownership lost for ingestion %s",
                        ingestion_id,
                    )
                    lease_lost.set()
                    return

    async def _run_phase_with_lease(
        self,
        name: str,
        record: IngestionRecord,
        profile: LoadedProfile,
        corpus: Dict[str, Any],
        state: Dict[str, Any],
        lease_lost: asyncio.Event,
    ) -> None:
        if lease_lost.is_set():
            raise IngestionExecutionLeaseLost(
                f"Execution lease lost for RAG ingestion {record.id}"
            )

        phase_task = asyncio.create_task(
            self._run_phase(name, record, profile, corpus, state)
        )
        lease_task = asyncio.create_task(lease_lost.wait())
        try:
            await asyncio.wait(
                (phase_task, lease_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lease_lost.is_set():
                phase_task.cancel()
                with suppress(asyncio.CancelledError):
                    await phase_task
                raise IngestionExecutionLeaseLost(
                    f"Execution lease lost for RAG ingestion {record.id}"
                )
            await phase_task
        finally:
            lease_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease_task

    async def run(
        self,
        ingestion_id: str,
        *,
        retry_transient: bool = False,
        execution_owner: Optional[str] = None,
        execution_lease_seconds: Optional[int] = None,
    ) -> IngestionRecord:
        record = await asyncio.to_thread(self.store.get, ingestion_id)
        if record is None:
            raise KeyError(ingestion_id)
        if record.is_terminal:
            return record
        if record.profile_snapshot:
            profile = LoadedProfile.from_dict(record.profile_snapshot)
        else:
            # Records created before execution snapshots were introduced retain
            # their historical registry-resolution behavior.
            profile = self._resolve_profile(record.profile)
        corpus = dict(record.corpus or profile.corpus)
        owner = execution_owner or f"local-{uuid.uuid4()}"
        lease_seconds = (
            ingestion_execution_lease_seconds()
            if execution_lease_seconds is None
            else _validate_execution_lease_seconds(execution_lease_seconds)
        )
        claimed = await asyncio.to_thread(
            self.store.claim_execution, ingestion_id, owner, lease_seconds
        )
        if not claimed:
            latest = await asyncio.to_thread(self.store.get, ingestion_id)
            if latest is not None and latest.is_terminal:
                return latest
            raise IngestionExecutionBusy(
                f"RAG ingestion {ingestion_id} is already running"
            )
        heartbeat_stop = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_execution(
                ingestion_id,
                owner,
                lease_seconds,
                heartbeat_stop,
                lease_lost,
            )
        )

        try:
            record.status = STATUS_RUNNING
            await self._persist(record, owner)

            state: Dict[str, Any] = {
                "files": [],
                "docs": [],
                "chunks": [],
                "vectors": [],
            }
            for name in (
                "discover", "parse", "chunk", "embed",
                "vector_write", "lightrag_upload", "drain", "finalize",
            ):
                await self._refresh_cancel(record)
                if record.cancel_requested:
                    record.status = STATUS_CANCELLED
                    await self._persist(record, owner)
                    return record
                await self._run_phase_with_lease(
                    name, record, profile, corpus, state, lease_lost
                )
                await self._persist(record, owner)
        except IngestionCancelled:
            record.status = STATUS_CANCELLED
            await self._persist(record, owner)
            return record
        except PhaseFatal as fatal:
            record.add_error(fatal.error)
            self._mark_failed_phase(record, fatal.error.phase, fatal.error)
            record.status = STATUS_FAILED
            await self._persist(record, owner)
            return record
        except IngestionExecutionLeaseLost:
            raise
        except TRANSIENT_EXCEPTIONS as exc:
            if retry_transient:
                running = next(
                    (
                        phase
                        for phase in record.phases
                        if phase.status == STATUS_RUNNING
                    ),
                    record.phase("finalize"),
                )
                running.status = STATUS_PENDING
                running.started_at = None
                running.ended_at = None
                running.duration_ms = None
                running.error = None
                running.note = "waiting for Celery retry"
                record.status = STATUS_PENDING
                await self._persist(record, owner)
                raise
            await self._record_unexpected_failure(record, exc, owner)
            return record
        except Exception as exc:  # noqa: BLE001 - any unexpected phase error is a
            # recorded, actionable job failure — never a crashed worker.
            await self._record_unexpected_failure(record, exc, owner)
            return record
        else:
            record.status = (
                STATUS_FAILED
                if record.errors and self._has_fatal_phase(record)
                else STATUS_COMPLETED
            )
            await self._persist(record, owner)
            return record
        finally:
            heartbeat_stop.set()
            await heartbeat
            await asyncio.to_thread(
                self.store.release_execution, ingestion_id, owner
            )

    async def _record_unexpected_failure(
        self, record: IngestionRecord, exc: Exception, owner: str
    ) -> None:
        running = next(
            (p.name for p in record.phases if p.status == STATUS_RUNNING), "finalize"
        )
        error = IngestionError(phase=running, message=f"unexpected error: {_describe_exc(exc)}")
        record.add_error(error)
        self._mark_failed_phase(record, running, error)
        record.status = STATUS_FAILED
        await self._persist(record, owner)

    def _has_fatal_phase(self, record: IngestionRecord) -> bool:
        return any(p.status == STATUS_FAILED for p in record.phases)

    def _mark_failed_phase(self, record: IngestionRecord, name: str, error: IngestionError) -> None:
        phase = record.phase(name)
        phase.status = STATUS_FAILED
        phase.error = {
            "message": error.message,
            "service": error.service,
            "http_status": error.http_status,
            "body": error.body,
        }

    async def _run_phase(self, name: str, record: IngestionRecord, profile: LoadedProfile, corpus: Dict[str, Any], state: Dict[str, Any]) -> None:
        phase = record.phase(name)
        phase.status = STATUS_RUNNING
        phase.started_at = _now_iso()
        phase.ended_at = None
        phase.duration_ms = None
        phase.note = None
        phase.error = None
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
        files: List[CorpusFile] = await asyncio.to_thread(
            self.deps.corpus.discover, corpus, corpus.get("path")
        )
        state["files"] = files
        record.counts["files_discovered"] = len(files)
        record.phase("discover").counts = {"files": len(files)}
        # Content hash of the discovered bytes (provenance).
        manifest = sorted(
            (f.name, hashlib.sha256(f.content).hexdigest()) for f in files
        )
        record.content_digest = hashlib.sha256(
            json.dumps(manifest, separators=(",", ":")).encode("utf-8")
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
        from pydantic import ValidationError

        from chunking_service import (
            ChunkingDependencyError,
            ChunkingError,
            ChunkRequest,
            chunk_text,
        )

        chunker = profile.chunker or {}
        strategy = chunker.get("strategy", "recursive")
        chunk_size = int(chunker.get("chunk_size", 512))
        overlap = int(chunker.get("overlap", 64))
        chunks: List[Dict[str, Any]] = []
        chunked_ok = 0
        for doc in state["docs"]:
            if not doc.text.strip():
                continue
            try:
                resp = await asyncio.to_thread(
                    chunk_text,
                    ChunkRequest(
                        text=doc.text,
                        strategy=strategy,
                        chunk_size=chunk_size,
                        overlap=overlap,
                    ),
                )
            except ChunkingDependencyError:
                # Systemic (chonkie missing) — affects every document; fail the
                # job rather than silently isolating it to a zero-chunk success.
                raise
            except (ValidationError, ChunkingError) as exc:
                # Per-document failure — e.g. doc.text over ChunkRequest's length
                # cap, or a chonkie chunking error. Isolate it and continue like
                # _phase_parse does, instead of aborting the whole corpus.
                record.add_error(
                    IngestionError(phase="chunk", file=doc.name, message=str(exc))
                )
                continue
            for tc in resp.chunks:
                chunks.append({"source": doc.name, "index": tc.index, "content": tc.content})
            chunked_ok += 1
        state["chunks"] = chunks
        record.counts["chunks"] = len(chunks)
        record.phase("chunk").counts = {
            "chunks": len(chunks),
            "documents_chunked": chunked_ok,
            "failed": len([d for d in state["docs"] if d.text.strip()]) - chunked_ok,
        }

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
            for target in targets:
                if not self.deps.weaviate.available():
                    self._target_unavailable(
                        record, "vector_write", "weaviate", "weaviate",
                        target.get("on_unavailable", "fail"),
                    )
                    return
                class_name = weaviate_class_name(target['collection_prefix'], profile.name)
                await self.deps.weaviate.ensure_class(class_name)
                await self.deps.weaviate.reconcile_objects(
                    class_name, profile.name, []
                )
            record.phase("vector_write").note = "no chunks; stale objects removed"
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
            class_name = weaviate_class_name(target['collection_prefix'], profile.name)
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
                await self.deps.weaviate.reconcile_objects(
                    class_name,
                    profile.name,
                    [obj["id"] for obj in objects],
                )
            except PhaseFatal:
                raise
            except TRANSIENT_EXCEPTIONS:
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
            except TRANSIENT_EXCEPTIONS:
                raise
            except Exception as exc:  # noqa: BLE001
                http_status, body = _http_error_details(exc)
                raise PhaseFatal(
                    IngestionError(
                        phase="lightrag_upload", service="lightrag", message=str(exc),
                        http_status=http_status, body=body,
                    )
                )
        record.counts["documents_uploaded"] = uploaded
        record.phase("lightrag_upload").counts = {"uploaded": uploaded}

    def _drain_backoff_delay(self, consecutive_failures: int) -> float:
        """Bounded exponential backoff with full jitter (seconds) between
        consecutive transient ``pipeline_status`` failures. Full jitter (a
        uniform draw in ``[0, delay]``) avoids synchronized retry storms across
        concurrent ingestions."""
        import random

        base = max(0.0, self.deps.drain_backoff_base)
        cap = max(base, self.deps.drain_backoff_max)
        delay = min(base * (2 ** max(0, consecutive_failures - 1)), cap)
        return random.uniform(0.0, delay) if delay > 0 else 0.0

    async def _phase_drain(self, record, profile, corpus, state):
        targets = [t for t in profile.graph_targets if t.get("wait_for_extraction", True)]
        if not targets or not self.deps.lightrag.available():
            record.phase("drain").status = "skipped"
            record.phase("drain").note = "no drainable graph target"
            return
        import asyncio

        polls = 0
        transient_retries = 0
        for target in targets:
            timeout = int(target.get("timeout_seconds", 3600))
            deadline = time.monotonic() + timeout
            consecutive_transient = 0
            last_transient_exc: Optional[BaseException] = None
            while True:
                transient = False
                try:
                    polls += 1
                    busy = await self.deps.lightrag.pipeline_busy()
                except httpx.HTTPStatusError as exc:
                    # Deterministic HTTP failure (401/403, validation, other
                    # 4xx): never retried — fail immediately with bounded,
                    # actionable detail (#673).
                    status, body = _http_error_details(exc)
                    raise PhaseFatal(
                        IngestionError(
                            phase="drain", service="lightrag",
                            message=f"pipeline_status failed: {_describe_exc(exc)}",
                            http_status=status, body=body,
                        )
                    )
                except TRANSIENT_EXCEPTIONS as exc:
                    # LightRAG can briefly stop servicing pipeline_status during
                    # a long extraction/merge. Retry within the drain deadline
                    # rather than failing a healthy ingestion (#673).
                    transient = True
                    transient_retries += 1
                    consecutive_transient += 1
                    last_transient_exc = exc
                else:
                    consecutive_transient = 0
                    last_transient_exc = None
                    if not busy:
                        break

                if time.monotonic() >= deadline:
                    detail = f"extraction did not drain within {timeout}s"
                    if last_transient_exc is not None:
                        detail += (
                            f" (last pipeline_status error after {transient_retries} "
                            f"transient retr{'y' if transient_retries == 1 else 'ies'}: "
                            f"{_describe_exc(last_transient_exc)})"
                        )
                    raise PhaseFatal(
                        IngestionError(
                            phase="drain", service="lightrag", message=detail,
                        )
                    )
                # Cancellation stays responsive between every poll — including
                # while backing off after a transient failure — and the
                # execution-lease heartbeat runs in its own task, unaffected.
                await self._refresh_cancel(record)
                if record.cancel_requested:
                    raise IngestionCancelled()
                delay = (
                    self._drain_backoff_delay(consecutive_transient)
                    if transient
                    else self.deps.poll_interval
                )
                await asyncio.sleep(delay)
        note = "extraction drained"
        if transient_retries:
            note += (
                f" after {transient_retries} transient pipeline_status "
                f"retr{'y' if transient_retries == 1 else 'ies'}"
            )
        drain_phase = record.phase("drain")
        drain_phase.note = note
        # Drain evidence: how many status polls ran and how many were transient
        # retries, so an operator can see a poll race happened (#673 AC).
        drain_phase.counts = {
            "status_polls": polls,
            "transient_retries": transient_retries,
        }

    async def _phase_finalize(self, record, profile, corpus, state):
        record.phase("finalize").counts = dict(record.counts)
