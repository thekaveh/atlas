"""
LangMem-inspired persistent memory service.

Provides fact extraction from conversations, semantic memory recall,
memory consolidation/deduplication, and user memory summarization.
Uses Weaviate for vector search with automatic pgvector fallback.
"""

import asyncio
import os
import json
import logging
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4

import httpx

from db_connection import acquire_conn, connect_postgres
from memory_store import MemoryStore, _to_uuid

logger = logging.getLogger("memory_service")


class _StaleConsolidationAction(Exception):
    """A fact changed after the LLM snapshot; roll back the whole action."""


#: Exceptions that mean the SYNC TARGET is unhealthy, as opposed to this one
#: row being bad. Only these count toward the halt streak.
_TARGET_TRANSIENT = (
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)

#: HTTP statuses that indicate the target, not the payload. 401 belongs here
#: (a bad master key affects every row); 408/429 are explicit back-pressure.
_TARGET_HEALTH_STATUSES = frozenset({401, 408, 429})


def _is_target_health_signal(exc: BaseException) -> bool:
    """True when `exc` says the TARGET is unhealthy, not that a row is bad.

    `httpx.HTTPStatusError` cannot be classified by type alone: every
    `raise_for_status()` in the store raises it, for 4xx as well as 5xx. Adding
    the bare type to the transient set made permanent PER-ROW failures — a 400
    from LiteLLM when a fact exceeds the embedding context, a 422 from a
    schema-violating property — count toward the halt streak. Three such rows
    are adjacent by construction after a bulk import, and because failing rows
    keep their `updated_at` they sort first on every later pass, so the stall
    was PERMANENT and user-deleted memories stopped propagating.
    """
    if isinstance(exc, _TARGET_TRANSIENT):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # `getattr`, not `exc.response`: this runs INSIDE the loop's
        # `except Exception` handler, so an AttributeError here would escape
        # `_reconcile_pending_vectors` entirely instead of deferring the row.
        # httpx requires `response` today, but a subclass need not.
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status is None:
            return False
        return status >= 500 or status in _TARGET_HEALTH_STATUSES
    return False


def _is_valid_consolidation_index(index: Any, fact_count: int) -> bool:
    return type(index) is int and 0 <= index < fact_count


def _validated_consolidation_indices(
    source_indices: Any, keep_index: Any, fact_count: int
) -> Optional[tuple[List[int], int]]:
    if not isinstance(source_indices, list) or len(source_indices) < 2:
        return None
    if not all(
        _is_valid_consolidation_index(index, fact_count)
        for index in source_indices
    ):
        return None
    if len(set(source_indices)) != len(source_indices):
        return None
    if not _is_valid_consolidation_index(keep_index, fact_count):
        return None
    if keep_index not in source_indices:
        return None
    return source_indices, keep_index


def _validate_consolidation_action(
    action_data: Any, fact_count: int
) -> Optional[tuple[str, List[int], int, str]]:
    """Return a safe consolidation action or reject untrusted LLM output."""
    if not isinstance(action_data, dict):
        return None
    action = action_data.get("action")
    reason = action_data.get("reason", "")
    if action not in {"merge", "supersede"} or not isinstance(reason, str):
        return None
    indices = _validated_consolidation_indices(
        action_data.get("source_indices"), action_data.get("keep_index"), fact_count
    )
    if indices is None:
        return None
    source_indices, keep_index = indices
    return action, source_indices, keep_index, reason


#: How many TARGET-HEALTH failures since the last successful row mean the
#: sync target itself is down
#: rather than one bad row.
#:
#: A bare `break` on the first failure was strictly worse than the `continue`
#: it replaced: these exception types are PER-REQUEST, and rows come back
#: `ORDER BY updated_at LIMIT 100`, so one poison row is always first — it
#: halted every pass and the backlog could never drain (measured 0/10 rows
#: against 9/10 with `continue`). Because `is_active = false` rows are
#: RETIREMENTS, that also stopped memory deletions propagating to the vector
#: store.
#:
#: Small enough to stop a genuine outage paying 100 round-trips per recall,
#: large enough that a single poison row cannot head-of-line-block the
#: backlog. KNOWN LIMIT: three or more ADJACENT poison rows still stall it —
#: failing rows keep their `updated_at` and so sort first forever. Fixing that
#: needs a `vector_sync_next_at` column or a target-health probe; see
#: D-24.1 in the maintenance audit.
_RECONCILE_TRANSIENT_STREAK = 3


class MemoryService:
    """LangMem-inspired persistent memory service."""

    def __init__(self):
        self.enabled = os.getenv("LANGMEM_ENABLED", "true").lower() == "true"
        self.database_url = os.getenv("DATABASE_URL", "")
        self.litellm_url = os.getenv("LITELLM_BASE_URL", "http://litellm:4000")
        self.litellm_api_key = os.getenv("LITELLM_API_KEY", "")
        self.weaviate_url = os.getenv("WEAVIATE_URL", "")
        self.namespace = os.getenv("LANGMEM_NAMESPACE", "default")
        self.max_facts = int(os.getenv("LANGMEM_MAX_FACTS_PER_USER", "1000"))
        self.extraction_model = os.getenv("LANGMEM_EXTRACTION_MODEL", "")
        self.embedding_model = os.getenv("LANGMEM_EMBEDDING_MODEL", "")

        self.store: Optional[MemoryStore] = None
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self):
        """Lazy initialization of the memory store.

        Locked: concurrent first requests would otherwise double-create
        the store and run MemoryStore.initialize() twice — and its
        delete-and-recreate collection heal must never race itself."""
        if self._initialized:
            return
        if not self.enabled:
            return

        async with self._init_lock:
            if self._initialized:
                return
            await self._initialize_locked()

    async def _initialize_locked(self):
        weaviate = self.weaviate_url if self.weaviate_url else None
        self.store = MemoryStore(
            database_url=self.database_url,
            weaviate_url=weaviate,
            litellm_url=self.litellm_url,
            litellm_api_key=self.litellm_api_key,
            embedding_model=self.embedding_model or None,
        )
        await self.store.initialize()
        self._initialized = True
        logger.info(
            f"MemoryService initialized (vector_backend={self.store.backend})"
        )

    def _check_enabled(self):
        """Raise if the service is disabled."""
        if not self.enabled:
            raise RuntimeError("LangMem memory service is disabled")

    def _resolve_namespace(self, namespace: Optional[str]) -> str:
        """Use the operator-configured namespace only when none was supplied."""
        return self.namespace if namespace is None else namespace

    async def _get_extraction_model(self) -> str:
        """Get the model identifier (LiteLLM-prefixed) for fact extraction.

        Resolution order:
          1. ``self.extraction_model`` (explicit ``LANGMEM_EXTRACTION_MODEL``
             env var / constructor arg).
          2. ``LITELLM_DEFAULT_MODEL`` env var (set in .env, injected by
             compose; value is already fully-qualified, e.g.
             ``ollama/qwen3.8:latest`` or a bare cloud model id).
          3. Raise ``RuntimeError`` — surfaces the missing config at call time
             rather than sending requests to a non-existent LiteLLM route.
        """
        if self.extraction_model:
            return self.extraction_model
        default_model = os.getenv("LITELLM_DEFAULT_MODEL", "")
        if default_model:
            return default_model
        raise RuntimeError(
            "No content model available for memory extraction. Set "
            "LITELLM_DEFAULT_MODEL in .env to a model id LiteLLM serves "
            "(e.g. ollama/qwen3.8:latest)."
        )

    async def _litellm_complete(
        self,
        model: str,
        prompt: str,
        *,
        json_mode: bool = False,
        timeout: float = 60.0,
    ) -> str:
        """Single-prompt completion through the LiteLLM gateway.

        Wraps LiteLLM's OpenAI-compatible /v1/chat/completions. The previous
        Ollama-native /api/generate flow used `prompt` + `format=json`; here
        we use a one-message `messages` array and OpenAI's `response_format`
        for the JSON-mode equivalent. Returns the assistant content string
        (empty on missing/malformed responses — caller decides how to recover).
        """
        headers = {}
        if self.litellm_api_key:
            headers["Authorization"] = f"Bearer {self.litellm_api_key}"
        body: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.litellm_url}/v1/chat/completions",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            result = resp.json()
            choices = result.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            return message.get("content") or ""

    def _acquire(self):
        """Acquire a SHORT-LIVED pooled connection (#804).

        Use this for DB ops that never hold the connection across non-DB I/O
        (an LLM completion, an embedding call, a Weaviate round-trip). The four
        reconcile-orbit paths — ``_reconcile_pending_vectors`` and its
        conn-passing callers ``consolidate`` / ``update_memory`` /
        ``delete_memory`` — deliberately keep a DEDICATED ``connect_postgres``
        connection instead, because they hold it across Weaviate reconcile I/O;
        pinning a bounded pool slot there would risk pool starvation. See the
        pool invariant in db_connection.py.
        """
        return acquire_conn(self.database_url)

    async def extract_facts(
        self,
        user_id: str,
        messages: List[Dict[str, str]],
        namespace: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract facts from conversation messages using the LiteLLM gateway."""
        self._check_enabled()
        await self._ensure_initialized()
        namespace = self._resolve_namespace(namespace)

        session_uuid = uuid4()
        session_id = str(session_uuid)
        user_uuid = _to_uuid(user_id)
        conv_uuid = _to_uuid(conversation_id)
        try:
            async with self._acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO public.memory_sessions
                        (id, user_id, conversation_id, status,
                         processing_started_at)
                    VALUES ($1, $2, $3, 'running', now())
                    """,
                    session_uuid,
                    user_uuid,
                    conv_uuid,
                )
        except asyncio.CancelledError as exc:
            # The INSERT may have committed server-side even when cancellation
            # arrived before the driver returned. A guarded terminal UPDATE is
            # harmless if no row exists and prevents a visible row remaining
            # permanently "running".
            await self._mark_extraction_failed_durably(session_uuid, exc)
            raise
        except Exception as exc:
            # Driver errors after an INSERT are commit-ambiguous. Attempt a
            # guarded terminal update through a fresh acquisition, then keep
            # the original database error as the caller-visible outcome.
            await self._mark_extraction_failed(session_uuid, exc)
            raise

        conversation_text = "\n".join(
            f"{msg.get('role', 'user')}: {msg.get('content', '')}"
            for msg in messages
        )

        try:
            model = await self._get_extraction_model()
            extraction_prompt = f"""Analyze the following conversation and extract key facts about the user.
For each fact, provide:
- content: the fact itself (concise, one sentence)
- fact_type: one of "observation", "preference", "instruction", "relationship", "event"
- confidence: a float between 0.0 and 1.0

Return ONLY a valid JSON array of objects. If no facts can be extracted, return an empty array [].

Conversation:
{conversation_text}

Extract the facts as JSON:"""
            response_text = await self._litellm_complete(
                model=model,
                prompt=extraction_prompt,
                json_mode=True,
                timeout=60.0,
            ) or "[]"
            parsed = json.loads(response_text)
            if isinstance(parsed, dict) and "facts" in parsed:
                parsed = parsed["facts"]
            if isinstance(parsed, dict) and "content" in parsed:
                parsed = [parsed]
            if not isinstance(parsed, list) or not all(
                isinstance(item, dict) for item in parsed
            ):
                raise ValueError("fact extraction response must be a JSON array of objects")
            extracted_facts = parsed
        except asyncio.CancelledError as exc:
            await self._mark_extraction_failed_durably(session_uuid, exc)
            raise
        except Exception as exc:
            logger.error(
                "Fact extraction LLM call failed (error_type=%s)",
                type(exc).__name__,
            )
            await self._mark_extraction_failed(session_uuid, exc)
            return {
                "session_id": session_id,
                "status": "failed",
                "facts_extracted": 0,
                "facts": [],
            }

        stored_facts: List[Dict[str, Any]] = []
        embedding_inputs: List[tuple[Any, Dict[str, Any]]] = []
        try:
            async with self._acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                        user_uuid,
                    )
                    current_count = await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM public.memory_facts
                        WHERE user_id = $1 AND is_active = true
                        """,
                        user_uuid,
                    )
                    for fact_data in extracted_facts:
                        if current_count + len(stored_facts) >= self.max_facts:
                            logger.warning(
                                "User %s reached max facts limit (%s)",
                                user_uuid,
                                self.max_facts,
                            )
                            break
                        content = str(fact_data.get("content", "")).strip()
                        if not content:
                            continue
                        fact_type = fact_data.get("fact_type", "observation")
                        if fact_type not in (
                            "observation",
                            "preference",
                            "instruction",
                            "relationship",
                            "event",
                        ):
                            fact_type = "observation"
                        confidence = max(
                            0.0, min(1.0, float(fact_data.get("confidence", 0.8)))
                        )
                        fact_uuid = uuid4()
                        fact_id = str(fact_uuid)
                        inserted = await conn.fetchrow(
                            """
                            INSERT INTO public.memory_facts
                                (id, user_id, namespace, content, fact_type, confidence,
                                 source_conversation_id, metadata, vector_sync_pending,
                                 created_at, updated_at)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, true, now(), now())
                            RETURNING created_at, updated_at
                            """,
                            fact_uuid,
                            user_uuid,
                            namespace,
                            content,
                            fact_type,
                            confidence,
                            conv_uuid,
                            json.dumps({"source": "auto_extraction"}),
                        )
                        public_fact = {
                            "id": fact_id,
                            "content": content,
                            "fact_type": fact_type,
                            "confidence": confidence,
                            "namespace": namespace,
                            "is_active": True,
                            "created_at": inserted["created_at"].isoformat(),
                            "updated_at": inserted["updated_at"].isoformat(),
                            "metadata": {"source": "auto_extraction"},
                        }
                        stored_facts.append(public_fact)
                        embedding_inputs.append((fact_uuid, public_fact))
                    await conn.execute(
                        """
                        UPDATE public.memory_sessions
                        SET status = 'completed', facts_extracted = $1,
                            processing_completed_at = now()
                        WHERE id = $2
                        """,
                        len(stored_facts),
                        session_uuid,
                    )
        except asyncio.CancelledError as exc:
            await self._mark_extraction_failed_durably(session_uuid, exc)
            raise
        except Exception as exc:
            await self._mark_extraction_failed(session_uuid, exc)
            return {
                "session_id": session_id,
                "status": "failed",
                "facts_extracted": 0,
                "facts": [],
            }

        for fact_uuid, fact in embedding_inputs:
            weaviate_id = None
            try:
                weaviate_id = await self.store.store_embedding(
                    fact_id=fact["id"],
                    content=fact["content"],
                    user_id=user_id,
                    namespace=namespace,
                    fact_type=fact["fact_type"],
                    confidence=fact["confidence"],
                    metadata={},
                )
            except Exception as exc:
                logger.warning(
                    "Failed to store embedding for fact %s (error_type=%s)",
                    fact["id"],
                    type(exc).__name__,
                )
            async with self._acquire() as conn:
                if weaviate_id:
                    await conn.execute(
                        "UPDATE public.memory_facts SET weaviate_id = $1, "
                        "vector_sync_pending = false WHERE id = $2",
                        weaviate_id,
                        fact_uuid,
                    )
                else:
                    # Embedding was not durably stored (store_embedding raised or
                    # returned no id). Flag the fact so _reconcile_pending_vectors
                    # — which only selects vector_sync_pending = true — retries it
                    # on a later recall/consolidate. Without this the fact would
                    # keep weaviate_id=NULL with vector_sync_pending=false and stay
                    # invisible to semantic recall permanently.
                    await conn.execute(
                        "UPDATE public.memory_facts SET vector_sync_pending = true WHERE id = $1",
                        fact_uuid,
                    )

        return {
            "session_id": session_id,
            "status": "completed",
            "facts_extracted": len(stored_facts),
            "facts": stored_facts,
        }

    async def _mark_extraction_failed_durably(
        self, session_uuid: Any, exc: BaseException
    ) -> None:
        """Finish the terminal session write despite repeated caller cancellation."""
        task = asyncio.create_task(
            self._retry_extraction_failure_write(session_uuid, exc)
        )
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()

    async def _retry_extraction_failure_write(
        self, session_uuid: Any, exc: BaseException
    ) -> None:
        for _attempt in range(3):
            if await self._mark_extraction_failed(session_uuid, exc):
                return
            await asyncio.sleep(0)
        raise RuntimeError("memory extraction failure could not be persisted")

    async def _mark_extraction_failed(
        self, session_uuid: Any, exc: BaseException
    ) -> bool:
        try:
            async with self._acquire() as conn:
                await conn.execute(
                    """
                    UPDATE public.memory_sessions
                    SET status = 'failed', error_message = $1,
                        processing_completed_at = now()
                    WHERE id = $2 AND status = 'running'
                    """,
                    "Memory extraction failed",
                    session_uuid,
                )
            return True
        except Exception as persist_error:
            logger.error(
                "Memory extraction failure could not be persisted "
                "(session_id=%s, error_type=%s)",
                session_uuid,
                type(persist_error).__name__,
            )
            return False

    async def recall(
        self,
        user_id: str,
        query: str,
        namespace: Optional[str] = None,
        limit: int = 10,
        min_confidence: float = 0.5,
    ) -> Dict[str, Any]:
        """Recall relevant memories for a query."""
        self._check_enabled()
        await self._ensure_initialized()
        namespace = self._resolve_namespace(namespace)
        await self._reconcile_pending_vectors()

        # Search vector store for semantically similar memories
        similar = await self.store.search_similar(
            query=query, user_id=user_id, namespace=namespace, limit=limit
        )

        # Fetch full fact records from PostgreSQL
        async with self._acquire() as conn:
            memories = []
            for result in similar:
                pg_id = result.get("pg_fact_id")
                if not pg_id:
                    continue

                row = await conn.fetchrow(
                    """
                    SELECT id, content, fact_type, confidence, namespace,
                           is_active, created_at, updated_at, metadata
                    FROM public.memory_facts
                    WHERE id = $1 AND is_active = true AND confidence >= $2
                      AND user_id = $3 AND namespace = $4
                    """,
                    _to_uuid(pg_id),
                    min_confidence,
                    _to_uuid(user_id),
                    namespace,
                )
                if row:
                    memories.append(
                        {
                            "id": str(row["id"]),
                            "content": row["content"],
                            "fact_type": row["fact_type"],
                            "confidence": row["confidence"],
                            "namespace": row["namespace"],
                            "is_active": row["is_active"],
                            "created_at": row["created_at"].isoformat(),
                            "updated_at": row["updated_at"].isoformat(),
                            "metadata": json.loads(row["metadata"]) if isinstance(row["metadata"], str) else (row["metadata"] or {}),
                        }
                    )

        context_summary = None
        if memories:
            try:
                facts_text = "\n".join(
                    f"- {m['content']} ({m['fact_type']}, confidence: {m['confidence']})"
                    for m in memories
                )
                model = await self._get_extraction_model()
                context_summary = await self._litellm_complete(
                    model=model,
                    prompt=(
                        f"Given these remembered facts about the user:\n{facts_text}\n\n"
                        f"And their current query: \"{query}\"\n\n"
                        "Write a brief, natural summary of the relevant memories "
                        "(2-3 sentences max). Be concise and factual."
                    ),
                    timeout=30.0,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to generate context summary (error_type=%s)",
                    type(exc).__name__,
                )

        return {"memories": memories, "context_summary": context_summary}

    @staticmethod
    def _log_reconcile_halt(reconciled: int, exc: BaseException) -> None:
        """Explain why the reconcile stopped rather than retrying the backlog."""
        logger.warning(
            "Memory vector reconciliation deferred after %d row(s) (error_type=%s); "
            "the sync target is unavailable, so the remaining rows would fail "
            "identically",
            reconciled,
            type(exc).__name__,
        )

    @staticmethod
    async def _clear_pending_flag(conn, row, new_weaviate_id) -> None:
        """Clear vector_sync_pending, guarded on the updated_at we read.

        If a concurrent update_memory changed this fact after we read it — and
        re-flagged it for the NEW content — an unguarded clear would wipe that
        flag against our now-stale vector write, stranding the newer content
        unreconciled with the flag false (permanent divergence). Guarding makes
        the clear a no-op in that case, so a later pass picks it up.
        """
        await conn.execute(
            """
            UPDATE public.memory_facts
            SET vector_sync_pending = false,
                weaviate_id = COALESCE($2, weaviate_id),
                updated_at = now()
            WHERE id = $1 AND vector_sync_pending = true
              AND updated_at = $3
            """,
            row["id"],
            new_weaviate_id,
            row["updated_at"],
        )

    async def _sync_one_row(self, row):
        """Push one fact's vector, or retire it. Returns the new weaviate id.

        `is_active = false` rows are RETIREMENTS — a memory the user deleted —
        so they go through `deactivate_embedding`, not a write.
        """
        if not row["is_active"]:
            await self.store.deactivate_embedding(
                str(row["id"]), row.get("weaviate_id")
            )
            return None
        return await self.store.update_embedding(
            fact_id=str(row["id"]),
            content=row["content"],
            user_id=str(row["user_id"]),
            namespace=row["namespace"],
            fact_type=row["fact_type"],
            confidence=row["confidence"],
            weaviate_id=row.get("weaviate_id"),
        )

    async def _reconcile_pending_vectors(
        self,
        conn=None,
        *,
        retry_transient: bool = False,
    ) -> int:
        """Apply durable Postgres vector-retirement intents to Weaviate."""
        if not self.store:
            return 0
        owns_connection = conn is None
        if owns_connection:
            # #804 HOLD: dedicated connection — this loop holds `conn` across
            # Weaviate update/deactivate I/O below, so it must NOT draw from the
            # shared pool (would pin a bounded slot across slow I/O).
            conn = await connect_postgres(self.database_url)
        reconciled = 0
        target_failures_since_progress = 0
        try:
            rows = await conn.fetch(
                """
                SELECT id, user_id, namespace, content, fact_type, confidence,
                       is_active, weaviate_id, updated_at
                FROM public.memory_facts
                WHERE vector_sync_pending = true
                ORDER BY updated_at
                LIMIT 100
                """
            )
            for row in rows:
                new_weaviate_id = None
                try:
                    new_weaviate_id = await self._sync_one_row(row)
                except Exception as exc:
                    target_signal = _is_target_health_signal(exc)
                    if retry_transient and target_signal:
                        raise
                    # Log EVERY deferral — a row that could not be reconciled
                    # must stay observable, not just the eventual halt.
                    logger.warning(
                        "Memory vector reconciliation deferred (error_type=%s)",
                        type(exc).__name__,
                    )
                    if not target_signal:
                        # A row-specific failure says nothing about the
                        # target's health, so it must not COUNT toward the
                        # streak — and it must not RESET it either. Resetting
                        # let interleaved shapes defeat the cap entirely:
                        # `503,503,400` repeating, or `429,400` repeating,
                        # ran all 100 rows and never halted. Only PROGRESS
                        # proves the target is reachable, so only progress
                        # clears the counter.
                        continue
                    target_failures_since_progress += 1
                    if target_failures_since_progress >= _RECONCILE_TRANSIENT_STREAK:
                        self._log_reconcile_halt(reconciled, exc)
                        break
                    continue
                # Optimistic guard on updated_at (the same discipline the
                # consolidate state transitions use): if a concurrent
                # update_memory changed this fact after we read it — re-flagging
                # vector_sync_pending for the NEW content — an unguarded clear
                # here would wipe that flag against our now-stale vector write,
                # stranding the newer content unreconciled with the flag false
                # (permanent divergence). Guarding on the read updated_at makes
                # this clear a no-op in that case, so the fact stays pending and
                # a later pass reconciles the current content.
                await self._clear_pending_flag(conn, row, new_weaviate_id)
                reconciled += 1
                target_failures_since_progress = 0  # progress: the target is reachable
        finally:
            if owns_connection:
                await conn.close()
        return reconciled

    async def _expire_excess_facts(
        self, user_uuid: Any, retry_transient: bool
    ) -> int:
        """Apply the per-user retention limit across every namespace."""
        conn = await connect_postgres(self.database_url)
        expired_count = 0
        try:
            active_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM public.memory_facts
                WHERE user_id = $1 AND is_active = true
                """,
                user_uuid,
            )
            if active_count <= self.max_facts:
                return 0
            expired_rows = await conn.fetch(
                """
                SELECT id, updated_at FROM public.memory_facts
                WHERE user_id = $1 AND is_active = true
                ORDER BY updated_at ASC
                LIMIT $2
                """,
                user_uuid,
                active_count - self.max_facts,
            )
            for row in expired_rows:
                expired = await conn.fetchrow(
                    """
                    UPDATE public.memory_facts
                    SET is_active = false, expires_at = now(),
                        vector_sync_pending = true, updated_at = now()
                    WHERE id = $1 AND is_active = true
                      AND updated_at = $2
                    RETURNING id, weaviate_id
                    """,
                    row["id"],
                    row["updated_at"],
                )
                expired_count += int(expired is not None)
            await self._reconcile_pending_vectors(
                conn, retry_transient=retry_transient
            )
            return expired_count
        finally:
            await conn.close()

    async def consolidate(
        self,
        user_id: Optional[str] = None,
        *,
        retry_transient: bool = False,
    ) -> Dict[str, Any]:
        """Consolidate/deduplicate user memories.

        Worker callers opt into re-raising transient upstream failures so
        Celery can retry them. The synchronous API keeps the historical
        best-effort behavior and continues with the next user.
        """
        self._check_enabled()
        await self._ensure_initialized()
        await self._reconcile_pending_vectors(retry_transient=retry_transient)

        # The LLM call below (per-user) can take up to 60s; holding a
        # single conn across that pins one Postgres slot per concurrent
        # user request and starves the connection pool under any real
        # consolidate fan-out. Release the conn between DB-bound blocks
        # and re-acquire after the LLM round-trip; semantics are
        # preserved because asyncpg Records are detached and `facts` is
        # indexed by integer position.

        # Resolve namespace-isolated work groups under a brief connection.
        # Namespace is an access boundary for recall/list/summarize and must be
        # the same boundary for LLM-driven consolidation.
        async with self._acquire() as conn:
            if user_id:
                uid = _to_uuid(user_id)
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT namespace FROM public.memory_facts
                    WHERE user_id = $1 AND is_active = true
                    ORDER BY namespace
                    """,
                    uid,
                )
                groups = [(uid, row["namespace"]) for row in rows]
            else:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT user_id, namespace
                    FROM public.memory_facts
                    WHERE is_active = true
                    ORDER BY user_id, namespace
                    """
                )
                groups = [
                    (row["user_id"], row["namespace"]) for row in rows
                ]

        total_reviewed = 0
        total_merged = 0
        total_superseded = 0
        total_expired = 0

        for uid, group_namespace in groups:
            # Fetch facts under a brief connection; release before the
            # LLM call so the connection slot is free during the round-
            # trip (up to 60s per user).
            async with self._acquire() as conn:
                facts = await conn.fetch(
                    """
                    SELECT id, content, fact_type, confidence, namespace,
                           created_at, updated_at, metadata, weaviate_id
                    FROM public.memory_facts
                    WHERE user_id = $1 AND namespace = $2
                      AND is_active = true
                    ORDER BY created_at
                    """,
                    uid,
                    group_namespace,
                )

            total_reviewed += len(facts)

            if len(facts) < 2:
                continue

            # Use LLM to identify duplicates and contradictions
            facts_text = "\n".join(
                f"[{i}] ({row['fact_type']}) {row['content']}"
                for i, row in enumerate(facts)
            )

            try:
                model = await self._get_extraction_model()
                consolidation_prompt = (
                    "Review these memory facts and identify:\n"
                    "1. Duplicates (same information, different wording)\n"
                    "2. Contradictions (newer fact supersedes older one)\n"
                    "3. Facts that can be merged into one\n\n"
                    f"Facts:\n{facts_text}\n\n"
                    "Return a JSON array of actions. Each action:\n"
                    '{"action": "merge"|"supersede", '
                    '"source_indices": [int, int], '
                    '"keep_index": int, '
                    '"reason": "string"}\n'
                    "If no consolidation needed, return []."
                )
                response_text = await self._litellm_complete(
                    model=model,
                    prompt=consolidation_prompt,
                    json_mode=True,
                    timeout=60.0,
                ) or "[]"
                actions = json.loads(response_text)
                if isinstance(actions, dict) and "actions" in actions:
                    actions = actions["actions"]
                if not isinstance(actions, list):
                    actions = []
            except (
                TimeoutError,
                ConnectionError,
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as exc:
                if retry_transient:
                    raise
                logger.warning(
                    "Consolidation LLM call failed (error_type=%s)",
                    type(exc).__name__,
                )
                continue
            except Exception as exc:
                logger.warning(
                    "Consolidation LLM call failed (error_type=%s)",
                    type(exc).__name__,
                )
                continue

            # #804 HOLD: dedicated connection — the apply loop passes `conn`
            # into _reconcile_pending_vectors (Weaviate I/O) below, so it must
            # NOT draw from the shared pool. Keep it on connect_postgres.
            conn = await connect_postgres(self.database_url)  # HOLD
            try:
                for action_data in actions:
                    validated = _validate_consolidation_action(
                        action_data, len(facts)
                    )
                    if validated is None:
                        continue
                    action, source_indices, keep_index, reason = validated

                    keep_fact = facts[keep_index]
                    source_facts = [
                        facts[i] for i in source_indices if i != keep_index
                    ]

                    if not source_facts:
                        continue

                    source_fact_uuids = []
                    try:
                        async with conn.transaction():
                            # Deactivate every source and write its audit record
                            # as one unit. A failed/stale later UPDATE must roll
                            # back every earlier source in the same LLM action.
                            for source_fact in source_facts:
                                sfid = source_fact["id"]
                                deactivated = await conn.fetchrow(
                                    """
                                    UPDATE public.memory_facts
                                    SET is_active = false, superseded_by = $1,
                                        vector_sync_pending = true,
                                        updated_at = now()
                                    WHERE id = $2 AND is_active = true
                                      AND updated_at = $3
                                      AND namespace = $5
                                      AND EXISTS (
                                          SELECT 1
                                          FROM public.memory_facts keeper
                                          WHERE keeper.id = $1
                                            AND keeper.is_active = true
                                            AND keeper.updated_at = $4
                                            AND keeper.namespace = $5
                                      )
                                    RETURNING id, weaviate_id
                                    """,
                                    keep_fact["id"],
                                    sfid,
                                    source_fact["updated_at"],
                                    keep_fact["updated_at"],
                                    group_namespace,
                                )
                                if not deactivated:
                                    raise _StaleConsolidationAction
                                source_fact_uuids.append(deactivated["id"])

                            await conn.execute(
                                """
                                INSERT INTO public.memory_consolidation_log
                                    (user_id, action, source_fact_ids,
                                     result_fact_id, reason)
                                VALUES ($1, $2, $3, $4, $5)
                                """,
                                uid,
                                {"merge": "merged", "supersede": "superseded"}[
                                    action
                                ],
                                source_fact_uuids,
                                keep_fact["id"],
                                reason,
                            )
                    except _StaleConsolidationAction:
                        continue

                    if action == "merge":
                        total_merged += len(source_fact_uuids)
                    else:
                        total_superseded += len(source_fact_uuids)
                    await self._reconcile_pending_vectors(
                        conn, retry_transient=retry_transient
                    )

            finally:
                await conn.close()  # HOLD (see above)

        for uid in dict.fromkeys(group_uid for group_uid, _namespace in groups):
            total_expired += await self._expire_excess_facts(
                uid, retry_transient
            )

        return {
            "user_id": user_id,
            "facts_reviewed": total_reviewed,
            "facts_merged": total_merged,
            "facts_superseded": total_superseded,
            "facts_expired": total_expired,
        }

    async def summarize(
        self, user_id: str, namespace: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate a natural-language user memory profile."""
        self._check_enabled()
        await self._ensure_initialized()
        namespace = self._resolve_namespace(namespace)
        user_uuid = _to_uuid(user_id)

        async with self._acquire() as conn:
            facts = await conn.fetch(
                """
                SELECT content, fact_type, confidence
                FROM public.memory_facts
                WHERE user_id = $1 AND namespace = $2 AND is_active = true
                ORDER BY confidence DESC, updated_at DESC
                LIMIT 50
                """,
                user_uuid,
                namespace,
            )

            total = await conn.fetchval(
                """
                SELECT COUNT(*) FROM public.memory_facts
                WHERE user_id = $1 AND namespace = $2 AND is_active = true
                """,
                user_uuid,
                namespace,
            )

            if not facts:
                return {
                    "user_id": user_id,
                    "summary": "No memories stored for this user yet.",
                    "total_facts": 0,
                }

        facts_text = "\n".join(
            f"- [{row['fact_type']}] {row['content']} (confidence: {row['confidence']})"
            for row in facts
        )
        try:
            model = await self._get_extraction_model()
            summary = await self._litellm_complete(
                model=model,
                prompt=(
                    "Based on these remembered facts about a user, "
                    "write a concise profile summary (3-5 sentences):\n\n"
                    f"{facts_text}\n\n"
                    "Write a natural, helpful summary:"
                ),
                timeout=30.0,
            ) or "Unable to generate summary."
        except Exception as exc:
            logger.warning(
                "Summary generation failed (error_type=%s)",
                type(exc).__name__,
            )
            summary = f"User has {total} stored memories across various topics."

        return {
            "user_id": user_id,
            "summary": summary,
            "total_facts": total,
        }

    async def list_memories(
        self,
        user_id: str,
        namespace: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List all memories for a user."""
        self._check_enabled()
        namespace = self._resolve_namespace(namespace)
        user_uuid = _to_uuid(user_id)

        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, content, fact_type, confidence, namespace,
                       is_active, created_at, updated_at, metadata
                FROM public.memory_facts
                WHERE user_id = $1 AND namespace = $2 AND is_active = true
                ORDER BY updated_at DESC
                LIMIT $3 OFFSET $4
                """,
                user_uuid,
                namespace,
                limit,
                offset,
            )

            total = await conn.fetchval(
                """
                SELECT COUNT(*) FROM public.memory_facts
                WHERE user_id = $1 AND namespace = $2 AND is_active = true
                """,
                user_uuid,
                namespace,
            )

            memories = [
                {
                    "id": str(row["id"]),
                    "content": row["content"],
                    "fact_type": row["fact_type"],
                    "confidence": row["confidence"],
                    "namespace": row["namespace"],
                    "is_active": row["is_active"],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                    "metadata": json.loads(row["metadata"]) if isinstance(row["metadata"], str) else (row["metadata"] or {}),
                }
                for row in rows
            ]

            return {
                "user_id": user_id,
                "memories": memories,
                "total": total,
            }


    async def update_memory(
        self, memory_id: str, user_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a specific memory fact."""
        self._check_enabled()
        await self._ensure_initialized()

        memory_uuid = _to_uuid(memory_id)
        user_uuid = _to_uuid(user_id)
        # #804 HOLD: dedicated connection — passes `conn` into
        # _reconcile_pending_vectors (Weaviate I/O) below; must not use the pool.
        conn = await connect_postgres(self.database_url)  # HOLD
        try:
            # Check if fact exists
            row = await conn.fetchrow(
                "SELECT * FROM public.memory_facts WHERE id = $1 AND user_id = $2",
                memory_uuid,
                user_uuid,
            )
            if not row:
                return None

            # Build update query dynamically
            set_clauses = ["updated_at = now()"]
            params = []
            param_idx = 1

            for field in ("content", "fact_type", "confidence", "is_active"):
                if field in updates and updates[field] is not None:
                    set_clauses.append(f"{field} = ${param_idx}")
                    params.append(updates[field])
                    param_idx += 1

            if "metadata" in updates and updates["metadata"] is not None:
                set_clauses.append(f"metadata = ${param_idx}")
                params.append(json.dumps(updates["metadata"]))
                param_idx += 1

            vector_fields = {"content", "fact_type", "confidence", "is_active"}
            needs_vector_sync = any(
                field in updates and updates[field] is not None
                for field in vector_fields
            )
            if needs_vector_sync:
                set_clauses.append("vector_sync_pending = true")

            params.append(memory_uuid)
            params.append(user_uuid)
            query = (
                f"UPDATE public.memory_facts SET {', '.join(set_clauses)} "
                f"WHERE id = ${param_idx} AND user_id = ${param_idx + 1} RETURNING *"
            )

            updated = await conn.fetchrow(query, *params)
            if not updated:
                return None

            if needs_vector_sync:
                await self._reconcile_pending_vectors(conn)

            return {
                "id": str(updated["id"]),
                "content": updated["content"],
                "fact_type": updated["fact_type"],
                "confidence": updated["confidence"],
                "namespace": updated["namespace"],
                "is_active": updated["is_active"],
                "created_at": updated["created_at"].isoformat(),
                "updated_at": updated["updated_at"].isoformat(),
                "metadata": json.loads(updated["metadata"]) if isinstance(updated["metadata"], str) else (updated["metadata"] or {}),
            }

        finally:
            await conn.close()  # HOLD (see above)

    async def delete_memory(self, memory_id: str, user_id: str) -> bool:
        """Soft-delete a memory fact (set is_active=false)."""
        self._check_enabled()
        await self._ensure_initialized()

        memory_uuid = _to_uuid(memory_id)
        user_uuid = _to_uuid(user_id)
        # #804 HOLD: dedicated connection — passes `conn` into
        # _reconcile_pending_vectors (Weaviate I/O) below; must not use the pool.
        conn = await connect_postgres(self.database_url)  # HOLD
        try:
            deleted = await conn.fetchrow(
                """
                UPDATE public.memory_facts
                SET is_active = false, vector_sync_pending = true,
                    updated_at = now()
                WHERE id = $1 AND user_id = $2
                RETURNING id
                """,
                memory_uuid,
                user_uuid,
            )
            if not deleted:
                return False
            await self._reconcile_pending_vectors(conn)

            return True

        finally:
            await conn.close()  # HOLD (see above)

    async def health_check(self) -> Dict[str, Any]:
        """Check memory service health."""
        if not self.enabled:
            return {
                "status": "disabled",
                "vector_backend": "none",
                "facts_count": 0,
                "enabled": False,
            }

        try:
            await self._ensure_initialized()

            async with self._acquire() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM public.memory_facts WHERE is_active = true"
                )

            return {
                "status": "healthy",
                "vector_backend": self.store.backend if self.store else "unknown",
                "facts_count": count,
                "enabled": True,
            }
        except Exception as exc:
            logger.warning(
                "Memory health check failed (error_type=%s)",
                type(exc).__name__,
            )
            return {
                "status": "unhealthy",
                "vector_backend": "unknown",
                "facts_count": 0,
                "enabled": True,
                "error": "Memory service is unavailable",
            }
