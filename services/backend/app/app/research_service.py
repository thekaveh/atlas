import asyncio
import asyncpg
import logging
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from db_connection import connect_postgres
from research_client import (
    ResearchClient,
    ResearchRequest,
    ResearchStatus,
    ResearchResult,
    ResearchError,
)


logger = logging.getLogger(__name__)


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
                "research bg task crashed (session_id=%s)",
                session_id,
                exc_info=exc,
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

    async def _get_db_connection(self):
        """Get database connection.

        timeout/command_timeout bound the connect phase and per-query
        budget. Defaults are 60s connect + None command_timeout, which
        let a hung Postgres bouncer or stuck migration pin a uvicorn
        worker indefinitely.
        """
        return await connect_postgres(self.db_url)

    async def start_research(
        self,
        query: str,
        max_loops: int = 3,
        search_api: str = "searxng",
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Start a new research session with database tracking"""
        
        # Create database record first
        session_id = str(uuid4())
        conn = await self._get_db_connection()
        
        try:
            await conn.execute("""
                INSERT INTO public.research_sessions 
                (id, query, status, max_loops, search_api, user_id, started_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, session_id, query, ResearchStatus.PENDING.value, max_loops, search_api, 
                UUID(user_id) if user_id else None, datetime.now(timezone.utc))
            
            # Log the start
            await conn.execute("""
                INSERT INTO public.research_logs (session_id, step_number, step_type, message)
                VALUES ($1, $2, $3, $4)
            """, session_id, 1, "start", f"Research session started for query: {query}")
            
        finally:
            await conn.close()

        # Start background research task. add_done_callback surfaces any
        # exception raised before _run_research_background's outer try
        # block (e.g. asyncpg pool reset) which asyncio would otherwise
        # swallow silently — the session would then sit in PENDING forever.
        task = asyncio.create_task(
            self._run_research_background(session_id, query, max_loops, search_api, user_id)
        )
        task.add_done_callback(_log_task_exception(session_id))
        self._active_tasks[session_id] = task

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
        conn = None

        try:
            conn = await self._get_db_connection()

            # Update status to running
            await conn.execute("""
                UPDATE public.research_sessions 
                SET status = $1, started_at = $2
                WHERE id = $3
            """, ResearchStatus.RUNNING.value, datetime.now(timezone.utc), session_id)

            await conn.execute("""
                INSERT INTO public.research_logs (session_id, step_number, step_type, message)
                VALUES ($1, $2, $3, $4)
            """, session_id, 2, "execute", "Starting research execution")

            # Create request for research client
            request = ResearchRequest(
                query=query,
                max_loops=max_loops,
                search_api=search_api,
                user_id=user_id
            )

            # Execute research using the actual local-deep-researcher service
            await self._execute_research(conn, session_id, request)

        except Exception as e:
            if conn is not None:
                # Update status to failed
                await conn.execute("""
                    UPDATE public.research_sessions
                    SET status = $1, completed_at = $2, error_message = $3
                    WHERE id = $4
                """, ResearchStatus.FAILED.value, datetime.now(timezone.utc), str(e), session_id)

                await conn.execute("""
                    INSERT INTO public.research_logs (session_id, step_number, step_type, message)
                    VALUES ($1, $2, $3, $4)
                """, session_id, 99, "error", f"Research failed: {str(e)}")
            else:
                logger.error(
                    "research bg task failed before database connection (session_id=%s)",
                    session_id,
                    exc_info=e,
                )

        finally:
            if conn is not None:
                await conn.close()
            # Clean up task reference
            if session_id in self._active_tasks:
                del self._active_tasks[session_id]

    async def _execute_research(
        self, 
        conn: asyncpg.Connection, 
        session_id: str, 
        request: ResearchRequest
    ):
        """Execute research using actual local-deep-researcher service"""
        remote_session_id: str | None = None

        try:
            # Use the research client to start the research
            research_response = await self.research_client.start_research(request)

            if research_response.status != ResearchStatus.RUNNING:
                raise ResearchError(f"Failed to start research: {research_response.message}")

            remote_session_id = research_response.session_id

            # Log the remote session ID
            await conn.execute("""
                INSERT INTO public.research_logs (session_id, step_number, step_type, message)
                VALUES ($1, $2, $3, $4)
            """, session_id, 3, "remote_start", f"Remote research session started: {remote_session_id}")

            # Wait for completion
            final_response = await self.research_client.wait_for_completion(remote_session_id)

            if final_response.status == ResearchStatus.COMPLETED:
                # Get the results
                research_result = await self.research_client.get_research_result(remote_session_id)

                if research_result:
                    # Store the results
                    await self._store_research_result(conn, session_id, research_result)
                else:
                    raise ResearchError("Failed to retrieve research results")
            else:
                raise ResearchError(f"Research failed: {final_response.message}")
        finally:
            if remote_session_id:
                self.research_client.discard_pending(remote_session_id)

    async def _store_research_result(
        self, 
        conn: asyncpg.Connection, 
        session_id: str, 
        research_result: ResearchResult
    ):
        """Store research results in the database"""
        
        # Log completion
        await conn.execute("""
            INSERT INTO public.research_logs (session_id, step_number, step_type, message)
            VALUES ($1, $2, $3, $4)
        """, session_id, 4, "complete", "Research completed successfully")

        # Store research result
        result_id = str(uuid4())
        await conn.execute("""
            INSERT INTO public.research_results 
            (id, session_id, title, summary, content, sources, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, result_id, session_id, research_result.title, research_result.summary,
            research_result.content, json.dumps(research_result.sources), 
            json.dumps(research_result.metadata))

        # Store individual sources
        for source in research_result.sources:
            await conn.execute("""
                INSERT INTO public.research_sources 
                (session_id, result_id, url, title, relevance_score, metadata)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, session_id, result_id, source.get("url", ""), source.get("title", ""),
                source.get("relevance_score", 0.0), json.dumps(source.get("metadata", {})))

        # Update session as completed
        await conn.execute("""
            UPDATE public.research_sessions 
            SET status = $1, completed_at = $2
            WHERE id = $3
        """, ResearchStatus.COMPLETED.value, datetime.now(timezone.utc), session_id)

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
            await conn.close()

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
            await conn.close()

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
            await conn.close()

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
                self._active_tasks[session_id].cancel()
                del self._active_tasks[session_id]

            await conn.execute("""
                INSERT INTO public.research_logs (session_id, step_number, step_type, message)
                VALUES ($1, $2, $3, $4)
            """, session_id, 98, "cancel", "Research session cancelled by user")
            
            return True
        finally:
            await conn.close()

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
            await conn.close()

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
                await conn.close()
            results["database"] = "healthy"
        except Exception as e:
            results["database"] = f"unhealthy: {str(e)}"
        
        # Test research client
        try:
            client_health = await self.research_client.health_check()
            results["research_client"] = client_health["status"]
        except Exception as e:
            results["research_client"] = f"unhealthy: {str(e)}"
        
        return results
