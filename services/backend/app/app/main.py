from fastapi import FastAPI, HTTPException, status, UploadFile, File, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from storage3 import SyncStorageClient as StorageClient
from typing import Optional, cast, Dict, Any, List, Union, Literal
from contextlib import asynccontextmanager
import os
import asyncio
import httpx
import asyncpg
import yaml
import re
import time

from n8n_client import N8nClient
from research_service import ResearchService
from comfyui_client import ComfyUIClient
from fal_media_client import FalClient
from uuid import UUID as _UUID
from memory_service import MemoryService
from memory_models import (
    MemoryExtractRequest, MemoryRecallRequest, MemoryConsolidateRequest,
    MemorySummarizeRequest, MemoryUpdateRequest,
    MemoryFact, MemoryExtractResponse, MemoryRecallResponse,
    MemoryConsolidateResponse, MemorySummarizeResponse,
    MemoryListResponse, MemoryHealthResponse,
)
from ray_routes import router as ray_router
from celery_app import celery_is_enabled, get_celery_job_status
from celery_tasks import memory_consolidate_task, rag_ingestion_task
from rag_ingestion import (
    ProfileNotFoundError,
    RagIngestionQueuedResponse,
    RagIngestionRecordResponse,
    RagIngestionRequest,
    RagIngestionService,
)
from graphiti_experiment import GraphitiExperimentConfig
from document_extraction import (
    DocumentExtractionError,
    DocumentExtractor,
    DocumentTooLargeError,
    ExtractionUnavailableError,
)
from chunking_service import (
    ChunkRequest,
    ChunkResponse,
    ChunkingDependencyError,
    ChunkingError,
    chunk_text,
)
from rag_eval_service import (
    RagEvaluationDependencyError,
    RagEvaluationError,
    RagEvaluationRequest,
    RagEvaluationResponse,
    evaluate_rag_records,
)


def _fal_source_enabled() -> bool:
    return (os.getenv("FAL_SOURCE", "disabled") or "disabled").strip().lower() == "enabled"


def _fal_api_key() -> str:
    return (os.getenv("FAL_API_KEY") or os.getenv("FAL_KEY") or "").strip()


def _require_fal_api_key() -> str:
    api_key = _fal_api_key()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FAL_SOURCE=enabled requires FAL_API_KEY",
        )
    return api_key


def _validate_uuid_param(value: str, name: str = "parameter"):
    """Validate a path parameter is a valid UUID, raise 400 if not."""
    try:
        _UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {name}: must be a valid UUID",
        )

# Get project name from environment
PROJECT_NAME = os.getenv("PROJECT_NAME", "atlas")

# Maximum body size for /storage/upload, in bytes. Default 100 MiB matches
# Supabase Storage's default object cap; operators can override via env.
# Without this guard `file.read()` will buffer arbitrarily large uploads
# into memory and OOM the worker.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))


def _parse_csv_env(value: str | None) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


BACKEND_CORS_ALLOW_ORIGIN_REGEX = os.getenv("BACKEND_CORS_ALLOW_ORIGIN_REGEX") or None


def _resolve_cors_origins(origins_value: str | None, origin_regex: str | None) -> List[str]:
    origins = _parse_csv_env(origins_value)
    if origin_regex:
        return [origin for origin in origins if origin != "*"]
    if origins:
        return origins
    return ["*"]


BACKEND_CORS_ORIGINS = _resolve_cors_origins(
    os.getenv("BACKEND_CORS_ORIGINS"),
    BACKEND_CORS_ALLOW_ORIGIN_REGEX,
)
BACKEND_STORAGE_ALLOWED_BUCKETS = set(
    _parse_csv_env(os.getenv("BACKEND_STORAGE_ALLOWED_BUCKETS")) or ["default"]
)
_SAFE_STORAGE_FILENAME = re.compile(r"^[A-Za-z0-9._ -]{1,255}$")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    # Graceful shutdown: close the process-lifetime n8n client so httpx
    # doesn't warn about an unclosed client and its keep-alive sockets
    # close deterministically. (n8n_client is the only long-lived HTTP
    # client; ComfyUIClient and the memory/research clients are per-call.)
    await n8n_client.aclose()


app = FastAPI(
    title=f"{PROJECT_NAME} Backend",
    description=f"Backend API for {PROJECT_NAME}",
    version="0.1.0",
    lifespan=_lifespan,
)

from observability import configure_otel  # noqa: E402
configure_otel(app)

# Prometheus metrics — emits standard HTTP server metrics
# (http_request_duration_seconds, http_requests_total by route/method/status).
# Scraped by the observability bundle's Prometheus at backend:8000/metrics.
# Always on; the endpoint sits unscraped when PROMETHEUS_SOURCE=disabled.
from prometheus_fastapi_instrumentator import Instrumentator  # noqa: E402
# excluded_handlers keeps /metrics and /health out of the request
# histogram (self-referential series + healthcheck noise pollute
# rate() queries). should_group_status_codes folds 2xx/3xx/4xx/5xx
# into class buckets, bounding the status_code label cardinality.
Instrumentator(
    excluded_handlers=["/metrics", "/health"],
    should_group_status_codes=True,
).instrument(app).expose(app, endpoint="/metrics")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=BACKEND_CORS_ORIGINS,
    allow_origin_regex=BACKEND_CORS_ALLOW_ORIGIN_REGEX,
    # NOTE: `allow_credentials=True` with the `*` wildcard origin is rejected by
    # all browsers (the spec forbids credentials + wildcard), so it would only
    # silently break credentialed requests. The backend is reached server-side
    # via Kong and does not rely on browser cookies, so credentials stay off
    # until specific origins are configured.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get environment variables
KONG_URL = os.getenv("KONG_URL")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not KONG_URL:
    raise ValueError("KONG_URL environment variable is required")
if not SERVICE_KEY:
    raise ValueError("SUPABASE_SERVICE_KEY environment variable is required")

# Construct Supabase Storage URL via Kong
# The standard path for storage via the gateway is /storage/v1.
# storage3 requires a trailing slash and warns + auto-corrects if it's
# missing — we set it explicitly to avoid the UserWarning at boot.
storage_url = f"{KONG_URL}/storage/v1/"

# Initialize Supabase Storage client
storage_client = StorageClient(
    url=storage_url,
    headers={
        "Authorization": f"Bearer {SERVICE_KEY}",
        "apikey": SERVICE_KEY,  # Supabase storage requires the service key as apikey header
    },
)


app.include_router(ray_router)
# Generic downstream extension seam — no-op unless a consumer mounts
# $BACKEND_PLUGINS_DIR with plugin packages. See plugin_seam.py.
from plugin_seam import load_plugins  # noqa: E402
# Inventory of mounted plugins (name, route prefix, health/docs, auth, env
# summary with secrets masked, load status). Populated at startup; served by
# GET /plugins so operators can see what is mounted and what env it declares.
PLUGIN_INVENTORY: List[Dict[str, Any]] = load_plugins(app)


class HealthResponse(BaseModel):
    status: str
    version: str


class PluginInventoryResponse(BaseModel):
    plugins: List[Dict[str, Any]]


@app.get("/plugins", response_model=PluginInventoryResponse, tags=["plugins"])
async def list_plugins() -> PluginInventoryResponse:
    """Inventory of mounted backend plugins (#402).

    Lists each discovered plugin's name, route prefix, health/docs metadata,
    per-plugin auth policy, declared env (secret values masked as ``***``), and
    load status (``loaded`` / ``skipped`` / ``error``). Manifest-less plugins
    appear with ``manifest: false`` and minimal metadata.
    """
    return PluginInventoryResponse(plugins=PLUGIN_INVENTORY)


class AsyncJobQueuedResponse(BaseModel):
    job_id: str
    status: str
    message: str
    task: str
    request: Dict[str, Any]


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    ready: bool
    successful: bool
    failed: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    traceback: Optional[str] = None


class StorageResponse(BaseModel):
    bucket: str
    path: str
    url: str


async def _read_upload_file_limited(
    file: UploadFile,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    chunks: List[bytes] = []
    total = 0
    chunk_size = 1024 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{label} exceeds maximum size of {max_bytes} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _document_max_file_size() -> int:
    config = getattr(document_extractor, "config", None)
    value = getattr(config, "max_file_size", None)
    if value is not None:
        return int(value)
    return int(os.getenv("TIKA_MAX_FILE_SIZE", str(50 * 1024 * 1024)))


def _validate_storage_target(bucket: str, filename: str) -> None:
    if bucket not in BACKEND_STORAGE_ALLOWED_BUCKETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bucket is not allowed",
        )
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or not _SAFE_STORAGE_FILENAME.fullmatch(filename)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename must be a safe object name without path separators",
        )


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for the API"""
    return HealthResponse(
        status="healthy",
        version="0.1.0",
    )


@app.get("/")
async def root():
    """Root endpoint that returns a welcome message"""
    return {
        "message": f"Welcome to the {PROJECT_NAME} Backend API",
        "docs_url": "/docs",
    }


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get Celery async-job state from the result backend."""
    if not celery_is_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Celery worker tier is disabled",
        )
    try:
        payload = await asyncio.to_thread(get_celery_job_status, job_id)
        return JobStatusResponse(**payload)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get job status: {str(e)}",
        )


# Initialize n8n client
n8n_client = N8nClient()

# Initialize research service
research_service = ResearchService()

# Initialize LangMem memory service
memory_service = MemoryService()

# Initialize document extractor facade (Docling first, Tika fallback)
document_extractor = DocumentExtractor()



class WorkflowResponse(BaseModel):
    """Response model for workflow operations.

    n8n's public API emits camelCase timestamps — validation aliases map
    them in while serialization keeps the snake_case response surface
    (FastAPI serializes by alias by default, so a plain `alias=` would
    have flipped the wire format to camelCase)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    active: bool
    created_at: Optional[str] = Field(default=None, validation_alias="createdAt")
    updated_at: Optional[str] = Field(default=None, validation_alias="updatedAt")



@app.get("/workflows", response_model=List[WorkflowResponse])
async def list_workflows():
    """List all n8n workflows"""
    try:
        workflows = await n8n_client.list_workflows()
        return workflows
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list workflows: {str(e)}",
        )


@app.get("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str):
    """Get a specific n8n workflow by ID"""
    try:
        workflow = await n8n_client.get_workflow(workflow_id)
        return workflow
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow with ID {workflow_id} not found",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get workflow: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get workflow: {str(e)}",
        )



@app.post("/storage/upload", response_model=StorageResponse)
async def upload_file(file: UploadFile = File(...), bucket: str = "default"):
    """Upload a file to Supabase Storage"""
    try:
        # Ensure filename exists and cast to str to satisfy type checker
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must have a filename",
            )
        filename = cast(str, file.filename)
        _validate_storage_target(bucket, filename)

        # Read file content in bounded chunks so an oversized upload fails
        # cleanly with 413 instead of OOMing the worker. UploadFile's
        # SpooledTemporaryFile is iterated, not materialized whole.
        content = await _read_upload_file_limited(
            file,
            max_bytes=MAX_UPLOAD_BYTES,
            label="File",
        )

        try:
            # Upload to storage. storage3 exposes upload/get_public_url on
            # the per-bucket proxy (from_), not on the client itself.
            bucket_ref = storage_client.from_(bucket)
            # storage3's SyncStorageClient does blocking network I/O; run it off
            # the event loop so a slow/large upload doesn't stall the worker and
            # every other in-flight request with it.
            await asyncio.to_thread(
                bucket_ref.upload,
                path=filename,
                file=content,
                file_options={"content-type": file.content_type}
                if file.content_type
                else None,
            )

            # Get public URL
            url = await asyncio.to_thread(bucket_ref.get_public_url, filename)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase Storage is unavailable",
            ) from e

        return StorageResponse(bucket=bucket, path=filename, url=url)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@app.post("/documents/extract")
async def extract_document(file: UploadFile = File(...)):
    """Extract text for RAG ingestion using Docling first, then Tika fallback."""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must have a filename",
        )
    content = await _read_upload_file_limited(
        file,
        max_bytes=_document_max_file_size(),
        label="Document",
    )
    try:
        result = await document_extractor.extract(
            content=content,
            filename=file.filename,
            content_type=file.content_type,
        )
        return result.to_dict()
    except DocumentTooLargeError as e:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(e),
        )
    except ExtractionUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except DocumentExtractionError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@app.post("/api/chunk", response_model=ChunkResponse)
async def chunk_document_text(request: ChunkRequest):
    """Chunk text for RAG ingestion using Chonkie-backed strategies."""
    try:
        return await asyncio.to_thread(chunk_text, request)
    except ChunkingDependencyError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except ChunkingError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@app.post("/api/rag/evaluate", response_model=RagEvaluationResponse)
async def evaluate_rag_quality(request: RagEvaluationRequest):
    """Evaluate supplied RAG answers and contexts with Ragas metrics."""
    try:
        return await asyncio.to_thread(evaluate_rag_records, request)
    except RagEvaluationDependencyError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except (RagEvaluationError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


# ─── RAG ingestion job contract (#413) ───────────────────────────────
# Atlas owns the repeatable ingestion lifecycle; a consumer declares a
# rag_ingestion_profile and submits jobs headlessly. A single process-wide
# service holds the store (Redis when configured, else in-memory) so the submit
# endpoint and the synchronous fallback share state; the Celery worker rebuilds
# its own service against the same Redis when the async path is used.
_rag_ingestion_service: Optional[RagIngestionService] = None


def get_rag_ingestion_service() -> RagIngestionService:
    global _rag_ingestion_service
    if _rag_ingestion_service is None:
        _rag_ingestion_service = RagIngestionService()
    return _rag_ingestion_service


@app.post(
    "/api/rag/ingestions",
    response_model=RagIngestionQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_rag_ingestion(request: RagIngestionRequest, async_job: bool = True):
    """Submit a RAG ingestion job for a declared profile (#413).

    ``async_job=true`` (default) dispatches the Celery worker when the tier is
    enabled; otherwise the job runs synchronously in-request. Idempotent by
    consumer + profile revision + corpus digest: a re-submit of the same corpus
    returns the existing job without re-running it.
    """
    service = get_rag_ingestion_service()
    try:
        record, created = service.submit(request.profile, corpus_path=request.corpus_path)
    except ProfileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown rag_ingestion profile: {request.profile!r}",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if not created:
        return RagIngestionQueuedResponse(
            ingestion_id=record.id,
            job_id=None,
            status=record.status,
            message="Idempotent: existing ingestion returned (not re-run).",
        )

    if async_job and celery_is_enabled():
        try:
            task = await asyncio.to_thread(
                rag_ingestion_task.apply_async, kwargs={"ingestion_id": record.id}
            )
        except Exception as e:  # noqa: BLE001 - broker unreachable
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to queue RAG ingestion: {str(e)}",
            )
        return RagIngestionQueuedResponse(
            ingestion_id=record.id,
            job_id=task.id,
            status="pending",
            message="RAG ingestion queued.",
        )

    # Synchronous fallback (Celery disabled or async_job=false).
    final = await service.run(record.id)
    return RagIngestionQueuedResponse(
        ingestion_id=final.id,
        job_id=None,
        status=final.status,
        message="RAG ingestion completed synchronously.",
    )


@app.get("/api/rag/ingestions", response_model=List[RagIngestionRecordResponse])
async def list_rag_ingestions():
    """List RAG ingestion jobs (machine-readable)."""
    service = get_rag_ingestion_service()
    return [RagIngestionRecordResponse(**r.to_dict()) for r in service.store.list()]


@app.get("/api/rag/ingestions/{ingestion_id}", response_model=RagIngestionRecordResponse)
async def get_rag_ingestion(ingestion_id: str):
    """Return the durable, machine-readable state of one ingestion job."""
    service = get_rag_ingestion_service()
    record = service.store.get(ingestion_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown ingestion id: {ingestion_id!r}",
        )
    return RagIngestionRecordResponse(**record.to_dict())


@app.post("/api/rag/ingestions/{ingestion_id}/cancel", response_model=RagIngestionRecordResponse)
async def cancel_rag_ingestion(ingestion_id: str):
    """Request cooperative cancellation of a running ingestion job."""
    service = get_rag_ingestion_service()
    if not service.store.request_cancel(ingestion_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown ingestion id: {ingestion_id!r}",
        )
    return RagIngestionRecordResponse(**service.store.get(ingestion_id).to_dict())


# Research API Models
class ResearchStartRequest(BaseModel):
    """Request model for starting research"""
    query: str = Field(min_length=1, max_length=4000)
    max_loops: Optional[int] = Field(default=3, ge=1, le=10)
    search_api: Optional[Literal["duckduckgo", "searxng"]] = "searxng"
    user_id: Optional[str] = None


class ResearchResponse(BaseModel):
    """Response model for research operations"""
    session_id: str
    status: str
    message: str
    data: Optional[Dict[str, Any]] = None


class ResearchSessionResponse(BaseModel):
    """Response model for research session details"""
    session_id: str
    query: str
    status: str
    max_loops: int
    search_api: str
    user_id: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


class ResearchResultResponse(BaseModel):
    """Response model for research results"""
    session_id: str
    result_id: str
    title: str
    summary: str
    content: str
    sources: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    created_at: str
    status: str


class ResearchLogResponse(BaseModel):
    """Response model for research logs"""
    step_number: int
    step_type: str
    message: str
    data: Optional[Dict[str, Any]] = None
    timestamp: str


# Research API Endpoints
@app.post("/research/start", response_model=ResearchResponse)
async def start_research(request: ResearchStartRequest):
    """Start a new research session"""
    # Validate user_id like every other user-id-bearing route (the other
    # research routes call _validate_uuid_param too) — without this an
    # invalid user_id reaches UUID() in research_service and surfaces as an
    # opaque 500 instead of a clean 400.
    if request.user_id is not None:
        _validate_uuid_param(request.user_id, "user_id")
    try:
        result = await research_service.start_research(
            query=request.query,
            max_loops=request.max_loops or 3,
            search_api=request.search_api or "searxng",
            user_id=request.user_id
        )
        return ResearchResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start research: {str(e)}"
        )


@app.get("/research/{session_id}/status", response_model=ResearchSessionResponse)
async def get_research_status(session_id: str):
    """Get the status of a research session"""
    _validate_uuid_param(session_id, "session_id")
    try:
        result = await research_service.get_research_status(session_id)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Research session {session_id} not found"
            )
        return ResearchSessionResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get research status: {str(e)}"
        )


@app.get("/research/{session_id}/result", response_model=ResearchResultResponse)
async def get_research_result(session_id: str):
    """Get the result of a completed research session"""
    _validate_uuid_param(session_id, "session_id")
    try:
        result = await research_service.get_research_result(session_id)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Research result for session {session_id} not found"
            )
        return ResearchResultResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get research result: {str(e)}"
        )


@app.post("/research/{session_id}/cancel", response_model=ResearchResponse)
async def cancel_research(session_id: str):
    """Cancel a running research session"""
    _validate_uuid_param(session_id, "session_id")
    try:
        success = await research_service.cancel_research(session_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel research session {session_id} - session not found or not running"
            )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=ResearchResponse(
                session_id=session_id,
                status="cancel_requested",
                message=(
                    "Local research task cancellation requested; remote "
                    "LangGraph cancellation is not supported by this integration"
                ),
            ).model_dump(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel research: {str(e)}"
        )


@app.get("/research/{session_id}/logs", response_model=List[ResearchLogResponse])
async def get_research_logs(session_id: str):
    """Get logs for a research session"""
    _validate_uuid_param(session_id, "session_id")
    try:
        logs = await research_service.get_research_logs(session_id)
        if logs is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Research session {session_id} not found",
            )
        return [ResearchLogResponse(**log) for log in logs]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get research logs: {str(e)}"
        )


@app.get("/research/sessions", response_model=List[ResearchSessionResponse])
async def list_research_sessions(
    user_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0)
):
    """List research sessions"""
    if user_id is not None:
        _validate_uuid_param(user_id, "user_id")
    try:
        sessions = await research_service.list_user_sessions(
            user_id=user_id,
            limit=limit,
            offset=offset,
        )
        return [ResearchSessionResponse(**session) for session in sessions]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list research sessions: {str(e)}"
        )


@app.get("/research/health")
async def research_health_check():
    """Health check for research service"""
    try:
        health = await research_service.health_check()
        return {
            "service": "research",
            "status": "healthy" if health["database"] == "healthy" else "degraded",
            "details": health
        }
    except Exception as e:
        return {
            "service": "research", 
            "status": "unhealthy",
            "error": str(e)
        }


# ComfyUI API Models
class ComfyUIGenerateRequest(BaseModel):
    """Request model for ComfyUI image generation"""
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: Optional[str] = Field(default="", max_length=4000)
    width: int = Field(default=512, ge=64, le=4096)
    height: int = Field(default=512, ge=64, le=4096)
    steps: int = Field(default=20, ge=1, le=150)
    cfg: float = Field(default=7.0, ge=0.0, le=30.0)
    seed: Optional[int] = None
    checkpoint: Optional[str] = Field(default="v1-5-pruned-emaonly.safetensors", max_length=255)
    wait_for_completion: bool = True


class ComfyUIWorkflowRequest(BaseModel):
    """Request model for custom ComfyUI workflow"""
    workflow: Dict[str, Any] = Field(min_length=1, max_length=500)
    wait_for_completion: bool = True


class ComfyUIResponse(BaseModel):
    """Response model for ComfyUI operations"""
    success: bool
    prompt_id: Optional[str] = None
    client_id: Optional[str] = None
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class MediaGenerateRequest(BaseModel):
    """Provider-neutral media generation request."""

    modality: str = Field(min_length=1, max_length=64)
    provider: str = Field(default="fal", min_length=1, max_length=64)
    model: Optional[str] = Field(default=None, max_length=255)
    input: Dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=3600)


class MediaOperationResponse(BaseModel):
    """Normalized media operation status and artifact envelope."""

    operation_id: str
    status: str
    provider: str
    model: str
    modality: str
    operation_url: Optional[str] = None
    artifact_url: Optional[str] = None
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    cost_usd: Optional[float] = None
    license: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)
    raw: Optional[Dict[str, Any]] = None


MEDIA_OPERATIONS: Dict[str, Dict[str, Any]] = {}


def _media_timeout_seconds(request_timeout: Optional[int] = None) -> int:
    if request_timeout is not None:
        return request_timeout
    try:
        return int(os.getenv("FAL_TIMEOUT_SECONDS", "120") or "120")
    except ValueError:
        return 120


def _normalize_media_route(provider: str, modality: str, model: Optional[str]) -> tuple[str, str, str]:
    normalized_provider = (provider or "").strip().lower()
    normalized_modality = (modality or "").strip().lower()
    selected_model = (model or os.getenv("FAL_MODEL") or "fal-ai/flux/dev").strip()
    if normalized_provider == "fal" and normalized_modality == "image":
        return normalized_provider, normalized_modality, selected_model
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "Unsupported media route: Atlas currently supports "
            "provider=fal with modality=image"
        ),
    )


def _media_response(payload: Dict[str, Any]) -> MediaOperationResponse:
    operation_id = str(payload["operation_id"])
    return MediaOperationResponse(
        operation_id=operation_id,
        status=str(payload.get("status", "unknown")),
        provider=str(payload.get("provider", "unknown")),
        model=str(payload.get("model", "unknown")),
        modality=str(payload.get("modality", "unknown")),
        operation_url=f"/media/operations/{operation_id}",
        artifact_url=payload.get("artifact_url"),
        artifacts=list(payload.get("artifacts") or []),
        cost_usd=payload.get("cost_usd"),
        license=payload.get("license"),
        provenance=dict(payload.get("provenance") or {}),
        raw=payload.get("raw"),
    )


# ComfyUI API Endpoints
@app.get("/comfyui/health")
async def comfyui_health_check():
    """Health check for the configured image generation provider."""
    if _fal_source_enabled():
        if _fal_api_key():
            return {
                "service": "fal",
                "status": "healthy",
                "details": {
                    "provider": "fal",
                    "model": os.getenv("FAL_MODEL", "fal-ai/flux/dev"),
                },
            }
        return {
            "service": "fal",
            "status": "unhealthy",
            "error": "FAL_SOURCE=enabled requires FAL_API_KEY",
        }

    try:
        async with ComfyUIClient() as client:
            health = await client.health_check()
            return {
                "service": "comfyui",
                "status": health.get("status", "unknown"),
                "details": health
            }
    except Exception as e:
        return {
            "service": "comfyui",
            "status": "unhealthy", 
            "error": str(e)
        }


@app.get("/comfyui/models")
async def get_comfyui_models():
    """Get available ComfyUI models"""
    try:
        async with ComfyUIClient() as client:
            models = await client.get_models()
            return {
                "success": True,
                "models": models
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get ComfyUI models: {str(e)}"
        )


@app.post(
    "/media/generate",
    response_model=MediaOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_media_generation(request: MediaGenerateRequest):
    """Submit a provider-neutral hosted media generation operation."""
    provider, modality, model = _normalize_media_route(
        request.provider,
        request.modality,
        request.model,
    )
    if provider == "fal" and not _fal_source_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FAL_SOURCE=enabled is required for provider=fal media generation",
        )
    if "prompt" not in request.input:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Media input must include prompt for modality=image",
        )

    api_key = _require_fal_api_key()
    try:
        async with FalClient(api_key=api_key, model=model) as client:
            payload = await client.submit_media_operation(
                modality=modality,
                input=request.input,
                model=model,
            )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to submit media generation with FAL: {str(e)}",
        )

    operation_id = str(payload["operation_id"])
    MEDIA_OPERATIONS[operation_id] = {
        "provider": provider,
        "modality": modality,
        "model": model,
        "created_at": time.monotonic(),
        "timeout_seconds": _media_timeout_seconds(request.timeout_seconds),
        "last_payload": payload,
    }
    return _media_response(payload)


@app.get("/media/operations/{operation_id}", response_model=MediaOperationResponse)
async def get_media_operation(operation_id: str):
    """Poll a hosted media generation operation."""
    operation = MEDIA_OPERATIONS.get(operation_id)
    if not operation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media operation {operation_id} not found",
        )
    elapsed = time.monotonic() - float(operation["created_at"])
    if elapsed > int(operation["timeout_seconds"]):
        payload = dict(operation["last_payload"])
        payload["status"] = "timeout"
        MEDIA_OPERATIONS[operation_id]["last_payload"] = payload
        return _media_response(payload)

    provider = operation["provider"]
    if provider != "fal":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported media provider for polling: {provider}",
        )
    if not _fal_source_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FAL_SOURCE=enabled is required to poll FAL media operations",
        )

    api_key = _require_fal_api_key()
    try:
        async with FalClient(api_key=api_key, model=operation["model"]) as client:
            payload = await client.get_media_operation(
                operation_id=operation_id,
                modality=operation["modality"],
            )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to poll media operation with FAL: {str(e)}",
        )

    MEDIA_OPERATIONS[operation_id]["last_payload"] = payload
    return _media_response(payload)


@app.post("/comfyui/generate", response_model=ComfyUIResponse)
async def generate_image(request: ComfyUIGenerateRequest):
    """Generate an image using the configured media provider."""
    if _fal_source_enabled():
        api_key = _require_fal_api_key()
        try:
            async with FalClient(api_key=api_key) as client:
                result = await client.generate_simple_image(
                    prompt=request.prompt,
                    negative_prompt=request.negative_prompt,
                    width=request.width,
                    height=request.height,
                    steps=request.steps,
                    cfg=request.cfg,
                    seed=request.seed,
                    checkpoint=request.checkpoint,
                )

            if not result.get("success"):
                return ComfyUIResponse(
                    success=False,
                    error=result.get("error", "Unknown error"),
                )

            return ComfyUIResponse(
                success=True,
                prompt_id=result.get("prompt_id"),
                client_id=result.get("client_id", "fal"),
                message="Image generated successfully",
                data={
                    "provider": "fal",
                    "outputs": result.get("outputs", {}),
                    "parameters": result.get("parameters", {}),
                    "raw": result.get("raw", {}),
                },
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate image with FAL: {str(e)}",
            )

    try:
        async with ComfyUIClient() as client:
            # Generate the image
            result = await client.generate_simple_image(
                prompt=request.prompt,
                negative_prompt=request.negative_prompt,
                width=request.width,
                height=request.height,
                steps=request.steps,
                cfg=request.cfg,
                seed=request.seed,
                checkpoint=request.checkpoint
            )
            
            if not result.get("success"):
                return ComfyUIResponse(
                    success=False,
                    error=result.get("error", "Unknown error")
                )
            
            prompt_id = result["prompt_id"]
            
            # If wait_for_completion is True, wait for the image to be generated
            if request.wait_for_completion:
                completion_result = await client.wait_for_completion(prompt_id)
                
                if completion_result.get("success"):
                    return ComfyUIResponse(
                        success=True,
                        prompt_id=prompt_id,
                        client_id=result["client_id"],
                        message="Image generated successfully",
                        data={
                            "outputs": completion_result["outputs"],
                            "parameters": result["parameters"]
                        }
                    )
                else:
                    return ComfyUIResponse(
                        success=False,
                        prompt_id=prompt_id,
                        error=completion_result.get("error", "Generation failed")
                    )
            else:
                # Return immediately with prompt ID
                return ComfyUIResponse(
                    success=True,
                    prompt_id=prompt_id,
                    client_id=result["client_id"],
                    message="Image generation queued",
                    data={"parameters": result["parameters"]}
                )
                
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate image: {str(e)}"
        )


@app.post("/comfyui/workflow", response_model=ComfyUIResponse)
async def execute_comfyui_workflow(request: ComfyUIWorkflowRequest):
    """Execute a custom ComfyUI workflow"""
    try:
        async with ComfyUIClient() as client:
            # Queue the workflow
            result = await client.queue_prompt(request.workflow)
            
            if not result.get("success"):
                return ComfyUIResponse(
                    success=False,
                    error=result.get("error", "Unknown error")
                )
            
            prompt_id = result["prompt_id"]
            
            # If wait_for_completion is True, wait for the workflow to complete
            if request.wait_for_completion:
                completion_result = await client.wait_for_completion(prompt_id)
                
                if completion_result.get("success"):
                    return ComfyUIResponse(
                        success=True,
                        prompt_id=prompt_id,
                        client_id=result["client_id"],
                        message="Workflow executed successfully",
                        data={"outputs": completion_result["outputs"]}
                    )
                else:
                    return ComfyUIResponse(
                        success=False,
                        prompt_id=prompt_id,
                        error=completion_result.get("error", "Workflow execution failed")
                    )
            else:
                # Return immediately with prompt ID
                return ComfyUIResponse(
                    success=True,
                    prompt_id=prompt_id,
                    client_id=result["client_id"],
                    message="Workflow queued"
                )
                
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute workflow: {str(e)}"
        )


@app.get("/comfyui/history/{prompt_id}")
async def get_generation_history(prompt_id: str):
    """Get ComfyUI generation history for a specific prompt"""
    try:
        async with ComfyUIClient() as client:
            history = await client.get_history(prompt_id)
            return {
                "success": True,
                "history": history
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get history: {str(e)}"
        )


@app.get("/comfyui/queue")
async def get_queue_status():
    """Get ComfyUI queue status"""
    try:
        async with ComfyUIClient() as client:
            queue = await client.get_queue_status()
            return {
                "success": True,
                "queue": queue
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get queue status: {str(e)}"
        )


@app.post("/comfyui/cancel/{prompt_id}")
async def cancel_generation(prompt_id: str):
    """Cancel a ComfyUI generation"""
    try:
        async with ComfyUIClient() as client:
            success = await client.cancel_prompt(prompt_id)
            return {
                "success": success,
                "message": "Generation cancelled" if success else "Failed to cancel generation"
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel generation: {str(e)}"
        )


@app.get("/comfyui/image/{filename}")
async def get_generated_image(filename: str, subfolder: str = "", folder_type: str = "output"):
    """Get a generated image from ComfyUI"""
    try:
        async with ComfyUIClient() as client:
            image_data = await client.get_image_data(filename, subfolder, folder_type)
            
            # Determine content type based on file extension
            content_type = "image/png"
            if filename.lower().endswith(('.jpg', '.jpeg')):
                content_type = "image/jpeg"
            elif filename.lower().endswith('.webp'):
                content_type = "image/webp"
            
            from fastapi.responses import Response
            # Sanitize the filename before placing it in a header: strip CR/LF
            # (which crash the HTTP/1.1 codec with a 500) and quote per RFC 6266
            # so a name containing ';' or '"' can't break the header structure.
            safe_name = filename.replace("\r", "").replace("\n", "").replace('"', "")
            return Response(
                content=image_data,
                media_type=content_type,
                headers={"Content-Disposition": f'inline; filename="{safe_name}"'}
            )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Image {filename} not found",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get image: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get image: {str(e)}"
        )


# ComfyUI Model Management Endpoints
@app.get("/comfyui/db/models")
async def get_comfyui_db_models(active_only: bool = True, essential_only: bool = False):
    """Get ComfyUI models from the manifest file written by the bootstrapper at startup.

    Reads COMFYUI_MANIFEST_PATH (default /comfyui-manifest/selected-models.yaml),
    which is generated by the bootstrapper's comfyui_resolver at every stack start.
    This route does NOT open a database connection.

    Returns the same response shape as before — {"success": True, "models": [...]} —
    so existing consumers (Open WebUI tool, n8n workflow) remain byte-compatible.

    If the manifest file is missing (e.g. ComfyUI is disabled or not yet started),
    returns an empty models list with HTTP 200 rather than erroring.
    """
    manifest_path = os.getenv(
        "COMFYUI_MANIFEST_PATH", "/comfyui-manifest/selected-models.yaml"
    )
    try:
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = yaml.safe_load(fh) or {}
        except (FileNotFoundError, yaml.YAMLError):
            # Missing OR corrupt/partial manifest — graceful empty list, not 500
            # (consumers — the Open WebUI tool, n8n — don't handle a 500 well).
            return {"success": True, "models": []}

        models: List[Dict[str, Any]] = manifest.get("models", [])

        # The manifest contains only active models (bootstrapper writes them
        # all as active=true).  Honor the query params for forward-compat.
        if essential_only:
            models = [m for m in models if m.get("essential")]

        # active_only=true is the only real call path (both consumers use it);
        # since the manifest already contains only active entries, this is a
        # no-op — but applying it explicitly keeps semantics correct if the
        # manifest format ever gains inactive entries.
        if active_only:
            models = [m for m in models if m.get("active", True)]

        return {"success": True, "models": models}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read ComfyUI manifest: {str(e)}",
        )


# =============================================================================
# LangMem Memory API Endpoints
# =============================================================================

@app.post("/memory/extract", response_model=MemoryExtractResponse)
async def memory_extract(request: MemoryExtractRequest):
    """Extract and store memory facts from conversation messages."""
    try:
        result = await memory_service.extract_facts(
            user_id=request.user_id,
            messages=[message.model_dump() for message in request.messages],
            namespace=request.namespace,
            conversation_id=request.conversation_id,
        )
        return MemoryExtractResponse(**result)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extract memories: {str(e)}",
        )


@app.post("/memory/recall", response_model=MemoryRecallResponse)
async def memory_recall(request: MemoryRecallRequest):
    """Recall relevant memories for a query using semantic search."""
    try:
        result = await memory_service.recall(
            user_id=request.user_id,
            query=request.query,
            namespace=request.namespace,
            limit=request.limit,
            min_confidence=request.min_confidence,
        )
        return MemoryRecallResponse(**result)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to recall memories: {str(e)}",
        )


@app.post(
    "/memory/consolidate",
    response_model=Union[MemoryConsolidateResponse, AsyncJobQueuedResponse],
)
async def memory_consolidate(
    request: MemoryConsolidateRequest, async_job: bool = False
):
    """Consolidate and deduplicate user memories."""
    if async_job:
        if not celery_is_enabled():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Celery worker tier is disabled",
            )
        try:
            task = await asyncio.to_thread(
                memory_consolidate_task.apply_async,
                kwargs={"user_id": request.user_id}
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to queue memory consolidation: {str(e)}",
            )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=AsyncJobQueuedResponse(
                job_id=task.id,
                status="pending",
                message="Memory consolidation queued",
                task="memory_consolidate",
                request={"user_id": request.user_id},
            ).model_dump(),
        )

    try:
        result = await memory_service.consolidate(user_id=request.user_id)
        return MemoryConsolidateResponse(**result)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to consolidate memories: {str(e)}",
        )


@app.post("/memory/summarize", response_model=MemorySummarizeResponse)
async def memory_summarize(request: MemorySummarizeRequest):
    """Generate a natural-language summary of a user's memory profile."""
    try:
        result = await memory_service.summarize(
            user_id=request.user_id, namespace=request.namespace
        )
        return MemorySummarizeResponse(**result)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to summarize memories: {str(e)}",
        )


@app.get("/memory/user/{user_id}", response_model=MemoryListResponse)
async def memory_list(
    user_id: str,
    namespace: str = Query(default="default", min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """List all active memories for a user."""
    _validate_uuid_param(user_id, "user_id")
    try:
        result = await memory_service.list_memories(
            user_id=user_id,
            namespace=namespace,
            limit=limit,
            offset=offset,
        )
        return MemoryListResponse(**result)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list memories: {str(e)}",
        )


@app.put("/memory/{memory_id}", response_model=Dict[str, Any])
async def memory_update(
    memory_id: str,
    request: MemoryUpdateRequest,
    user_id: str = Query(...),
):
    """Update a specific memory fact."""
    _validate_uuid_param(memory_id, "memory_id")
    _validate_uuid_param(user_id, "user_id")
    try:
        updates = request.model_dump(exclude_none=True)
        result = await memory_service.update_memory(memory_id, user_id, updates)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory {memory_id} not found",
            )
        return {"success": True, "memory": result}
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update memory: {str(e)}",
        )


@app.delete("/memory/{memory_id}", response_model=Dict[str, Any])
async def memory_delete(memory_id: str, user_id: str = Query(...)):
    """Delete (deactivate) a specific memory fact."""
    _validate_uuid_param(memory_id, "memory_id")
    _validate_uuid_param(user_id, "user_id")
    try:
        success = await memory_service.delete_memory(memory_id, user_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory {memory_id} not found",
            )
        return {"success": True, "message": "Memory deleted successfully"}
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete memory: {str(e)}",
        )


@app.get("/memory/health", response_model=MemoryHealthResponse)
async def memory_health_check():
    """Health check for the LangMem memory service."""
    result = await memory_service.health_check()
    return MemoryHealthResponse(**result)


@app.get("/memory/graphiti/status", response_model=Dict[str, Any])
async def graphiti_experiment_status():
    """Report the disabled-by-default backend-only Graphiti experiment plan."""
    return GraphitiExperimentConfig.from_env().status_payload()
