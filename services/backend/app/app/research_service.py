import asyncio
import logging
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from db_connection import get_pg_pool
from research_client import (
    ResearchClient,
    ResearchRequest,
    ResearchStatus,
    ResearchResult,
    ResearchError,
)


logger = logging.getLogger(__name__)
_PUBLIC_RESEARCH_FAILURE = "Research failed; inspect backend logs for details"


class ResearchCapacityError(RuntimeError):
    """Raised before persistence when this Backend has no research slot."""


def _positive_env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _log_task_exception(session_id: str):
    """Build an add_done_callback that surfaces silent task crashes.

    asyncio swallows exceptions raised before ``create_task``'s coroutine
    catches them; without this hook the research background task would
    die quietly, leaving the DB row in PENDING and the operator with no
    diagnostic trail.
    """
    def _callback(task: "asyncio.Task[Any]") -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.error(
                "research bg task crashed (session_id=%s, error_type=%s)",
                session_id,
                type(exc).__name__,
            )
    return _callback


class ResearchService:
    """Service for managing research operations with database persistence"""

    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable is required")
        
        self.research_client = ResearchClient()
        self._active_tasks = {}  # Track background tasks
        self._cancel_requested_tasks: set[asyncio.Task[Any]] = set()
        self.max_concurrent_research = _positive_env_int(
            "RESEARCH_MAX_CONCURRENT", 4
        )
        self.lease_seconds = _positive_env_int(
            "RESEARCH_SESSION_LEASE_SECONDS", 300
        )
        self.heartbeat_interval = max(1, min(30, self.lease_seconds // 3))
        self._maintenance_task: Optional[asyncio.Task[Any]] = None
        self._pool = None

    async def _get_db_connection(self):
        """Acquire a research-session database connection from the shared pool.

        Every research query is short-lived and never holds the connection
        across the long research-poll I/O (that runs on a separate task with no
        connection held), so drawing from the shared pool (#804) is safe here.
        The pool preserves the 10s connect / 30s command timeouts so a hung
        Postgres bouncer or stuck query cannot pin a uvicorn worker. Callers
        MUST return the connection via ``_release_db_connection``.
        """
        pool = await get_pg_pool(self.db_url)
        self._pool = pool
        return await pool.acquire()

    async def _release_db_connection(self, conn) -> None:
        """Return a research connection (paired with _get_db_connection).

        Normally releases back to the shared pool. If no pool was established
        (e.g. ``_get_db_connection`` was overridden in a test, or acquisition
        failed before the pool was cached), the connection is closed directly —
        a real pooled connection cannot exist without ``self._pool`` being set,
        so this fallback only ever runs on a non-pooled/dedicated connection.
        """
        pool = getattr(self, "_pool", None)
        if pool is None:
            await conn.close()
            return
        await pool.release(conn)

    async def start_research(
        self,
        query: str,
        max_loops: int = 3,
        search_api: str = "searxng",
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Start a new research session with database tracking"""
        
        session_id = str(uuid4())
        capacity = getattr(self, "max_concurrent_research", None)
        if capacity is None:
            capacity = _positive_env_int("RESEARCH_MAX_CONCURRENT", 4)
        # The reservation is inserted before the first await, so concurrent
        # request coroutines cannot all pass the capacity check and then block
        # in database work. It is replaced by the background Task after the
        # durable PENDING row has been committed.
        if len(self._active_tasks) >= capacity:
            raise ResearchCapacityError("Research capacity is full")
        self._active_tasks[session_id] = None

        try:
            conn = await self._get_db_connection()
            try:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO public.research_sessions
                        (id, query, status, max_loops, search_api, user_id, started_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """, session_id, query, ResearchStatus.PENDING.value, max_loops,
                        search_api, UUID(user_id) if user_id else None,
                        datetime.now(timezone.utc))

                    await conn.execute("""
                        INSERT INTO public.research_logs
                        (session_id, step_number, step_type, message)
                        VALUES ($1, $2, $3, $4)
                    """, session_id, 1, "start",
                        f"Research session started for query: {query}")
            finally:
                await self._release_db_connection(conn)

            # Start background research task. add_done_callback surfaces any
            # exception raised before _run_research_background's outer try
            # block (e.g. asyncpg pool reset) which asyncio would otherwise
            # swallow silently — the session would then sit in PENDING forever.
            task = asyncio.create_task(
                self._run_research_background(
                    session_id, query, max_loops, search_api, user_id
                )
            )
            task.add_done_callback(_log_task_exception(session_id))
            self._active_tasks[session_id] = task
        except BaseException:
            self._active_tasks.pop(session_id, None)
            raise

        return {
            "session_id": session_id,
            "status": ResearchStatus.PENDING.value,
            "message": "Research session created and queued",
            "query": query,
            "max_loops": max_loops,
            "search_api": search_api
        }

    async def _run_research_background(
        self, 
        session_id: str, 
        query: str, 
        max_loops: int, 
        search_api: str,
        user_id: Optional[str]
    ):
        """Run research in background and update database"""
        heartbeat_task: Optional[asyncio.Task[Any]] = None
        try:
            started = await self._mark_research_running(session_id)
            if not started:
                return

            heartbeat_task = asyncio.create_task(
                self._heartbeat_research(session_id)
            )

            # Create request for research client
            request = ResearchRequest(
                query=query,
                max_loops=max_loops,
                search_api=search_api,
                user_id=user_id
            )

            # Execute research using the actual local-deep-researcher service
            await self._execute_research(session_id, request)

        except asyncio.CancelledError:
            try:
                await self._record_research_failure(
                    session_id, "Research worker stopped before completion"
                )
            except Exception as record_error:
                logger.error(
                    "cancelled research could not be terminalized "
                    "(session_id=%s, error_type=%s)",
                    session_id,
                    type(record_error).__name__,
                )
            raise
        except Exception as e:
            logger.error(
                "research execution failed (session_id=%s, error_type=%s)",
                session_id,
                type(e).__name__,
            )
            try:
                await self._record_research_failure(
                    session_id, _PUBLIC_RESEARCH_FAILURE
                )
            except Exception as record_error:
                logger.error(
                    "research failure could not be persisted "
                    "(session_id=%s, error_type=%s)",
                    session_id,
                    type(record_error).__name__,
                )

        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            # Clean up task reference
            if session_id in self._active_tasks:
                del self._active_tasks[session_id]

    async def _mark_research_running(self, session_id: str) -> bool:
        """Claim a pending session without reviving a cancelled one."""
        conn = await self._get_db_connection()
        try:
            row = await conn.fetchrow("""
                UPDATE public.research_sessions
                SET status = $1, started_at = $2, heartbeat_at = $2
                WHERE id = $3 AND status = $4
                RETURNING id
            """, ResearchStatus.RUNNING.value, datetime.now(timezone.utc),
                session_id, ResearchStatus.PENDING.value)
            if not row:
                return False
            await conn.execute("""
                INSERT INTO public.research_logs
                (session_id, step_number, step_type, message)
                VALUES ($1, $2, $3, $4)
            """, session_id, 2, "execute", "Starting research execution")
            return True
        finally:
            await self._release_db_connection(conn)

    async def _write_research_heartbeat(self, session_id: str) -> None:
        conn = await self._get_db_connection()
        try:
            await conn.execute("""
                UPDATE public.research_sessions
                SET heartbeat_at = now()
                WHERE id = $1 AND status = $2
            """, session_id, ResearchStatus.RUNNING.value)
        finally:
            await self._release_db_connection(conn)

    async def _heartbeat_research(self, session_id: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await self._write_research_heartbeat(session_id)
            except Exception as exc:
                logger.warning(
                    "research heartbeat failed (session_id=%s, error_type=%s)",
                    session_id,
                    type(exc).__name__,
                )

    async def recover_stale_sessions(self) -> int:
        """Terminalize abandoned pending/running sessions under one transaction."""

        conn = await self._get_db_connection()
        try:
            async with conn.transaction():
                rows = await conn.fetch("""
                    UPDATE public.research_sessions
                    SET status = 'failed',
                        completed_at = now(),
                        error_message = 'Research worker lease expired'
                    WHERE (
                        status = 'pending'
                        AND created_at < now() - ($1 * interval '1 second')
                    ) OR (
                        status = 'running'
                        AND COALESCE(heartbeat_at, started_at, updated_at, created_at)
                            < now() - ($1 * interval '1 second')
                    )
                    RETURNING id
                """, self.lease_seconds)
                for row in rows:
                    await conn.execute("""
                        INSERT INTO public.research_logs
                        (session_id, step_number, step_type, message)
                        VALUES ($1, $2, $3, $4)
                    """, row["id"], 99, "error",
                        "Research failed: worker lease expired")
                return len(rows)
        finally:
            await self._release_db_connection(conn)

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                recovered = await self.recover_stale_sessions()
                if recovered:
                    logger.warning(
                        "terminalized %s stale research session(s)", recovered
                    )
            except Exception as exc:
                logger.warning(
                    "research maintenance sweep failed (error_type=%s)",
                    type(exc).__name__,
                )

    async def start_maintenance(self) -> None:
        # Best-effort at startup, mirroring the lazy-pool philosophy: if the DB
        # is still initializing when the backend boots, don't prevent startup —
        # the maintenance loop recovers stale sessions on later sweeps.
        try:
            await self.recover_stale_sessions()
        except Exception as exc:
            logger.warning(
                "research startup sweep deferred (error_type=%s); maintenance "
                "loop will retry",
                type(exc).__name__,
            )
        if self._maintenance_task is None or self._maintenance_task.done():
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def aclose(self) -> None:
        tasks = [task for task in self._active_tasks.values() if task is not None]
        cancel_requested = getattr(self, "_cancel_requested_tasks", set())
        for task in tasks:
            if task not in cancel_requested:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            await asyncio.gather(self._maintenance_task, return_exceptions=True)
            self._maintenance_task = None

    async def _append_research_log(
        self, session_id: str, step: int, step_type: str, message: str
    ) -> None:
        conn = await self._get_db_connection()
        try:
            await conn.execute("""
                INSERT INTO public.research_logs
                (session_id, step_number, step_type, message)
                VALUES ($1, $2, $3, $4)
            """, session_id, step, step_type, message)
        finally:
            await self._release_db_connection(conn)

    async def _record_research_failure(
        self, session_id: str, error_message: str
    ) -> bool:
        """Record failure only while the session is still non-terminal."""
        conn = await self._get_db_connection()
        try:
            row = await conn.fetchrow("""
                UPDATE public.research_sessions
                SET status = $1, completed_at = $2, error_message = $3
                WHERE id = $4 AND status IN ($5, $6)
                RETURNING id
            """, ResearchStatus.FAILED.value, datetime.now(timezone.utc),
                error_message, session_id, ResearchStatus.PENDING.value,
                ResearchStatus.RUNNING.value)
            if not row:
                return False
            await conn.execute("""
                INSERT INTO public.research_logs
                (session_id, step_number, step_type, message)
                VALUES ($1, $2, $3, $4)
            """, session_id, 99, "error", f"Research failed: {error_message}")
            return True
        finally:
            await self._release_db_connection(conn)

    async def _execute_research(
        self, 
        session_id: str, 
        request: ResearchRequest
    ):
        """Execute research using actual local-deep-researcher service"""
        remote_session_id: str | None = None

        try:
            # Use the research client to start the research
            research_response = await self.research_client.start_research(request)

            # #802: start_research now reports PENDING (thread created), not
            # RUNNING (the run is dispatched later by wait_for_completion).
            if research_response.status != ResearchStatus.PENDING:
                raise ResearchError(f"Failed to start research: {research_response.message}")

            remote_session_id = research_response.session_id

            # Log the remote session ID
            await self._append_research_log(
                session_id,
                3,
                "remote_start",
                f"Remote research session started: {remote_session_id}",
            )

            # Wait for completion
            final_response = await self.research_client.wait_for_completion(remote_session_id)

            if final_response.status == ResearchStatus.COMPLETED:
                # Get the results
                research_result = await self.research_client.get_research_result(remote_session_id)

                if research_result:
                    # Store the results
                    await self._store_research_result(session_id, research_result)
                else:
                    raise ResearchError("Failed to retrieve research results")
            else:
                raise ResearchError(f"Research failed: {final_response.message}")
        finally:
            if remote_session_id:
                self.research_client.discard_pending(remote_session_id)

    async def _store_research_result(
        self, 
        session_id: str, 
        research_result: ResearchResult
    ) -> bool:
        """Store a result atomically if cancellation has not won the race."""
        conn = await self._get_db_connection()
        try:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    SELECT status
                    FROM public.research_sessions
                    WHERE id = $1
                    FOR UPDATE
                """, session_id)
                if not row or row["status"] != ResearchStatus.RUNNING.value:
                    return False

                await conn.execute("""
                    INSERT INTO public.research_logs
                    (session_id, step_number, step_type, message)
                    VALUES ($1, $2, $3, $4)
                """, session_id, 4, "complete", "Research completed successfully")

                result_id = str(uuid4())
                await conn.execute("""
                    INSERT INTO public.research_results
                    (id, session_id, title, summary, content, sources, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                """, result_id, session_id, research_result.title,
                    research_result.summary, research_result.content,
                    json.dumps(research_result.sources),
                    json.dumps(research_result.metadata))

                for source in research_result.sources:
                    await conn.execute("""
                        INSERT INTO public.research_sources
                        (session_id, result_id, url, title, relevance_score, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6)
                    """, session_id, result_id, source.get("url") or "",
                        source.get("title"), source.get("relevance_score", 0.0),
                        json.dumps(source.get("metadata", {})))

                await conn.execute("""
                    UPDATE public.research_sessions
                    SET status = $1, completed_at = $2
                    WHERE id = $3 AND status = $4
                """, ResearchStatus.COMPLETED.value, datetime.now(timezone.utc),
                    session_id, ResearchStatus.RUNNING.value)
                return True
        finally:
            await self._release_db_connection(conn)

    async def get_research_status(
        self, session_id: str, owner_user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get research session status"""
        conn = await self._get_db_connection()
        
        try:
            row = await conn.fetchrow("""
                SELECT id, query, status, max_loops, search_api, user_id,
                       created_at, updated_at, started_at, completed_at, error_message
                FROM public.research_sessions 
                WHERE id = $1
                  AND ($2::uuid IS NULL OR user_id = $2::uuid)
            """, session_id, UUID(owner_user_id) if owner_user_id else None)
            
            if not row:
                return None
                
            return {
                "session_id": str(row["id"]),
                "query": row["query"],
                "status": row["status"],
                "max_loops": row["max_loops"],
                "search_api": row["search_api"],
                "user_id": str(row["user_id"]) if row["user_id"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                "error_message": row["error_message"]
            }
        finally:
            await self._release_db_connection(conn)

    async def get_research_result(
        self, session_id: str, owner_user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get research results for a completed session"""
        conn = await self._get_db_connection()
        
        try:
            row = await conn.fetchrow("""
                SELECT r.id, r.title, r.summary, r.content, r.sources, r.metadata, r.created_at,
                       s.status
                FROM public.research_results r
                JOIN public.research_sessions s ON r.session_id = s.id
                WHERE r.session_id = $1
                  AND ($2::uuid IS NULL OR s.user_id = $2::uuid)
            """, session_id, UUID(owner_user_id) if owner_user_id else None)
            
            if not row:
                return None
                
            return {
                "session_id": session_id,
                "result_id": str(row["id"]),
                "title": row["title"],
                "summary": row["summary"],
                "content": row["content"],
                "sources": json.loads(row["sources"]) if isinstance(row["sources"], str) else (row["sources"] or []),
                "metadata": json.loads(row["metadata"]) if isinstance(row["metadata"], str) else (row["metadata"] or {}),
                "created_at": row["created_at"].isoformat(),
                "status": row["status"]
            }
        finally:
            await self._release_db_connection(conn)

    async def list_user_sessions(
        self, 
        user_id: Optional[str] = None, 
        limit: int = 50, 
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List research sessions for a user"""
        conn = await self._get_db_connection()
        
        try:
            if user_id:
                rows = await conn.fetch("""
                    SELECT id, query, status, max_loops, search_api, 
                           created_at, started_at, completed_at
                    FROM public.research_sessions 
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                """, UUID(user_id), limit, offset)
            else:
                rows = await conn.fetch("""
                    SELECT id, query, status, max_loops, search_api, 
                           created_at, started_at, completed_at
                    FROM public.research_sessions 
                    ORDER BY created_at DESC
                    LIMIT $1 OFFSET $2
                """, limit, offset)
            
            return [
                {
                    "session_id": str(row["id"]),
                    "query": row["query"],
                    "status": row["status"],
                    "max_loops": row["max_loops"],
                    "search_api": row["search_api"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                    "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None
                }
                for row in rows
            ]
        finally:
            await self._release_db_connection(conn)

    async def cancel_research(
        self, session_id: str, owner_user_id: Optional[str] = None
    ) -> bool:
        """Cancel a running research session"""
        conn = await self._get_db_connection()
        
        try:
            # PENDING is cancellable too: the insert(PENDING)->RUNNING update
            # races this path, and the background task is already live in
            # _active_tasks during that window. Make the DB update conditional
            # so a stale read cannot overwrite COMPLETED/FAILED.
            cancelled_row = await conn.fetchrow("""
                UPDATE public.research_sessions
                SET status = $1, completed_at = $2
                WHERE id = $3 AND status IN ($4, $5)
                  AND ($6::uuid IS NULL OR user_id = $6::uuid)
                RETURNING id
            """, ResearchStatus.CANCELLED.value, datetime.now(timezone.utc), session_id,
                ResearchStatus.PENDING.value, ResearchStatus.RUNNING.value,
                UUID(owner_user_id) if owner_user_id else None)

            if not cancelled_row:
                return False
            
            # Cancel background task if it exists
            if session_id in self._active_tasks:
                task = self._active_tasks[session_id]
                if task is not None:
                    task.cancel()
                    cancel_requested = getattr(
                        self, "_cancel_requested_tasks", None
                    )
                    if cancel_requested is None:
                        cancel_requested = self._cancel_requested_tasks = set()
                    cancel_requested.add(task)
                    task.add_done_callback(cancel_requested.discard)

            await conn.execute("""
                INSERT INTO public.research_logs (session_id, step_number, step_type, message)
                VALUES ($1, $2, $3, $4)
            """, session_id, 98, "cancel", "Research session cancelled by user")
            
            return True
        finally:
            await self._release_db_connection(conn)

    async def get_research_logs(
        self, session_id: str, owner_user_id: Optional[str] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """Get research logs for a session"""
        conn = await self._get_db_connection()
        
        try:
            session_row = await conn.fetchrow("""
                SELECT id FROM public.research_sessions
                WHERE id = $1
                  AND ($2::uuid IS NULL OR user_id = $2::uuid)
            """, session_id, UUID(owner_user_id) if owner_user_id else None)
            if not session_row:
                return None

            rows = await conn.fetch("""
                SELECT step_number, step_type, message, data, created_at
                FROM public.research_logs 
                WHERE session_id = $1
                ORDER BY step_number ASC
            """, session_id)
            
            return [
                {
                    "step_number": row["step_number"],
                    "step_type": row["step_type"],
                    "message": row["message"],
                    "data": json.loads(row["data"]) if isinstance(row["data"], str) else row["data"],
                    "timestamp": row["created_at"].isoformat()
                }
                for row in rows
            ]
        finally:
            await self._release_db_connection(conn)

    async def health_check(self) -> Dict[str, Any]:
        """Check service health including database and research client"""
        results = {
            "database": "unknown",
            "research_client": "unknown",
            "active_tasks": len(self._active_tasks)
        }
        
        # Test database connection
        try:
            conn = await self._get_db_connection()
            try:
                await conn.fetchval("SELECT 1")
            finally:
                # close in finally — a probe failure post-connect used to
                # leak the connection (every other site closes in finally).
                await self._release_db_connection(conn)
            results["database"] = "healthy"
        except Exception as e:
            logger.warning(
                "research database health check failed (error_type=%s)",
                type(e).__name__,
            )
            results["database"] = "unhealthy"
        
        # Test research client
        try:
            client_health = await self.research_client.health_check()
            results["research_client"] = client_health["status"]
        except Exception as e:
            logger.warning(
                "research client health check failed (error_type=%s)",
                type(e).__name__,
            )
            results["research_client"] = "unhealthy"
        
        return results
