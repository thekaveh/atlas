import httpx
import os
import json
from typing import Dict, Any, List, Optional, AsyncGenerator
from pydantic import BaseModel
from enum import Enum


class ResearchError(Exception):
    """Raised when a research workflow fails — wraps upstream errors
    from local-deep-researcher into a single catchable type so callers
    don't need ``except Exception``."""
    pass


class ResearchStatus(str, Enum):
    """Research status enumeration"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchRequest(BaseModel):
    """Request model for starting research"""
    query: str
    max_loops: int = 3
    search_api: str = "duckduckgo"
    user_id: Optional[str] = None


class ResearchResponse(BaseModel):
    """Response model for research operations"""
    session_id: str
    status: ResearchStatus
    message: str
    data: Optional[Dict[str, Any]] = None


class ResearchResult(BaseModel):
    """Model for completed research results"""
    session_id: str
    title: str
    summary: str
    content: str
    sources: List[Dict[str, Any]]
    metadata: Dict[str, Any]


class ResearchClient:
    """Client for interacting with Local Deep Researcher service"""

    def __init__(self, base_url: Optional[str] = None, timeout: int = 300):
        self.base_url = base_url or os.getenv(
            "LOCAL_DEEP_RESEARCHER_URL", 
            "http://local-deep-researcher:2024"
        )
        self.timeout = timeout
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self._pending_requests: Dict[str, ResearchRequest] = {}
        self._completed_results: Dict[str, ResearchResult] = {}

    async def health_check(self) -> Dict[str, Any]:
        """Check if the research service is healthy"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{self.base_url}/ok")
                if response.status_code == 200:
                    return {"status": "healthy", "service": "local-deep-researcher"}
                else:
                    return {"status": "unhealthy", "error": f"HTTP {response.status_code}"}
            except Exception as e:
                return {"status": "unhealthy", "error": str(e)}

    async def start_research(self, request: ResearchRequest) -> ResearchResponse:
        """Start a new research session"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(f"{self.base_url}/threads", json={})
                response.raise_for_status()
                data = response.json()
                thread_id = data.get("thread_id") or data.get("id")
                if not thread_id:
                    raise ResearchError("LangGraph thread response did not include thread_id")
                self._pending_requests[thread_id] = request
                return ResearchResponse(
                    session_id=thread_id,
                    status=ResearchStatus.RUNNING,
                    message="Research thread created successfully",
                    data=data
                )
            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP {e.response.status_code}: {e.response.text}"
                return ResearchResponse(
                    session_id="",
                    status=ResearchStatus.FAILED,
                    message=f"Failed to start research: {error_msg}"
                )
            except Exception as e:
                return ResearchResponse(
                    session_id="",
                    status=ResearchStatus.FAILED,
                    message=f"Failed to start research: {str(e)}"
                )

    async def get_research_status(self, session_id: str) -> ResearchResponse:
        """Get the status of a research session"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f"{self.base_url}/threads/{session_id}/state")
                response.raise_for_status()
                
                data = response.json()
                return ResearchResponse(
                    session_id=session_id,
                    status=ResearchStatus(data.get("status", "pending")),
                    message=data.get("message", ""),
                    data=data
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return ResearchResponse(
                        session_id=session_id,
                        status=ResearchStatus.FAILED,
                        message="Research session not found"
                    )
                error_msg = f"HTTP {e.response.status_code}: {e.response.text}"
                return ResearchResponse(
                    session_id=session_id,
                    status=ResearchStatus.FAILED,
                    message=f"Failed to get status: {error_msg}"
                )
            except Exception as e:
                return ResearchResponse(
                    session_id=session_id,
                    status=ResearchStatus.FAILED,
                    message=f"Failed to get status: {str(e)}"
                )

    async def get_research_result(self, session_id: str) -> Optional[ResearchResult]:
        """Get the final result of a completed research session"""
        return self._completed_results.get(session_id)

    async def cancel_research(self, session_id: str) -> ResearchResponse:
        """Cancel a running research session"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                return ResearchResponse(
                    session_id=session_id,
                    status=ResearchStatus.FAILED,
                    message="Cancellation is not supported by the LangGraph dev server integration",
                )
            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP {e.response.status_code}: {e.response.text}"
                return ResearchResponse(
                    session_id=session_id,
                    status=ResearchStatus.FAILED,
                    message=f"Failed to cancel research: {error_msg}"
                )
            except Exception as e:
                return ResearchResponse(
                    session_id=session_id,
                    status=ResearchStatus.FAILED,
                    message=f"Failed to cancel research: {str(e)}"
                )

    async def stream_research_logs(self, session_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream real-time logs from a research session"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                async with client.stream(
                    "GET",
                    f"{self.base_url}/threads/{session_id}/runs/stream",
                    headers=self.headers
                ) as response:
                    response.raise_for_status()
                    
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])  # Remove "data: " prefix
                                yield data
                            except json.JSONDecodeError:
                                continue
                        elif line == "event: close":
                            break
            except Exception as e:
                yield {"error": f"Stream error: {str(e)}"}

    async def list_active_sessions(self) -> List[Dict[str, Any]]:
        """List all currently active research sessions."""
        return [{"session_id": session_id} for session_id in self._pending_requests]

    async def wait_for_completion(
        self, 
        session_id: str, 
        poll_interval: int = 5, 
        max_wait_time: int = 300
    ) -> ResearchResponse:
        """Execute the LangGraph research run and wait for the SSE stream to finish."""
        request = self._pending_requests.get(session_id)
        if request is None:
            return ResearchResponse(
                session_id=session_id,
                status=ResearchStatus.FAILED,
                message="Research request was not found for LangGraph thread",
            )

        payload = {
            "assistant_id": "agent",
            "input": {"research_topic": request.query},
            "config": {
                "configurable": {
                    "max_loops": request.max_loops,
                    "search_api": request.search_api,
                }
            },
            "metadata": {"user_id": request.user_id} if request.user_id else {},
            "stream_mode": ["values"],
        }
        final_values: Dict[str, Any] = {}
        current_event = ""
        try:
            async with httpx.AsyncClient(timeout=max_wait_time) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/threads/{session_id}/runs/stream",
                    json=payload,
                    headers=self.headers,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("event: "):
                            current_event = line[7:].strip()
                            continue
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if raw.strip() in {"", "[DONE]"}:
                            continue
                        try:
                            event_data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(event_data, dict):
                            if current_event == "error" or "error" in event_data:
                                error = event_data.get("error") or event_data.get("message") or event_data
                                raise ResearchError(str(error))
                            data = event_data.get("data", event_data)
                            if isinstance(data, dict):
                                if current_event == "error" or "error" in data:
                                    error = data.get("error") or data.get("message") or data
                                    raise ResearchError(str(error))
                                values = data.get("values", data)
                                if isinstance(values, dict):
                                    final_values = values
            if not final_values:
                return ResearchResponse(
                    session_id=session_id,
                    status=ResearchStatus.FAILED,
                    message="Research run produced no final values",
                )
            result = self._result_from_langgraph_values(session_id, request, final_values)
            self._completed_results[session_id] = result
            self._pending_requests.pop(session_id, None)
            return ResearchResponse(
                session_id=session_id,
                status=ResearchStatus.COMPLETED,
                message="Research completed successfully",
                data=final_values,
            )
        except Exception as e:
            return ResearchResponse(
                session_id=session_id,
                status=ResearchStatus.FAILED,
                message=f"Research run failed: {str(e)}",
            )

    def _result_from_langgraph_values(
        self,
        session_id: str,
        request: ResearchRequest,
        values: Dict[str, Any],
    ) -> ResearchResult:
        summary = (
            values.get("final_summary")
            or values.get("running_summary")
            or values.get("summary")
            or ""
        )
        sources = values.get("sources_gathered") or values.get("sources") or []
        if isinstance(sources, str):
            sources = [{"url": source.strip()} for source in sources.splitlines() if source.strip()]
        if not isinstance(sources, list):
            sources = []
        return ResearchResult(
            session_id=session_id,
            title=f"Research: {request.query[:80]}",
            summary=summary[:500] if summary else "",
            content=summary,
            sources=sources,
            metadata={
                "thread_id": session_id,
                "search_api": request.search_api,
                "max_loops": request.max_loops,
                "langgraph_values": values,
            },
        )
