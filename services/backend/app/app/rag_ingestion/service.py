"""RAG ingestion service + phase orchestrator (#413).

``submit`` resolves a declared profile, computes the idempotency key
(consumer + profile + revision + corpus digest + embedding model + declared
destination context), dedups against the store, and
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
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from redis.exceptions import RedisError

from .clients import (
    ingestion_runtime_context,
    is_definitive_rejection,
    CorpusFile,
    Deps,
    ParsedDocument,
    ParserError,
)
from .models import (
    ingestion_idempotency_key, weaviate_class_name, weaviate_objects, http_error_details,
    PUBLICATION_WAIT_NOTES,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    IngestionError,
    IngestionRecord,
)
from .profiles import LoadedProfile, get_profile
from .store import (
    ExecutionClaim, PublicationClaim, IngestionStore, default_store,
    publication_target, publication_targets,
    _validate_execution_lease_seconds, ingestion_execution_lease_seconds,
)


async def _cancel_pending_task(task: asyncio.Task[Any]) -> None:
    """Cancel if needed and always consume the child's terminal result."""
    if not task.done():
        task.cancel()
    # A child may raise while unwinding its cancellation handler. Cleanup must
    # still preserve the parent's cancellation and join the lease watcher.
    with suppress(asyncio.CancelledError, Exception):
        await task


logger = logging.getLogger(__name__)
_DISPATCH_LEASE_SECONDS = 30


async def _join_cleanup_task(
    task: asyncio.Task[Any], *, operation: str, ingestion_id: str
) -> None:
    """Join owned cleanup despite repeated cancellation; log, never replace."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    try:
        task.result()
    except asyncio.CancelledError:
        logger.warning(
            "RAG %s cleanup was cancelled for ingestion %s",
            operation,
            ingestion_id,
        )
    except Exception as exc:
        logger.error(
            "RAG %s cleanup failed for ingestion %s",
            operation,
            ingestion_id,
            exc_info=exc,
        )



def _renewal_retry_wait(
    ingestion_id: str, deadline: float, interval: float, call_bound: float,
) -> Optional[float]:
    """Seconds before retrying a renewal that raised, or None once the lease
    would expire (cycle 12 of the 2026-10-08 run). ``call_bound`` is how long
    one renewal can block: with a 1 s margin the last call returned after
    the lease had expired, and the phase kept writing unleased (cycle 50)."""
    remaining = deadline - time.monotonic()
    logger.exception(
        "RAG execution lease renewal failed for ingestion %s (%.1fs of lease left)",
        ingestion_id,
        remaining,
    )
    if remaining <= call_bound + 1.0:
        return None
    return min(interval, max(0.5, remaining / 3))


class PhaseFatal(RuntimeError):
    """A phase failure that aborts the remaining phases and fails the job (a
    capability ``on_unavailable: fail`` target, or a drain timeout)."""

    def __init__(self, error: IngestionError):
        super().__init__(error.message)
        self.error = error


class IngestionCancelled(RuntimeError):
    pass


class IngestionExecutionBusy(RuntimeError):
    """Raised when execution or collection publication cannot be admitted."""


class CollectionPublicationBusy(IngestionExecutionBusy):
    """A Weaviate class this job writes is guarded by another attempt.

    ``note`` names the guard state when the admission probe observed it.
    """

    def __init__(self, message: str, guard: Optional[str] = None) -> None:
        super().__init__(message)
        self.note = PUBLICATION_WAIT_NOTES.get(guard, PUBLICATION_WAIT_NOTES[None])


class IngestionExecutionLeaseLost(RuntimeError):
    """Raised when a worker can no longer persist under its execution lease."""


TRANSIENT_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.NetworkError,
    RedisError,
)


def is_permanent_redis_reply(exc: BaseException) -> bool:
    """A Redis error reply that repeats on every attempt, e.g. WRONGTYPE (#1354).

    Subclasses Redis returns while it recovers (read-only replica after a
    failover, maxmemory pressure, cluster resharding, a lost master) stay
    transient."""
    from redis import exceptions as redis_errors

    transient = tuple(
        getattr(redis_errors, name)
        for name in ("ReadOnlyError", "OutOfMemoryError", "TryAgainError",
                     "ClusterDownError", "MasterDownError", "AskError", "MovedError")
        if hasattr(redis_errors, name)
    )
    return isinstance(exc, redis_errors.ResponseError) and not isinstance(exc, transient)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class RagIngestionService:
    def __init__(self, store: Optional[IngestionStore] = None, deps: Optional[Deps] = None, profiles_path: Optional[str] = None) -> None:
        self.store = store or default_store()
        self.deps = deps or Deps()
        self._profiles_path = profiles_path

    # ── submit ───────────────────────────────────────────────────────
    def _resolve_profile(self, name: str) -> LoadedProfile:
        return get_profile(name, self._profiles_path)

    def _job_key(self, profile, corpus, fingerprint):
        base = ingestion_idempotency_key(
            profile, corpus, fingerprint, str(getattr(self.deps.embedder, "model", "") or "")
        )
        context = ingestion_runtime_context(profile, self.deps.weaviate, self.deps.lightrag)
        payload = json.dumps([base, context], separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def total_graph_wait(self, profile_name: str) -> int:
        """Seconds the drain may wait in total: each graph target that waits
        for extraction gets its own timeout_seconds, one after another (0 when
        none waits)."""
        profile = self._resolve_profile(profile_name)  # raises ProfileNotFoundError
        return sum(
            int(t.get("timeout_seconds", 3600)) for t in profile.graph_targets
            if t.get("wait_for_extraction", True)
        )

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
        key = self._job_key(profile, corpus, corpus_fingerprint)

        record = IngestionRecord(
            id=str(uuid.uuid4()),
            consumer=profile.consumer,
            profile=profile.name,
            revision=profile.revision,
            idempotency_key=key,
            content_digest=corpus_fingerprint[:16],
            profile_snapshot=profile.to_dict(corpus=corpus),
            corpus=dict(corpus),
            publication_targets=publication_targets(profile),
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
        self, ingestion_id: str, message: str, owner: Optional[str] = None
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
            (_now_iso(), owner),
        )

    def claim_dispatch(self, ingestion_id: str, owner: str) -> bool:
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=_DISPATCH_LEASE_SECONDS)
        return self.store.claim_dispatch(
            ingestion_id,
            (owner, now.isoformat(), stale_before.isoformat()),
        )

    def mark_dispatched(
        self, ingestion_id: str, job_id: Optional[str], owner: str
    ) -> Optional[IngestionRecord]:
        return self.store.mark_dispatched(ingestion_id, (job_id, owner), _now_iso())

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
        # The lease stays valid until this deadline; a renewal that raises
        # (one slow Redis reply) is retried until then instead of discarding
        # the whole run at the first error (2026-10-08 run, cycle 12).
        deadline = time.monotonic() + lease_seconds
        wait = interval
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
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
                    wait = _renewal_retry_wait(
                        ingestion_id, deadline, interval, getattr(self.store, "call_timeout_seconds", 0.0),
                    )
                    if wait is None:
                        lease_lost.set()
                        return
                    continue
                if not renewed:
                    logger.warning(
                        "RAG execution lease ownership lost for ingestion %s",
                        ingestion_id,
                    )
                    lease_lost.set()
                    return
                deadline = time.monotonic() + lease_seconds
                wait = interval

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
                await _cancel_pending_task(phase_task)
                raise IngestionExecutionLeaseLost(
                    f"Execution lease lost for RAG ingestion {record.id}"
                )
            await phase_task
        finally:
            try:
                await _cancel_pending_task(phase_task)
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
        execution_recovery_owner: Optional[str] = None,
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
        claim = ExecutionClaim(owner, lease_seconds, execution_recovery_owner)
        claimed = await asyncio.to_thread(
            self.store.claim_execution, ingestion_id, claim
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
        state: Dict[str, Any] = {
            "files": [],
            "docs": [],
            "chunks": [],
            "vectors": [],
            "execution_owner": owner,
            "publication_claim": PublicationClaim(owner, str(uuid.uuid4())),
        }

        try:
            record.status = STATUS_RUNNING
            if record.publication_targets is None:
                record.publication_targets = publication_targets(profile)
            # Every attempt re-runs all phases from fresh state, so errors
            # persisted by a superseded attempt (before a Celery retry) are
            # stale duplicates, not findings of this run.
            record.errors = []
            await self._persist(record, owner)

            for name in (
                "discover", "parse", "chunk", "embed",
                "vector_write", "lightrag_upload", "drain", "finalize",
            ):
                await self._refresh_cancel(record)
                if record.cancel_requested:
                    record.status = STATUS_CANCELLED
                    await self._persist(record, owner)
                    return record
                if name == "discover":
                    await self._refuse_guarded_publication(record)
                await self._run_phase_with_lease(
                    name, record, profile, corpus, state, lease_lost
                )
                await self._persist(record, owner)
        except IngestionCancelled:
            record.status = STATUS_CANCELLED
            await self._persist(record, owner)
            return record
        except CollectionPublicationBusy as busy:
            # Lease contention raises before this attempt starts, so only
            # collection admission refuses here. Its note belongs on the
            # phase that waits. A foreign execution owner must still surface
            # LeaseLost from this persist, never Busy.
            record.defer_retry(busy.note, "vector_write")
            await self._persist(record, owner)
            raise
        except PhaseFatal as fatal:
            record.add_error(fatal.error)
            record.mark_failed_phase(fatal.error.phase, fatal.error)
            record.status = STATUS_FAILED
            await self._persist(record, owner)
            return record
        except IngestionExecutionLeaseLost:
            raise
        except TRANSIENT_EXCEPTIONS as exc:
            if retry_transient and not is_permanent_redis_reply(exc):
                record.defer_retry("waiting for Celery retry")
                await self._persist(record, owner)
                raise
            await self._record_unexpected_failure(record, exc, owner)
            return record
        except Exception as exc:  # noqa: BLE001 - any unexpected phase error is a
            # recorded, actionable job failure — never a crashed worker.
            await self._record_unexpected_failure(record, exc, owner)
            return record
        except asyncio.CancelledError:
            # Celery's soft time limit (or a worker shutdown) escapes the event
            # loop and asyncio.run cancels this coroutine. Without a terminal
            # record the job stayed "running", and as a dedup status it
            # answered every resubmit with the dead job until its TTL expired.
            try:
                await self._record_unexpected_failure(
                    record,
                    RuntimeError("ingestion interrupted before completion (Celery time limit)"),
                    owner,
                )
            except Exception:  # noqa: BLE001 - never replace the cancellation
                logger.exception("could not record interrupted RAG ingestion %s", record.id)
            raise
        else:
            record.status = (
                STATUS_FAILED
                if record.errors and record.has_fatal_phase()
                else STATUS_COMPLETED
            )
            await self._persist(record, owner)
            return record
        finally:
            heartbeat_stop.set()
            await _join_cleanup_task(
                heartbeat,
                operation="execution heartbeat",
                ingestion_id=ingestion_id,
            )
            # Fallback: the vector phase normally released them already.
            await self._release_clean_publications(record, state)
            release = asyncio.create_task(
                asyncio.to_thread(
                    self.store.release_execution, ingestion_id, owner
                )
            )
            await _join_cleanup_task(
                release,
                operation="execution lease release",
                ingestion_id=ingestion_id,
            )

    async def fail_abandoned(self, ingestion_id: str, recovery_owner: str, reason: str) -> bool:
        """Mark a run the worker gave up on as failed. Without this the record
        kept its last status, "running", which the create script treats as a
        dedup hit, so every resubmit got the dead job back until the TTL
        (2026-10-08 run, cycle 30). Fenced: the claim fails while another
        live worker holds the lease, and that run is left alone."""
        owner = f"abandon:{uuid.uuid4()}"
        claim = ExecutionClaim(owner, ingestion_execution_lease_seconds(), recovery_owner)
        if not await asyncio.to_thread(self.store.claim_execution, ingestion_id, claim):
            return False
        try:
            record = await asyncio.to_thread(self.store.get, ingestion_id)
            if record is None or record.is_terminal:
                return False
            await self._record_unexpected_failure(record, RuntimeError(reason), owner)
            return True
        finally:
            await asyncio.to_thread(self.store.release_execution, ingestion_id, owner)

    async def _record_unexpected_failure(
        self, record: IngestionRecord, exc: Exception, owner: str
    ) -> None:
        running = next(
            (p.name for p in record.phases if p.status == STATUS_RUNNING), "finalize"
        )
        error = IngestionError(phase=running, message=f"unexpected error: {_describe_exc(exc)}")
        record.add_error(error)
        record.mark_failed_phase(running, error)
        record.status = STATUS_FAILED
        await self._persist(record, owner)

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
        fingerprint = hashlib.sha256(
            json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        record.content_digest = fingerprint[:16]
        runtime_key = self._job_key(profile, corpus, fingerprint)
        if runtime_key != record.idempotency_key:
            record.counts["dedup_invalidated"] = 1
            record.phase("discover").note = "corpus or destination identity changed; resubmits create a new job"

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
                state.setdefault("failed_sources", set()).add(file.name)
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

    @classmethod
    def _note_empty_parse(cls, record, state, name: str) -> None:
        """Handle a document that produced no text.

        An OPERATOR-EMPTIED file and a PARSER FAULT both arrive here with no
        text and want OPPOSITE outcomes: the emptied file must lose its
        vectors (the reconcile doing its job, and the contract this module's
        tests state), while a real document a parser turned into whitespace
        must be excluded from stale-object deletion. The preserve-set protects
        objects in a surviving class; a class replacement can still remove them.

        The source bytes settle it: `state["files"]` holds what was actually on
        disk. Preserving unconditionally meant an emptied file's stale content
        stayed searchable FOREVER, with the run reporting completed and zero
        errors.
        """
        if not cls._source_had_content(state, name):
            return
        state.setdefault("failed_sources", set()).add(name)
        record.add_error(
            IngestionError(
                phase="chunk",
                file=name,
                message=(
                    "parsed to whitespace despite non-empty source; "
                    "treated as a failed source"
                ),
            )
        )

    @staticmethod
    def _source_had_content(state, name: str) -> bool:
        """True when the file on disk was non-empty.

        A parser that returns whitespace for real bytes has FAILED; a file the
        operator emptied has not.
        """
        for file in state.get("files", ()):
            if file.name == name:
                return bool((file.content or b"").strip())
        return False

    async def _phase_chunk(self, record, profile, corpus, state):
        from pydantic import ValidationError

        from chunking_service import (
            ChunkingDependencyError,
            ChunkingError,
            CorpusChunkRequest,
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
                self._note_empty_parse(record, state, doc.name)
                continue
            try:
                resp = await asyncio.to_thread(
                    chunk_text,
                    CorpusChunkRequest(
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
                # Per-document failure — e.g. a chonkie chunking error or an
                # invalid chunker setting. Isolate it and continue like
                # _phase_parse does, instead of aborting the whole corpus.
                state.setdefault("failed_sources", set()).add(doc.name)
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

    @staticmethod
    def _refuse_destructive_reconcile(record) -> None:
        """Fail the run instead of wiping the previous generation.

        The phase status alone is NOT enough. Terminal status is
        `FAILED if record.errors and record.has_fatal_phase()`, so with an
        empty `errors` list the `and` short-circuits to COMPLETED — and the
        whitespace-parse case records no per-document errors at all. The run
        then reported success while having produced nothing, which is exactly
        the signal a consumer polls on. Record an error too, so both halves of
        that condition hold.
        """
        discovered = record.counts["files_discovered"]
        message = (
            f"{discovered} file(s) discovered but no chunks survived parse/chunk — "
            f"refusing to reconcile, which would delete the previous generation"
        )
        record.phase("vector_write").status = "failed"
        record.phase("vector_write").note = message
        record.counts["vectors_written"] = 0
        record.add_error(IngestionError(phase="vector_write", message=message))

    async def _ensure_target_class(self, record, state, class_name: str) -> None:
        if await self.deps.weaviate.ensure_class(class_name, embedding=self._embedding_of(state)):
            record.counts[f"class_rebuilt:{class_name}"] = 1

    def _embedding_of(self, state) -> tuple[str, int]:
        """(model, dimension) of this run's vectors: the class identity (#1364)."""
        return (str(getattr(self.deps.embedder, "model", "") or ""), len(state["chunks"][0]["vector"]))

    async def _reconcile_kept_sources(
        self, record, profile, state, class_name, objects
    ) -> None:
        """Delete stale objects, excluding sources that failed THIS attempt.

        `failed_sources` comes from `state`, not `record.errors`: that list is
        reloaded with the record and carries entries from earlier attempts, so
        a clean Celery retry would otherwise still skip the cleanup.
        """
        failed_sources = sorted(state.get("failed_sources", set()))
        await self.deps.weaviate.reconcile_objects(
            class_name,
            profile.name,
            [obj["id"] for obj in objects],
            preserve_sources=failed_sources,
        )
        if failed_sources and record.counts.get(f"class_rebuilt:{class_name}"):
            # A preceding attempt can rebuild the class and write some objects.
            # Its earlier vectors were removed; partial retry writes may exist.
            note = (
                f"wrote {len(objects)} object(s); class rebuilt during this job; "
                f"earlier vectors were removed, so {len(failed_sources)} source(s) "
                f"that failed this attempt may have no vectors until a later run "
                f"succeeds ({', '.join(failed_sources[:5])})"
            )
        elif failed_sources:
            note = (
                f"wrote {len(objects)} object(s); excluded {len(failed_sources)} "
                f"source(s) that failed this attempt from stale-object deletion; "
                f"their vector availability is unverified; an interrupted class "
                f"replacement may have removed earlier vectors "
                f"({', '.join(failed_sources[:5])})"
            )
        else:
            return
        phase = record.phase("vector_write")
        prior = f"{phase.note}; " if phase.note else ""
        phase.note = f"{prior}{class_name}: {note}"

    async def _claim_publications(self, record, state):
        if state.get("publication_claimed"):
            return
        admission = asyncio.create_task(asyncio.to_thread(
            self.store.claim_publications, record.id, state["publication_claim"],
        ))
        try:
            claimed = await asyncio.shield(admission)
        except asyncio.CancelledError:
            await _join_cleanup_task(
                admission, operation="publication admission", ingestion_id=record.id,
            )
            raise
        if not claimed:
            raise CollectionPublicationBusy(f"RAG collection publication blocked for ingestion {record.id}")
        state["publication_claimed"] = True

    async def _refuse_guarded_publication(self, record) -> None:
        # Read-only admission before discover/parse/chunk/embed: a guarded class
        # would refuse this attempt at vector_write after all that work. The
        # atomic claim in vector_write stays the authority. Without Weaviate or
        # the embedder, a job with chunks never reaches the claim (vector_write
        # skips or fails first), so a guard must not hold it back. An empty
        # corpus still meets the claim.
        targets = record.publication_targets
        guard = (
            targets and self.deps.weaviate.available() and self.deps.embedder.available()
            and await asyncio.to_thread(self.store.publication_guarded, targets)
        )
        if guard:
            raise CollectionPublicationBusy(
                f"RAG collection publication blocked for ingestion {record.id}", guard,
            )

    async def _join_store_write(self, record, operation: str, *call) -> None:
        # Runs to completion despite cancellation, so no late write follows the
        # caller's next step; a failure is logged, never raised.
        write = asyncio.create_task(asyncio.to_thread(*call))
        await _join_cleanup_task(write, operation=operation, ingestion_id=record.id)

    async def _release_clean_publications(self, record, state) -> None:
        # Token-scoped and idempotent: deletes only this attempt's clean guards.
        await self._join_store_write(
            record, "clean publication guard release", self.store.release_publications,
            record.id, state["publication_claim"], record.publication_targets or [],
        )
        state["publication_claimed"] = False

    async def _settle_rejected_publication(self, record, state, failure: tuple[str, BaseException]) -> None:
        # Weaviate answered with a 4xx, so no request of this attempt is still in
        # flight: end the uncertain-write block. The publication proof keeps the
        # in-progress token, so completed-job reuse stays invalid. A 5xx, timeout,
        # cancellation or other error leaves the guard dirty.
        class_name, exc = failure
        if is_definitive_rejection(exc):
            await self._join_store_write(
                record, "rejected publication settle", self.store.settle_publication,
                record.id, state["publication_claim"], publication_target(class_name),
            )

    async def _mark_publication(self, record, state, publication: tuple[str, bool]) -> None:
        class_name, published = publication
        target = publication_target(class_name)
        await self._claim_publications(record, state)
        saved = await asyncio.to_thread(
            self.store.mark_publication, record.id, state["publication_claim"], (target, published)
        )
        if not saved:
            raise IngestionExecutionLeaseLost(f"Publication fence lost for RAG ingestion {record.id}")

    async def _phase_vector_write(self, record, profile, corpus, state):
        if not profile.vector_targets:
            record.phase("vector_write").status = "skipped"
            record.phase("vector_write").note = "no vector_targets"
            return
        try:
            await self._write_vector_targets(record, profile, state)
        finally:
            # Guards cover only this phase. Holding them through LightRAG upload
            # and drain would block the class for up to an hour, and a worker
            # crash there would leave it blocked.
            await self._release_clean_publications(record, state)

    async def _write_vector_targets(self, record, profile, state):
        targets = profile.vector_targets
        if not state["chunks"]:
            # An EMPTY CORPUS and a TOTALLY FAILED RUN are not the same thing.
            # `reconcile_objects(cls, profile, [])` deletes every object for
            # this profile, so reaching here after files WERE discovered wipes
            # the entire previous generation — and the job still reported
            # `status: "completed"`. Reproduced three ways: every file 5xx from
            # docling, `overlap >= chunk_size`, and documents that parse to
            # whitespace (that last one recorded ZERO errors, so a consumer
            # polling for "completed" could not tell it from success).
            if record.counts.get("files_discovered"):
                # Only when the target is actually reachable. With
                # `on_unavailable: skip` and the backend down, nothing could
                # have been reconciled, so reporting "refusing to reconcile,
                # which would delete the previous generation" misattributes an
                # unreachable target as a corpus failure — and the contract
                # above says a skip does NOT land in errors[].
                if any(self.deps.weaviate.available() for _ in targets[:1]):
                    self._refuse_destructive_reconcile(record)
                    return
            for target in targets:
                if not self.deps.weaviate.available():
                    self._target_unavailable(
                        record, "vector_write", "weaviate", "weaviate",
                        target.get("on_unavailable", "fail"),
                    )
                    return
                class_name = weaviate_class_name(target['collection_prefix'], profile.name)
                await self._mark_publication(record, state, (class_name, False))
                try:
                    await self.deps.weaviate.ensure_class(class_name)
                    await self.deps.weaviate.reconcile_objects(
                        class_name, profile.name, []
                    )
                except Exception as exc:
                    await self._settle_rejected_publication(record, state, (class_name, exc))
                    raise
                await self._mark_publication(record, state, (class_name, True))
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
                # ensure_class may replace a model-incompatible class before any
                # object write. Invalidate currentness before that first mutation.
                await self._mark_publication(record, state, (class_name, False))
                await self._ensure_target_class(record, state, class_name)
                objects = weaviate_objects(class_name, profile, state["chunks"])
                total += await self.deps.weaviate.write_objects(class_name, objects)
                # Reconcile PER SOURCE. The deletion pass treats "not in this
                # run's output" as "stale", which conflates it with "this run
                # could not produce it" — so a document that FAILED had its
                # previously ingested vectors deleted.
                #
                # Skipping the whole reconcile when anything failed was the
                # wrong correction: one permanently-broken file (a corrupt PDF,
                # an oversize doc) then disabled stale cleanup FOREVER, so
                # vectors of documents the operator DELETED stayed searchable
                # indefinitely. Preserve exactly the sources that failed, and
                # clean up everything else.
                #
                # `failed_sources` is collected in `state` during this ATTEMPT,
                # not derived from `record.errors` — that list is reloaded with
                # the record and carries entries from earlier attempts, so a
                # clean Celery retry would otherwise still skip the cleanup.
                await self._reconcile_kept_sources(
                    record, profile, state, class_name, objects
                )
                await self._mark_publication(record, state, (class_name, True))
            except (IngestionExecutionBusy, IngestionExecutionLeaseLost):
                raise
            except PhaseFatal:
                raise
            except TRANSIENT_EXCEPTIONS:
                raise
            except Exception as exc:  # noqa: BLE001 - upstream write failure is fatal
                await self._settle_rejected_publication(record, state, (class_name, exc))
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
                http_status, body = http_error_details(exc)
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
                    status, body = http_error_details(exc)
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
