"""
Dual vector backend for LangMem memory storage.

Supports an explicitly latched Weaviate or pgvector backend. Initial Weaviate
failure selects authoritative pgvector; failback happens only through an
explicit readiness probe.
"""

import os
import asyncio
import logging
from typing import Optional, List, Dict, Any, Union
from uuid import UUID

import httpx

from db_connection import acquire_conn


def _to_uuid(value: Union[str, UUID, None]) -> Optional[UUID]:
    """Convert a string or UUID to a UUID object, or return None."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(value)

logger = logging.getLogger("memory_store")

WEAVIATE_COLLECTION_NAME = "Memory"
MAX_PGVECTOR_DIMENSION = 4000
MAX_FAILBACK_REBUILD_ATTEMPTS = 3


class FailbackGenerationChurn(RuntimeError):
    """Raised when authoritative writes prevent a stable failback snapshot."""


class MemoryEmbeddingWriteSuperseded(RuntimeError):
    """Raised when canonical fact content changed before its vector write."""


class MemoryEmbeddingBackfillSuperseded(RuntimeError):
    """Raised when a newer model/dimension target replaces this backfill."""


def _parse_embedding_dimension(value: Union[str, int, None]) -> int:
    raw = "768" if value is None else value
    try:
        dimension = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "LANGMEM_EMBEDDING_DIM must be an integer from 1 through 4000"
        ) from exc
    if not 1 <= dimension <= MAX_PGVECTOR_DIMENSION:
        raise ValueError(
            "LANGMEM_EMBEDDING_DIM must be an integer from 1 through 4000"
        )
    return dimension


def _weaviate_target_unavailable(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.NetworkError),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return status is not None and (status >= 500 or status in {401, 408, 429})
    return False


class MemoryStore:
    """Abstraction over Weaviate and pgvector for memory vector storage."""

    def __init__(
        self,
        database_url: str,
        weaviate_url: Optional[str] = None,
        litellm_url: Optional[str] = None,
        litellm_api_key: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_dimension: Union[str, int, None] = None,
        manage_schema: bool = False,
    ):
        self.database_url = database_url
        self.weaviate_url = weaviate_url
        self.litellm_url = litellm_url or "http://litellm:4000"
        self.litellm_api_key = litellm_api_key or os.getenv("LITELLM_API_KEY", "")
        # Embedding model identifier in LiteLLM's model_list
        # (e.g. "ollama/nomic-embed-text", "openai/text-embedding-3-small").
        self.embedding_model = embedding_model or os.getenv(
            "LITELLM_EMBEDDING_MODEL", "ollama/nomic-embed-text"
        )
        self.embedding_dimension = _parse_embedding_dimension(
            embedding_dimension
            if embedding_dimension is not None
            else os.getenv("LANGMEM_EMBEDDING_DIM", "768")
        )
        self._pgvector_generation = 1
        self.manage_schema = manage_schema
        self.backend: Optional[str] = None  # "weaviate" or "pgvector"
        self.weaviate_state = "disabled" if not weaviate_url else "unprobed"
        self.weaviate_state_reason = (
            "not_configured" if not weaviate_url else "not_probed"
        )
        self._weaviate_client = None
        self._initialized = False
        # Self-guards the one-shot init so the destructive collection heal
        # in _ensure_weaviate_collection can never race itself, regardless
        # of caller. (MemoryService also serializes its single init call,
        # but a direct MemoryStore user would otherwise be unprotected.)
        self._init_lock = asyncio.Lock()
        # A probe may perform readiness I/O and a full secondary rebuild, so it
        # has its own long-lived serialization lock. Backend state changes use
        # a separate short mutex; no database, embedding-provider, Weaviate, or
        # search I/O is allowed while that mutex is held.
        self._probe_run_lock = asyncio.Lock()
        self._transition_lock = asyncio.Lock()

    async def initialize(self):
        """Detect available vector backend and initialize."""
        if self._initialized:
            return

        async with self._init_lock:
            # Double-check under the lock: a concurrent caller may have
            # completed init (incl. the collection heal) while we waited.
            if self._initialized:
                return

            if self.manage_schema:
                probe_embedding = await self._generate_embedding(
                    "Atlas embedding dimension contract probe"
                )
                self._validate_embedding(probe_embedding)
                await self._ensure_pgvector_schema_contract()
            if self.weaviate_url:
                await self._probe_weaviate(explicit=False)
            else:
                self.backend = "pgvector"
                self.weaviate_state = "disabled"
                self.weaviate_state_reason = "not_configured"
                await self._mark_weaviate_dirty()
            logger.info(
                "Memory store initialized (backend=%s, weaviate_state=%s, reason=%s)",
                self.backend,
                self.weaviate_state,
                self.weaviate_state_reason,
            )
            self._initialized = True

    async def _probe_weaviate(self, *, explicit: bool) -> bool:
        """Probe Weaviate and atomically transition the latched backend."""
        async with self._probe_run_lock:
            if not self.weaviate_url:
                async with self._transition_lock:
                    self.backend = "pgvector"
                    self.weaviate_state = "disabled"
                    self.weaviate_state_reason = "not_configured"
                return False
            async with self._transition_lock:
                already_ready = (
                    self.backend == "weaviate" and self.weaviate_state == "ready"
                )
            if explicit and already_ready:
                if not self.manage_schema:
                    return True
                rebuild_required, _generation = (
                    await self._get_weaviate_sync_state()
                )
                if not rebuild_required:
                    return True
            try:
                await self._probe_weaviate_ready()
                # Readiness alone is insufficient after a pgvector-authoritative
                # outage: rebuild every active object and retirement before the
                # latch can expose Weaviate searches again.
                rebuild_required, generation = await self._get_weaviate_sync_state()
                if rebuild_required:
                    for _attempt in range(MAX_FAILBACK_REBUILD_ATTEMPTS):
                        await self._sync_weaviate_from_postgres()
                        if await self._complete_weaviate_rebuild(generation):
                            break
                        rebuild_required, generation = (
                            await self._get_weaviate_sync_state()
                        )
                    else:
                        raise FailbackGenerationChurn(
                            "Weaviate failback could not reach a stable generation"
                        )
            except Exception as exc:
                reason = (
                    "failback_generation_churn"
                    if isinstance(exc, FailbackGenerationChurn)
                    else (
                        "explicit_probe_failed" if explicit else "initial_probe_failed"
                    )
                )
                try:
                    await self._mark_weaviate_dirty()
                except Exception as marker_exc:
                    async with self._transition_lock:
                        self.backend = None
                        self.weaviate_state = "unavailable"
                        self.weaviate_state_reason = reason
                    raise RuntimeError(
                        "Cannot durably select pgvector fallback"
                    ) from marker_exc
                async with self._transition_lock:
                    self.backend = "pgvector"
                    self.weaviate_state = "unavailable"
                    self.weaviate_state_reason = reason
                logger.warning(
                    "Weaviate probe failed; pgvector remains authoritative "
                    "(reason=%s, error_type=%s)",
                    self.weaviate_state_reason,
                    type(exc).__name__,
                )
                return False
            # The rebuild CAS may have succeeded just before another process
            # made pgvector authoritative. Re-read durable state immediately
            # before the local latch; every subsequent Weaviate operation also
            # performs the same shared-state precheck.
            if self.manage_schema:
                rebuild_required, _generation = await self._get_weaviate_sync_state(
                    include_pending=False
                )
                if rebuild_required:
                    async with self._transition_lock:
                        self.backend = "pgvector"
                        self.weaviate_state = "unavailable"
                        self.weaviate_state_reason = "secondary_dirty_detected"
                    return False
            async with self._transition_lock:
                self.backend = "weaviate"
                self.weaviate_state = "ready"
                self.weaviate_state_reason = (
                    "explicit_probe_succeeded" if explicit else "initial_probe_succeeded"
                )
            return True

    async def _probe_weaviate_ready(self) -> None:
        force_recreate = await self._ensure_weaviate_identity()
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{self.weaviate_url}/v1/.well-known/ready"
            )
            if response.status_code != 200:
                raise RuntimeError("Weaviate readiness probe failed")
        await self._ensure_weaviate_collection(force_recreate=force_recreate)

    async def _sync_weaviate_from_postgres(self) -> None:
        """Rebuild Weaviate from authoritative Postgres before failback."""
        last_id: Optional[UUID] = None
        while True:
            # Do not hold a database connection across external Weaviate I/O.
            async with acquire_conn(self.database_url) as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, namespace, content, fact_type,
                           confidence, is_active, weaviate_id, updated_at
                    FROM public.memory_facts
                    WHERE ($1::uuid IS NULL OR id > $1::uuid)
                    ORDER BY id
                    LIMIT 100
                    """,
                    last_id,
                )
            if not rows:
                return
            for row in rows:
                fact_id = str(row["id"])
                if row["is_active"]:
                    weaviate_id = await self._store_weaviate(
                        fact_id,
                        row["content"],
                        str(row["user_id"]),
                        row["namespace"],
                        row["fact_type"],
                        row["confidence"],
                    )
                    await self._delete_stale_weaviate_objects(
                        fact_id, keep_id=weaviate_id
                    )
                else:
                    await self._deactivate_weaviate(
                        fact_id, row.get("weaviate_id") or fact_id
                    )
                # Drain the exact durable intent just applied. If a concurrent
                # edit changes updated_at, this guarded clear is a no-op and
                # the generation CAS forces another rebuild pass.
                async with acquire_conn(self.database_url) as conn:
                    await conn.execute(
                        """
                        UPDATE public.memory_facts
                        SET vector_sync_pending = false,
                            weaviate_id = CASE WHEN is_active THEN $2 ELSE weaviate_id END
                        WHERE id = $1 AND updated_at = $3
                        """,
                        row["id"],
                        fact_id if row["is_active"] else row.get("weaviate_id"),
                        row["updated_at"],
                    )
                last_id = row["id"]

    async def _get_weaviate_sync_state(
        self, *, include_pending: bool = True
    ) -> tuple[bool, int]:
        if not self.manage_schema:
            return True, 0
        pending_clause = """
            OR EXISTS (
                SELECT 1 FROM public.memory_facts
                 WHERE vector_sync_pending = true
            )
        """ if include_pending else ""
        async with acquire_conn(self.database_url) as conn:
            row = await conn.fetchrow(
                f"""
                SELECT (
                    weaviate_rebuild_required {pending_clause}
                ) AS rebuild_required,
                weaviate_dirty_generation
                FROM public.memory_embedding_schema_state
                WHERE singleton = true
                """
            )
        if row is None:
            raise RuntimeError("Memory embedding schema state is missing")
        return bool(row["rebuild_required"]), int(row["weaviate_dirty_generation"])

    async def _mark_weaviate_dirty(self) -> None:
        if not self.manage_schema:
            return
        async with acquire_conn(self.database_url) as conn:
            await conn.fetchval("SELECT public.mark_memory_weaviate_dirty()")

    async def _ensure_weaviate_identity(self) -> bool:
        """Persist the full secondary contract and report stale synced identity."""
        if not self.manage_schema:
            return False
        async with acquire_conn(self.database_url) as conn:
            row = await conn.fetchrow(
                """
                WITH ensured AS MATERIALIZED (
                    SELECT public.ensure_memory_weaviate_identity($1, $2)
                )
                SELECT weaviate_synced_model, weaviate_synced_dimension
                FROM public.memory_embedding_schema_state, ensured
                WHERE singleton = true
                """,
                self.embedding_model,
                self.embedding_dimension,
            )
        if row is None:
            raise RuntimeError("Memory embedding schema state is missing")
        return (
            row["weaviate_synced_model"] != self.embedding_model
            or row["weaviate_synced_dimension"] != self.embedding_dimension
        )

    async def _complete_weaviate_rebuild(self, generation: int) -> bool:
        if not self.manage_schema:
            return True
        async with acquire_conn(self.database_url) as conn:
            return bool(
                await conn.fetchval(
                    "SELECT public.complete_memory_weaviate_rebuild($1, $2, $3)",
                    generation,
                    self.embedding_model,
                    self.embedding_dimension,
                )
            )

    async def _latch_pgvector_after_runtime_failure(self, exc: BaseException) -> None:
        # Persist before exposing pgvector success. Otherwise another request
        # can observe fallback while a failed marker write still leaves a
        # restart able to select stale Weaviate. The durable write deliberately
        # happens outside the short state mutex.
        try:
            await self._mark_weaviate_dirty()
        except Exception:
            # Keep the prior latch: pgvector must never become observable when
            # the durable fallback intent could not be recorded.
            raise
        async with self._transition_lock:
            self.backend = "pgvector"
            self.weaviate_state = "unavailable"
            self.weaviate_state_reason = "runtime_operation_failed"
        logger.warning(
            "Weaviate runtime operation failed; pgvector latched authoritative "
            "until an explicit probe (error_type=%s)",
            type(exc).__name__,
        )

    async def _clean_weaviate_generation(self) -> Optional[int]:
        """Fail closed when another process made Postgres authoritative.

        The durable marker is shared across backend processes. A process that
        still has a healthy local Weaviate latch must therefore consult it
        before serving recall; otherwise a pgvector write made by a different
        process could remain missing from Weaviate until this process restarts.
        """
        async with self._transition_lock:
            backend = self.backend
        if not self.manage_schema or backend != "weaviate":
            return 0
        rebuild_required, generation = await self._get_weaviate_sync_state(
            include_pending=False
        )
        if not rebuild_required:
            return generation
        async with self._transition_lock:
            if self.backend != "weaviate":
                return None
            self.backend = "pgvector"
            self.weaviate_state = "unavailable"
            self.weaviate_state_reason = "secondary_dirty_detected"
            logger.warning(
                "Durable Weaviate dirty marker detected; pgvector latched "
                "authoritative until an explicit probe"
            )
            return None

    async def _weaviate_mutation_crossed_generation(
        self, expected_generation: int
    ) -> bool:
        """Detect a shared backend transition across external mutation I/O."""
        async with self._transition_lock:
            backend = self.backend
        if not self.manage_schema:
            return backend != "weaviate"
        rebuild_required, generation = await self._get_weaviate_sync_state(
            include_pending=False
        )
        return (
            backend != "weaviate"
            or rebuild_required
            or generation != expected_generation
        )

    async def _latch_pgvector_for_generation_change(self) -> None:
        async with self._transition_lock:
            self.backend = "pgvector"
            self.weaviate_state = "unavailable"
            self.weaviate_state_reason = "secondary_dirty_detected"

    async def probe_weaviate(self) -> bool:
        """Explicitly re-probe and, on success, fail back to Weaviate."""
        await self.initialize()
        return await self._probe_weaviate(explicit=True)

    def vector_backend_state(self) -> Dict[str, Any]:
        return {
            "backend": self.backend or "uninitialized",
            "weaviate_state": self.weaviate_state,
            "reason": self.weaviate_state_reason,
            "embedding_dimension": self.embedding_dimension,
        }

    def _validate_embedding(self, embedding: List[float]) -> None:
        actual = len(embedding)
        if actual != self.embedding_dimension:
            raise ValueError(
                f"Embedding model returned {actual} dimensions, but the configured "
                f"schema dimension is {self.embedding_dimension}"
            )

    def _distance_expression(self, parameter: str = "$1") -> str:
        dimension = self.embedding_dimension
        cast = "vector" if dimension <= 2000 else "halfvec"
        return f"embedding::{cast}({dimension}) <=> {parameter}::{cast}({dimension})"

    async def _ensure_pgvector_schema_contract(self) -> None:
        """Resume an optimistic backfill without holding DB across provider I/O."""
        async with acquire_conn(self.database_url) as conn:
            state = await conn.fetchrow(
                """
                SELECT active_dimension, target_dimension, phase,
                       pgvector_active_model, pgvector_target_model,
                       pgvector_active_generation, pgvector_target_generation
                FROM public.memory_embedding_schema_state
                WHERE singleton = true
                """
            )
            if not state:
                raise RuntimeError("Memory embedding schema state is missing")
            if state["target_dimension"] != self.embedding_dimension:
                raise RuntimeError(
                    "Memory embedding schema target does not match "
                    "LANGMEM_EMBEDDING_DIM"
                )
            if state["pgvector_target_model"] != self.embedding_model:
                raise RuntimeError(
                    "Memory embedding schema target model does not match "
                    "the effective embedding model"
                )
            target_generation = int(state["pgvector_target_generation"])
            if state["phase"] == "ready":
                if state["active_dimension"] != self.embedding_dimension:
                    raise RuntimeError("Memory embedding schema ready state is inconsistent")
                if (
                    state["pgvector_active_model"] != self.embedding_model
                    or state["pgvector_active_generation"] != target_generation
                ):
                    raise RuntimeError(
                        "Memory embedding schema ready model identity is inconsistent"
                    )
                self._pgvector_generation = target_generation
                return

        # Multiple backend replicas may generate the same row concurrently.
        # The content + dimension guard makes the short write idempotent: a
        # losing replica never overwrites newer content or already-correct work.
        while True:
            async with acquire_conn(self.database_url) as conn:
                current_target = await conn.fetchrow(
                    """
                    SELECT target_dimension, pgvector_target_model,
                           pgvector_target_generation
                    FROM public.memory_embedding_schema_state
                    WHERE singleton = true
                    """
                )
                if (
                    not current_target
                    or current_target["target_dimension"] != self.embedding_dimension
                    or current_target["pgvector_target_model"] != self.embedding_model
                    or int(current_target["pgvector_target_generation"])
                    != target_generation
                ):
                    raise MemoryEmbeddingBackfillSuperseded(
                        "Memory embedding backfill target was superseded"
                    )
                rows = await conn.fetch(
                    f"""
                    SELECT id, content
                    FROM public.memory_facts
                    WHERE embedding IS NULL
                       OR vector_dims(embedding) <> {self.embedding_dimension}
                       OR embedding_model IS DISTINCT FROM $1
                       OR embedding_generation <> {target_generation}
                    ORDER BY id
                    LIMIT 100
                    """,
                    self.embedding_model,
                )
            if not rows:
                break
            for row in rows:
                # The potentially slow external call deliberately happens with
                # no pooled connection or advisory lock checked out.
                embedding = await self._generate_embedding(row["content"])
                self._validate_embedding(embedding)
                async with acquire_conn(self.database_url) as conn:
                    result = await conn.execute(
                        f"""
                        UPDATE public.memory_facts
                        SET embedding = $1::vector({self.embedding_dimension}),
                            embedding_model = $4,
                            embedding_generation = {target_generation}
                        WHERE id = $2 AND content = $3
                          AND (embedding IS NULL OR
                               vector_dims(embedding) <> {self.embedding_dimension} OR
                               embedding_model IS DISTINCT FROM $4 OR
                               embedding_generation <> {target_generation})
                          AND EXISTS (
                              SELECT 1
                              FROM public.memory_embedding_schema_state
                              WHERE singleton = true
                                AND target_dimension = {self.embedding_dimension}
                                AND pgvector_target_model = $4
                                AND pgvector_target_generation = {target_generation}
                          )
                        """,
                        str(embedding),
                        row["id"],
                        row["content"],
                        self.embedding_model,
                    )
                if result == "UPDATE 0":
                    logger.debug(
                        "Memory embedding backfill row changed or was completed "
                        "by another replica (id=%s)",
                        row["id"],
                    )

        async with acquire_conn(self.database_url) as conn:
            await conn.execute(
                "SELECT public.contract_memory_embedding_contract($1, $2, $3)",
                self.embedding_model,
                self.embedding_dimension,
                target_generation,
            )
        self._pgvector_generation = target_generation

    async def _ensure_weaviate_collection(self, *, force_recreate: bool = False):
        """Ensure the collection exactly matches the configured vector contract."""
        expected_model = self.embedding_model.split("/", 1)[-1]
        expected_base_url = self.litellm_url.rstrip("/").removesuffix("/v1")
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Check if collection exists
            resp = await client.get(
                f"{self.weaviate_url}/v1/schema/{WEAVIATE_COLLECTION_NAME}"
            )
            if resp.status_code == 200:
                try:
                    existing = resp.json()
                    mod = (existing.get("moduleConfig") or {}).get("text2vec-openai") or {}
                    actual_model = mod.get("model")
                    actual_base_url = (mod.get("baseURL") or "").rstrip("/")
                    actual_base_url = actual_base_url.removesuffix("/v1")
                    replace = (
                        force_recreate
                        or existing.get("vectorizer") != "text2vec-openai"
                        or actual_model != expected_model
                        or actual_base_url != expected_base_url
                    )
                    if replace:
                        # The pinned Weaviate 1.38.13 class vectorizer config is
                        # immutable. Persist dirty intent before destructive
                        # replacement so a crash can never expose an empty,
                        # apparently synchronized secondary.
                        await self._mark_weaviate_dirty()
                        logger.warning(
                            "Memory collection vector contract changed; "
                            "deleting and recreating it before rebuild",
                        )
                        delete_response = await client.delete(
                            f"{self.weaviate_url}/v1/schema/{WEAVIATE_COLLECTION_NAME}"
                        )
                        if delete_response.status_code not in (200, 204):
                            raise RuntimeError("Weaviate collection replacement failed")
                    else:
                        return  # Already exists and healthy
                except Exception:
                    raise RuntimeError("Weaviate collection validation failed") from None
            elif resp.status_code != 404:
                raise RuntimeError("Weaviate collection inspection failed")
            else:
                # Collection absence is itself a dirty transition. Record it
                # before creating the empty class so clean DB identity cannot
                # accidentally skip the authoritative rebuild.
                await self._mark_weaviate_dirty()

            # Create collection
            schema = {
                "class": WEAVIATE_COLLECTION_NAME,
                "description": "LangMem persistent memory facts",
                "vectorizer": "text2vec-openai",
                "moduleConfig": {
                    "text2vec-openai": {
                        # Strip the LiteLLM provider prefix (text2vec-openai
                        # expects an OpenAI-style model name; LiteLLM resolves
                        # the actual provider from its model_list).
                        "model": expected_model,
                        # No /v1 suffix: Weaviate's openai module joins
                        # "/v1/embeddings" onto baseURL itself, so a /v1
                        # here produced /v1/v1/embeddings → 404 on every
                        # Weaviate-backed memory insert/search. Normalize so
                        # an operator's LITELLM_BASE_URL ending in /v1 (or
                        # /v1/) doesn't reintroduce it — otherwise the
                        # retroactive heal below flags the class as broken,
                        # deletes + recreates an identical broken class on
                        # every boot (self-defeating loop).
                        "baseURL": expected_base_url,
                        "vectorizeClassName": False,
                    }
                },
                "properties": [
                    {
                        "name": "content",
                        "dataType": ["text"],
                        "description": "Memory fact content",
                    },
                    {
                        "name": "userId",
                        "dataType": ["text"],
                        "description": "User ID who owns this memory",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                    {
                        "name": "namespace",
                        "dataType": ["text"],
                        "description": "Memory namespace",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                    {
                        "name": "factType",
                        "dataType": ["text"],
                        "description": "Type of fact",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                    {
                        "name": "confidence",
                        "dataType": ["number"],
                        "description": "Confidence score",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                    {
                        "name": "pgFactId",
                        "dataType": ["text"],
                        "description": "Reference to PostgreSQL memory_facts.id",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                    {
                        "name": "isActive",
                        "dataType": ["boolean"],
                        "description": "Whether the memory is active",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                ],
            }
            resp = await client.post(
                f"{self.weaviate_url}/v1/schema", json=schema
            )
            if resp.status_code in (200, 201):
                logger.info("Created Weaviate Memory collection")
            else:
                raise RuntimeError("Weaviate collection creation failed")

    async def _generate_embedding(self, text: str) -> List[float]:
        """Generate an embedding vector through the LiteLLM gateway.

        Uses LiteLLM's OpenAI-compatible /v1/embeddings endpoint. The model
        identifier should already include LiteLLM's provider prefix (e.g.
        "ollama/nomic-embed-text") so LiteLLM can route to the right upstream.
        """
        headers = {}
        if self.litellm_api_key:
            headers["Authorization"] = f"Bearer {self.litellm_api_key}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.litellm_url}/v1/embeddings",
                json={"model": self.embedding_model, "input": text},
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

    async def store_embedding(
        self,
        fact_id: str,
        content: str,
        user_id: str,
        namespace: str,
        fact_type: str,
        confidence: float,
        metadata: Dict[str, Any],
    ) -> Optional[str]:
        """Store an embedding for a memory fact. Returns weaviate_id or None."""
        await self.initialize()
        async with self._transition_lock:
            backend = self.backend
        if backend != "weaviate":
            # If a failback races this write after the snapshot, this
            # authoritative pgvector transaction advances the shared dirty
            # generation and invalidates the failback CAS.
            await self._store_pgvector(fact_id, content)
            return None

        clean_generation = await self._clean_weaviate_generation()
        if clean_generation is None:
            await self._store_pgvector(fact_id, content)
            return None
        try:
            weaviate_id = await self._store_weaviate(
                fact_id, content, user_id, namespace, fact_type, confidence
            )
        except Exception as exc:
            if not _weaviate_target_unavailable(exc):
                raise
            await self._latch_pgvector_after_runtime_failure(exc)
            await self._store_pgvector(fact_id, content)
            return None
        # Weaviate is the serving backend, but pgvector must remain a current
        # shadow so a later outage can latch it without missing facts from the
        # healthy-Weaviate interval. This write deliberately does not dirty the
        # secondary generation. If it fails, propagate: the caller must leave
        # vector_sync_pending=true and reconciliation will retry both writes.
        await self._store_pgvector(fact_id, content, mark_dirty=False)
        if await self._weaviate_mutation_crossed_generation(clean_generation):
            await self._mark_weaviate_dirty()
            await self._latch_pgvector_for_generation_change()
            return None
        return weaviate_id

    async def _store_weaviate(
        self,
        fact_id: str,
        content: str,
        user_id: str,
        namespace: str,
        fact_type: str,
        confidence: float,
    ) -> str:
        """Store embedding in Weaviate."""
        obj = {
            "class": WEAVIATE_COLLECTION_NAME,
            "id": fact_id,
            "properties": {
                "content": content,
                "userId": user_id,
                "namespace": namespace,
                "factType": fact_type,
                "confidence": confidence,
                "pgFactId": fact_id,
                "isActive": True,
            },
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.weaviate_url}/v1/objects", json=obj
            )
            if resp.status_code == 422:
                replacement = await client.put(
                    f"{self.weaviate_url}/v1/objects/"
                    f"{WEAVIATE_COLLECTION_NAME}/{fact_id}",
                    json=obj,
                )
                replacement.raise_for_status()
            else:
                resp.raise_for_status()
            return fact_id

    async def _store_pgvector(
        self, fact_id: str, content: str, *, mark_dirty: bool = True
    ) -> None:
        """Store pgvector, optionally declaring it the authoritative target."""
        embedding = await self._generate_embedding(content)
        self._validate_embedding(embedding)
        # #804: embedding already generated above (no connection held across
        # that I/O), so this short UPDATE/SELECT draws from the shared pool.
        target_guard = ""
        if self.manage_schema:
            target_guard = (
                " AND EXISTS (SELECT 1 FROM public.memory_embedding_schema_state "
                "WHERE singleton = true "
                f"AND target_dimension = {self.embedding_dimension} "
                "AND pgvector_target_model = $4 "
                f"AND pgvector_target_generation = {self._pgvector_generation})"
            )
        async with acquire_conn(self.database_url) as conn:
            async with conn.transaction():
                # Advance the monotonic generation and write the authoritative
                # vector in one transaction. A crash cannot commit either side
                # alone, and failback CAS cannot erase this generation.
                if self.manage_schema and mark_dirty:
                    await conn.fetchval(
                        "SELECT public.mark_memory_weaviate_dirty()"
                    )
                # `$1::vector` binds the literal as text so Postgres runs the
                # vector input function; asyncpg has no native vector codec.
                result = await conn.execute(
                    f"UPDATE public.memory_facts SET embedding = "
                    f"$1::vector({self.embedding_dimension}), "
                    f"embedding_model = $4, "
                    f"embedding_generation = {self._pgvector_generation} "
                    f"WHERE id = $2 AND content = $3{target_guard}",
                    str(embedding),
                    _to_uuid(fact_id),
                    content,
                    self.embedding_model,
                )
                if result == "UPDATE 0":
                    raise MemoryEmbeddingWriteSuperseded(
                        "Memory fact content or embedding contract changed "
                        "before embedding write"
                    )

    async def search_similar(
        self,
        query: str,
        user_id: str,
        namespace: str = "default",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search for semantically similar memories."""
        await self.initialize()
        async with self._transition_lock:
            backend = self.backend
        if backend == "weaviate":
            clean_generation = await self._clean_weaviate_generation()
            if clean_generation is None:
                return await self._search_pgvector(query, user_id, namespace, limit)
            try:
                results = await self._search_weaviate(
                    query, user_id, namespace, limit
                )
            except Exception as exc:
                if not _weaviate_target_unavailable(exc):
                    raise
                await self._latch_pgvector_after_runtime_failure(exc)
                return await self._search_pgvector(
                    query, user_id, namespace, limit
                )
            if self.manage_schema:
                rebuild_required, generation = await self._get_weaviate_sync_state(
                    include_pending=False
                )
                if rebuild_required or generation != clean_generation:
                    if rebuild_required:
                        async with self._transition_lock:
                            if self.backend == "weaviate":
                                self.backend = "pgvector"
                                self.weaviate_state = "unavailable"
                                self.weaviate_state_reason = (
                                    "secondary_dirty_detected"
                                )
                    # The Weaviate result was obtained across a generation
                    # transition and may be stale. Never expose it. This call
                    # intentionally sits outside the Weaviate exception block.
                    return await self._search_pgvector(
                        query, user_id, namespace, limit
                    )
            return results
        return await self._search_pgvector(query, user_id, namespace, limit)

    @staticmethod
    def _escape_graphql_string(value: str) -> str:
        """Escape a string for safe inclusion in GraphQL string literals."""
        return (
            value
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace("\b", "\\b")
            .replace("\f", "\\f")
        )

    async def _search_weaviate(
        self, query: str, user_id: str, namespace: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Search Weaviate for similar memories."""
        safe_query = self._escape_graphql_string(query)
        safe_user_id = self._escape_graphql_string(user_id)
        safe_namespace = self._escape_graphql_string(namespace)
        graphql = {
            "query": f"""{{
                Get {{
                    {WEAVIATE_COLLECTION_NAME}(
                        nearText: {{concepts: ["{safe_query}"]}}
                        where: {{
                            operator: And
                            operands: [
                                {{path: ["userId"], operator: Equal, valueText: "{safe_user_id}"}},
                                {{path: ["namespace"], operator: Equal, valueText: "{safe_namespace}"}},
                                {{path: ["isActive"], operator: Equal, valueBoolean: true}}
                            ]
                        }}
                        limit: {limit}
                    ) {{
                        content
                        userId
                        namespace
                        factType
                        confidence
                        pgFactId
                        _additional {{
                            distance
                            id
                        }}
                    }}
                }}
            }}"""
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.weaviate_url}/v1/graphql", json=graphql
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        objects = (
            data.get("data", {})
            .get("Get", {})
            .get(WEAVIATE_COLLECTION_NAME, [])
        )
        for obj in objects:
            results.append(
                {
                    "pg_fact_id": obj.get("pgFactId"),
                    "content": obj.get("content"),
                    "fact_type": obj.get("factType"),
                    "confidence": obj.get("confidence"),
                    "distance": obj.get("_additional", {}).get("distance"),
                    "weaviate_id": obj.get("_additional", {}).get("id"),
                }
            )
        return results

    async def _search_pgvector(
        self, query: str, user_id: str, namespace: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Search pgvector for similar memories using cosine similarity."""
        embedding = await self._generate_embedding(query)
        self._validate_embedding(embedding)
        distance = self._distance_expression()
        # #804: embedding already generated above (no connection held across
        # that I/O), so this short UPDATE/SELECT draws from the shared pool.
        async with acquire_conn(self.database_url) as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, content, fact_type, confidence,
                       {distance} AS distance
                FROM public.memory_facts
                WHERE user_id = $2
                  AND namespace = $3
                  AND is_active = true
                  AND embedding IS NOT NULL
                  AND vector_dims(embedding) = {self.embedding_dimension}
                  AND embedding_model = $5
                  AND embedding_generation = {self._pgvector_generation}
                ORDER BY {distance}
                LIMIT $4
                """,
                str(embedding),
                _to_uuid(user_id),
                namespace,
                limit,
                self.embedding_model,
            )
            return [
                {
                    "pg_fact_id": str(row["id"]),
                    "content": row["content"],
                    "fact_type": row["fact_type"],
                    "confidence": row["confidence"],
                    "distance": row["distance"],
                    "weaviate_id": None,
                }
                for row in rows
            ]

    async def delete_embedding(self, fact_id: str, weaviate_id: Optional[str] = None):
        """Delete an embedding from the vector store."""
        await self.initialize()
        async with self._transition_lock:
            backend = self.backend
        if backend != "weaviate":
            # pgvector recall follows the canonical row lifecycle. The dirty
            # marker invalidates a concurrent failback CAS.
            await self._mark_weaviate_dirty()
            return
        if not weaviate_id:
            await self._mark_weaviate_dirty()
            return

        clean_generation = await self._clean_weaviate_generation()
        if clean_generation is None:
            await self._mark_weaviate_dirty()
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(
                    f"{self.weaviate_url}/v1/objects/"
                    f"{WEAVIATE_COLLECTION_NAME}/{weaviate_id}"
                )
                if resp.status_code not in (200, 204, 404):
                    resp.raise_for_status()
        except Exception as exc:
            if not _weaviate_target_unavailable(exc):
                raise
            await self._latch_pgvector_after_runtime_failure(exc)
            return
        if await self._weaviate_mutation_crossed_generation(clean_generation):
            await self._mark_weaviate_dirty()
            await self._latch_pgvector_for_generation_change()
        # pgvector: embedding is in the row, deleted when the row is deleted/updated

    async def deactivate_embedding(
        self, fact_id: str, weaviate_id: Optional[str] = None
    ) -> None:
        """Remove a deactivated fact from Weaviate recall without deleting audit data."""
        await self.initialize()
        async with self._transition_lock:
            backend = self.backend
        if backend != "weaviate":
            # PostgreSQL `is_active` is part of every pgvector recall
            # predicate. Dirty intent protects that canonical retirement from
            # a concurrent failback.
            await self._mark_weaviate_dirty()
            return

        clean_generation = await self._clean_weaviate_generation()
        if clean_generation is None:
            await self._mark_weaviate_dirty()
            return
        try:
            await self._deactivate_weaviate(fact_id, weaviate_id)
        except Exception as exc:
            if not _weaviate_target_unavailable(exc):
                raise
            await self._latch_pgvector_after_runtime_failure(exc)
            return
        if await self._weaviate_mutation_crossed_generation(clean_generation):
            await self._mark_weaviate_dirty()
            await self._latch_pgvector_for_generation_change()

    async def _deactivate_weaviate(
        self, fact_id: str, weaviate_id: Optional[str] = None
    ) -> None:
        if not self.weaviate_url:
            raise RuntimeError("Cannot deactivate Weaviate object: WEAVIATE_URL is unset")
        async with httpx.AsyncClient(timeout=10.0) as client:
            object_ids = [weaviate_id] if weaviate_id else []
            object_ids.extend(await self._weaviate_ids_for_fact(client, fact_id))

            for object_id in dict.fromkeys(value for value in object_ids if value):
                resp = await client.patch(
                    f"{self.weaviate_url}/v1/objects/"
                    f"{WEAVIATE_COLLECTION_NAME}/{object_id}",
                    json={
                        "class": WEAVIATE_COLLECTION_NAME,
                        "properties": {"isActive": False},
                    },
                )
                # Missing objects are already absent from semantic recall.
                if resp.status_code != 404:
                    resp.raise_for_status()

    async def _weaviate_ids_for_fact(self, client, fact_id: str) -> List[str]:
        safe_fact_id = self._escape_graphql_string(fact_id)
        lookup = await client.post(
            f"{self.weaviate_url}/v1/graphql",
            json={
                "query": f"""{{
                    Get {{
                        {WEAVIATE_COLLECTION_NAME}(
                            where: {{path: [\"pgFactId\"], operator: Equal,
                                    valueText: \"{safe_fact_id}\"}}
                        ) {{ _additional {{ id }} }}
                    }}
                }}"""
            },
        )
        lookup.raise_for_status()
        lookup_data = lookup.json()
        if lookup_data.get("errors"):
            raise RuntimeError("Weaviate legacy-object lookup failed")
        return [
            value
            for obj in (
                lookup_data.get("data", {})
                .get("Get", {})
                .get(WEAVIATE_COLLECTION_NAME, [])
            )
            if (value := obj.get("_additional", {}).get("id"))
        ]

    async def _delete_stale_weaviate_objects(
        self, fact_id: str, *, keep_id: str
    ) -> None:
        """Remove legacy duplicate objects after deterministic-ID rebuild."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            for object_id in dict.fromkeys(
                await self._weaviate_ids_for_fact(client, fact_id)
            ):
                if object_id == keep_id:
                    continue
                response = await client.delete(
                    f"{self.weaviate_url}/v1/objects/"
                    f"{WEAVIATE_COLLECTION_NAME}/{object_id}"
                )
                if response.status_code not in (200, 204, 404):
                    response.raise_for_status()

    async def update_embedding(
        self,
        fact_id: str,
        content: str,
        user_id: str = "",
        namespace: str = "default",
        fact_type: str = "observation",
        confidence: float = 1.0,
        weaviate_id: Optional[str] = None,
    ) -> Optional[str]:
        """Update an embedding after fact content changes. Returns new weaviate_id."""
        await self.initialize()
        async with self._transition_lock:
            backend = self.backend
        if backend != "weaviate":
            await self._store_pgvector(fact_id, content)
            return None

        clean_generation = await self._clean_weaviate_generation()
        if clean_generation is None:
            await self._store_pgvector(fact_id, content)
            return None
        try:
            new_weaviate_id = await self._update_weaviate(
                fact_id, content, user_id, namespace, fact_type,
                confidence, weaviate_id,
            )
        except Exception as exc:
            if not _weaviate_target_unavailable(exc):
                raise
            await self._latch_pgvector_after_runtime_failure(exc)
            await self._store_pgvector(fact_id, content)
            return None
        await self._store_pgvector(fact_id, content, mark_dirty=False)
        if await self._weaviate_mutation_crossed_generation(clean_generation):
            await self._mark_weaviate_dirty()
            await self._latch_pgvector_for_generation_change()
            return None
        return new_weaviate_id

    async def _update_weaviate(
        self,
        fact_id: str,
        content: str,
        user_id: str,
        namespace: str,
        fact_type: str,
        confidence: float,
        weaviate_id: Optional[str],
    ) -> str:
        new_weaviate_id = await self._store_weaviate(
            fact_id, content, user_id, namespace, fact_type, confidence
        )
        if weaviate_id and weaviate_id != new_weaviate_id:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.delete(
                    f"{self.weaviate_url}/v1/objects/"
                    f"{WEAVIATE_COLLECTION_NAME}/{weaviate_id}"
                )
                if response.status_code not in (200, 204, 404):
                    response.raise_for_status()
        return new_weaviate_id
