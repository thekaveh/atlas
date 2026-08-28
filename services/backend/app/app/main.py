from fastapi import FastAPI, HTTPException, status, UploadFile, File, Query, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from storage3 import SyncStorageClient as StorageClient
from typing import Optional, cast, Dict, Any, List, Union, Literal
from contextlib import asynccontextmanager
import os
import asyncio
import hashlib
import logging
import math
import httpx
import asyncpg
import yaml
import re
import time
import secrets
import sys
import traceback
from datetime import datetime, timezone
from decimal import Decimal

from n8n_client import N8nClient
from research_service import ResearchCapacityError, ResearchService
from comfyui_client import ComfyUIClient
from comfyui_media_client import ComfyUIMediaClient
from fal_media_client import (
    FalClient,
    FalSubmissionAmbiguousError,
    fal_timeout_seconds_from_env,
    preflight_media_operation,
    validate_fal_config,
    validate_image_request_shape,
)
import media_registry
from media_input import (
    ImageHostingError,
    ImageInputError,
    prepare_image_input,
    validate_media_input_config,
)
import media_ledger
from media_request_limit import (
    MediaRequestLimitMiddleware,
    media_request_max_bytes_from_env,
)
from media_operation_store import (
    MediaOperationCollisionError,
    TERMINAL_MEDIA_STATUSES,
    build_media_operation_store,
)
from media_ledger import (
    BudgetExceeded,
    LedgerOperationCollisionError,
    ProviderDisabled,
    UnknownCostRejected,
)
from uuid import UUID as _UUID, uuid4
from memory_service import MemoryService
from memory_models import (
    MemoryExtractRequest, MemoryRecallRequest, MemoryConsolidateRequest,
    MemorySummarizeRequest, MemoryUpdateRequest,
    MemoryFact, MemoryExtractResponse, MemoryRecallResponse,
    MemoryConsolidateResponse, MemorySummarizeResponse,
    MemoryListResponse, MemoryHealthResponse,
)
from ray_routes import router as ray_router
from celery_app import (
    celery_is_enabled,
    get_celery_job_status,
)
from celery_tasks import memory_consolidate_task, rag_ingestion_task
from rag_ingestion import (
    ingestion_execution_lease_seconds,
    IngestionExecutionBusy,
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
from lightrag_rerank_adapter import (
    RerankAdapterRequest,
    RerankAdapterResponse,
    RerankAdapterDependencyError,
    RerankAdapterTimeoutError,
    RerankAdapterUpstreamError,
    rerank_via_tei,
    validate_rerank_adapter_config,
)
from backend_identity import (
    BackendPrincipal,
    _ct_equals,
    authorize_media_scope,
    authorize_user_id,
    principal_scope_key,
    require_backend_principal,
    require_comfy_automation_principal,
    require_comfy_read_principal,
    require_memory_automation_principal,
    require_memory_principal,
    require_n8n_operator_principal,
    require_research_principal,
    require_service_principal,
    require_stateless_principal,
    research_owner_id,
)
from readiness import check_backend_readiness
from access_log import configure_uvicorn_access_log_redaction


logger = logging.getLogger(__name__)
configure_uvicorn_access_log_redaction()


def _unexpected_error(operation: str, exc: Exception, *, status_code: int = 500) -> HTTPException:
    """Log an unexpected failure without exposing its details to API clients."""
    stack = " <- ".join(
        f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)
    ) or "unavailable"
    logger.error(
        "%s failed (error_type=%s, stack=%s)",
        operation,
        type(exc).__name__,
        stack,
    )
    return HTTPException(status_code=status_code, detail=f"{operation} failed")


def _fal_source_enabled() -> bool:
    return (os.getenv("FAL_SOURCE", "disabled") or "disabled").strip().lower() == "enabled"


def _comfyui_media_enabled() -> bool:
    """Gate for ``provider=comfyui`` media generation (#519).

    Source-aware, mirroring ``_fal_source_enabled``: ``COMFYUI_SOURCE`` is
    plumbed into the backend container by the compose fragment
    (``${COMFYUI_SOURCE:-disabled}``), so the gateway offers the provider
    only when ComfyUI is actually configured (any non-disabled source). An
    unreachable host still surfaces as a 502 at submit time (the honest
    failure mode for a down local service); this gate is the clean 503 for
    "ComfyUI not configured".
    """
    source = (os.getenv("COMFYUI_SOURCE", "disabled") or "disabled").strip().lower()
    return source not in {"disabled", "none", ""}


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
def _positive_byte_cap(name: str, default: int) -> int:
    """A byte cap from the environment, falling back loudly rather than dying.

    A bare `int(os.getenv(...))` crashed the whole service at IMPORT time on a
    typo, and accepted `0`, which rejects every upload — a cap that silently
    means "nothing may be uploaded" is worse than no cap.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using default %d", name, raw, default)
        return default
    if parsed <= 0:
        logger.warning("%s=%d must be positive; using default %d", name, parsed, default)
        return default
    return parsed


MAX_UPLOAD_BYTES = _positive_byte_cap("MAX_UPLOAD_BYTES", 100 * 1024 * 1024)


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


# Background reclamation for abandoned media-budget reservations. The poll-time
# timeout only fires when a client polls; a submitted operation that is never
# polled again would otherwise keep its RESERVED/SUBMITTED ledger row counting
# against the consumer's cap forever. prune_expired() sweeps rows older than the
# retention window (a no-op unless MEDIA_BUDGET_RETENTION_DAYS is set); schedule
# it so the advertised backstop actually runs.
_MEDIA_BUDGET_PRUNE_INTERVAL_SECONDS = 3600
_MEDIA_LEDGER_INTENT_INTERVAL_SECONDS = 30
_media_budget_prune_task: Optional[asyncio.Task] = None
_media_ledger_intent_task: Optional[asyncio.Task] = None


async def _media_budget_prune_loop() -> None:
    while True:
        await asyncio.sleep(_MEDIA_BUDGET_PRUNE_INTERVAL_SECONDS)
        try:
            pruned = await MEDIA_BUDGET_ENGINE.prune_expired()
            if pruned:
                logger.info(
                    "media budget prune reclaimed %s expired ledger row(s)", pruned
                )
        except Exception as exc:
            logger.warning(
                "media budget prune sweep failed (error_type=%s)",
                type(exc).__name__,
            )


async def _media_ledger_intent_loop() -> None:
    """Drain durable attach/cleanup intents even when their owner never polls."""
    while True:
        await asyncio.sleep(_MEDIA_LEDGER_INTENT_INTERVAL_SECONDS)
        try:
            operations = await MEDIA_OPERATION_STORE.pending_ledger_intents()
        except Exception as exc:
            logger.warning(
                "media ledger intent scan failed (error_type=%s)",
                type(exc).__name__,
            )
            continue
        for operation in operations:
            operation_id = str(operation.get("operation_id") or "")
            if not operation_id:
                continue
            try:
                recovered = await _maybe_recover_media_ledger_intent(
                    operation_id, operation
                )
                await _maybe_reconcile_ledger(operation_id, recovered)
            except Exception as exc:
                logger.warning(
                    "media ledger intent recovery failed for %s (error_type=%s)",
                    operation_id,
                    type(exc).__name__,
                )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _media_budget_prune_task, _media_ledger_intent_task
    # #804: pre-warm the shared asyncpg pool at startup, best-effort — if the DB
    # is not reachable yet, the pool is created lazily on first use rather than
    # failing app startup (keeps the TestClient/unit path working too).
    from db_connection import PoolConfigurationError, close_pg_pools, get_pg_pool
    _db_url = os.getenv("DATABASE_URL")
    if _db_url:
        try:
            await get_pg_pool(_db_url)
        except PoolConfigurationError:
            raise
        except Exception:  # noqa: BLE001 — startup must not hard-fail on DB warmup
            logger.warning("PG pool pre-warm failed; will create lazily on first use")
    await research_service.start_maintenance()
    if MEDIA_BUDGET_ENGINE.config.retention_days:
        _media_budget_prune_task = asyncio.create_task(_media_budget_prune_loop())
    _media_ledger_intent_task = asyncio.create_task(_media_ledger_intent_loop())
    try:
        yield
    finally:
        # Terminalize local research work before closing process-lifetime
        # clients and shared stores.
        if _media_budget_prune_task is not None:
            _media_budget_prune_task.cancel()
            await asyncio.gather(_media_budget_prune_task, return_exceptions=True)
            _media_budget_prune_task = None
        if _media_ledger_intent_task is not None:
            _media_ledger_intent_task.cancel()
            await asyncio.gather(_media_ledger_intent_task, return_exceptions=True)
            _media_ledger_intent_task = None
        shutdown_error: Optional[Exception] = None
        for component, closer in (
            ("research service", research_service.aclose),
            ("Postgres pools", close_pg_pools),
            ("n8n client", n8n_client.aclose),
            ("media operation store", MEDIA_OPERATION_STORE.aclose),
        ):
            try:
                await closer()
            except Exception as exc:
                logger.error("Failed to close %s: %s", component, exc)
                if shutdown_error is None:
                    shutdown_error = exc
        if shutdown_error is not None:
            raise shutdown_error


app = FastAPI(
    title=f"{PROJECT_NAME} Backend",
    description=f"Backend API for {PROJECT_NAME}",
    version="0.1.0",
    lifespan=_lifespan,
)

validate_media_input_config()
validate_fal_config()
validate_rerank_adapter_config()
ingestion_execution_lease_seconds()
app.add_middleware(
    MediaRequestLimitMiddleware,
    max_bytes=media_request_max_bytes_from_env(),
)

from observability import configure_otel  # noqa: E402
configure_otel(app)

# Prometheus metrics — emits standard HTTP server metrics
# (http_request_duration_seconds, http_requests_total by route/method/status).
# Scraped by the observability bundle's Prometheus at backend:8000/metrics.
# Always on; the endpoint sits unscraped when PROMETHEUS_SOURCE=disabled.
from prometheus_fastapi_instrumentator import Instrumentator  # noqa: E402
# excluded_handlers keeps diagnostics out of the request
# histogram (self-referential series + healthcheck noise pollute
# rate() queries). should_group_status_codes folds 2xx/3xx/4xx/5xx
# into class buckets, bounding the status_code label cardinality.
Instrumentator(
    excluded_handlers=["/metrics", "/health", "/ready"],
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


@app.get(
    "/plugins",
    response_model=PluginInventoryResponse,
    tags=["plugins"],
    dependencies=[Depends(require_service_principal)],
)
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
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
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


@app.get("/ready")
async def readiness_check():
    """Readiness gate for required Backend dependencies."""
    dependencies = await check_backend_readiness()
    ready = all(value == "ready" for value in dependencies.values())
    payload = {
        "status": "ready" if ready else "unavailable",
        "dependencies": dependencies,
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.get("/")
async def root():
    """Root endpoint that returns a welcome message"""
    return {
        "message": f"Welcome to the {PROJECT_NAME} Backend API",
        "docs_url": "/docs",
    }


@app.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    dependencies=[Depends(require_service_principal)],
)
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
    except Exception as exc:
        raise _unexpected_error("Get job status", exc)


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



@app.get(
    "/workflows",
    response_model=List[WorkflowResponse],
    dependencies=[Depends(require_service_principal)],
)
async def list_workflows():
    """List all n8n workflows"""
    try:
        workflows = await n8n_client.list_workflows()
        return workflows
    except Exception as exc:
        raise _unexpected_error("List workflows", exc)


@app.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowResponse,
    dependencies=[Depends(require_service_principal)],
)
async def get_workflow(workflow_id: str):
    """Get a specific n8n workflow by ID"""
    try:
        workflow = await n8n_client.get_workflow(workflow_id)
        return workflow
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow with ID {workflow_id} not found",
            )
        raise _unexpected_error("Get workflow", exc)
    except Exception as exc:
        raise _unexpected_error("Get workflow", exc)



@app.post(
    "/storage/upload",
    response_model=StorageResponse,
    dependencies=[Depends(require_n8n_operator_principal)],
)
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
    except Exception as exc:
        raise _unexpected_error("Upload file", exc)


@app.post(
    "/documents/extract",
    dependencies=[Depends(require_stateless_principal)],
)
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
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(e),
        )
    except ExtractionUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except DocumentExtractionError as e:
        logger.exception("Document extraction failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document extraction failed",
        ) from e


@app.post(
    "/api/chunk",
    response_model=ChunkResponse,
    dependencies=[Depends(require_stateless_principal)],
)
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


@app.post(
    "/api/rag/evaluate",
    response_model=RagEvaluationResponse,
    dependencies=[Depends(require_stateless_principal)],
)
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


# ─── LightRAG → TEI rerank adapter (#415) ────────────────────────────
# LightRAG's Jina/Cohere rerank clients speak {query, documents} and read
# {"results": [{index, relevance_score}]}; TEI's /rerank speaks {query, texts}
# and returns a sorted array of {index, score}. This route is the translation
# seam. It is the ONLY sanctioned LightRAG→TEI path: LightRAG is never wired
# directly at TEI (the payload shapes are incompatible). The route is auth-gated
# by a generated bearer token so nothing on the backend network can drive the
# reranker anonymously; the same token is handed to LightRAG's
# RERANK_BINDING_API_KEY when the operator opts the adapter in.
_lightrag_rerank_bearer = HTTPBearer(auto_error=False)


def _require_lightrag_rerank_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_lightrag_rerank_bearer),
) -> None:
    expected = (os.getenv("LIGHTRAG_RERANK_ADAPTER_TOKEN") or "").strip()
    if not expected:
        # No token configured → the adapter has not been enabled. Fail closed
        # rather than serving reranks with no authentication.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LightRAG rerank adapter is not configured (LIGHTRAG_RERANK_ADAPTER_TOKEN unset)",
        )
    supplied = credentials.credentials if credentials is not None else ""
    scheme = (credentials.scheme if credentials is not None else "") or ""
    # _ct_equals compares utf-8 bytes so a non-ASCII bearer token yields a clean
    # 401 rather than a TypeError -> 500 (secrets.compare_digest rejects non-ASCII str).
    if scheme.lower() != "bearer" or not _ct_equals(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token for the LightRAG rerank adapter",
        )


@app.post(
    "/lightrag/rerank",
    response_model=RerankAdapterResponse,
    tags=["lightrag"],
    dependencies=[Depends(_require_lightrag_rerank_token)],
)
async def lightrag_rerank(request: RerankAdapterRequest):
    """Rerank LightRAG-retrieved documents by proxying to the TEI reranker.

    Accepts LightRAG's {query, documents} payload, forwards {query, texts} to
    TEI, and returns {"results": [{index, relevance_score}]}. Requires a valid
    ``Authorization: Bearer <LIGHTRAG_RERANK_ADAPTER_TOKEN>``.
    """
    try:
        return await asyncio.to_thread(rerank_via_tei, request)
    except RerankAdapterDependencyError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except RerankAdapterTimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(e),
        )
    except RerankAdapterUpstreamError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
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


async def _mark_rag_dispatch_failed(
    service: RagIngestionService,
    record_id: str,
    failure: tuple[str, str | None],
    cancellation_seen: asyncio.Event,
) -> None:
    message, owner = failure
    cleanup = asyncio.create_task(
        asyncio.to_thread(service.mark_dispatch_failed, record_id, message, owner)
    )
    await _join_owned_task(cleanup, cancellation_seen)


async def _mark_rag_dispatched(
    service: RagIngestionService,
    record_id: str,
    dispatch: tuple[str | None, str],
    cancellation_seen: asyncio.Event,
) -> Any:
    job_id, owner = dispatch
    update = asyncio.create_task(
        asyncio.to_thread(service.mark_dispatched, record_id, job_id, owner)
    )
    return await _join_owned_task(update, cancellation_seen)


async def _dispatch_rag_ingestion(
    record_id: str, cancellation_seen: asyncio.Event
) -> Any:
    dispatch = asyncio.create_task(
        asyncio.to_thread(
            rag_ingestion_task.apply_async,
            kwargs={"ingestion_id": record_id},
            task_id=f"rag-ingestion-{record_id}",
        )
    )
    return await _join_owned_task(dispatch, cancellation_seen)


async def _claim_rag_dispatch(
    service: RagIngestionService,
    record_id: str,
    owner: str,
    cancellation_seen: asyncio.Event,
) -> bool:
    claim = asyncio.create_task(
        asyncio.to_thread(service.claim_dispatch, record_id, owner)
    )
    claimed = await _join_owned_task(claim, cancellation_seen)
    if cancellation_seen.is_set() and claimed:
        await _mark_rag_dispatch_failed(
            service,
            record_id,
            ("RAG ingestion request cancelled before dispatch", owner),
            cancellation_seen,
        )
    _raise_if_cancelled(cancellation_seen)
    return claimed


async def _submit_rag_record(
    service: RagIngestionService,
    request: RagIngestionRequest,
    cancellation_seen: asyncio.Event,
) -> tuple[Any, bool]:
    submit_task = asyncio.create_task(
        asyncio.to_thread(
            service.submit, request.profile, corpus_path=request.corpus_path
        )
    )
    return await _join_owned_task(submit_task, cancellation_seen)


async def _reconcile_cancelled_rag_submit(
    service: RagIngestionService,
    record: Any,
    created: bool,
    cancellation_seen: asyncio.Event,
) -> None:
    if not cancellation_seen.is_set():
        return
    if created:
        await _mark_rag_dispatch_failed(
            service,
            record.id,
            ("RAG ingestion request cancelled before dispatch", None),
            cancellation_seen,
        )
    raise asyncio.CancelledError()


def _existing_rag_response(
    record: Any, created: bool, async_job: bool
) -> Any | None:
    recover_prepared = record.dispatch_state == "prepared" and (
        record.status == "pending"
        or (record.status == "running" and not async_job)
    )
    recover_dispatch = (
        record.status == "pending"
        and record.dispatch_state == "dispatching"
    )
    if created or recover_prepared or recover_dispatch:
        return None
    return _rag_idempotent_response(record)


def _rag_idempotent_response(record: Any) -> RagIngestionQueuedResponse:
    return RagIngestionQueuedResponse(
        ingestion_id=record.id,
        job_id=record.dispatch_job_id,
        status=record.status,
        message="Idempotent: existing ingestion returned (not re-run).",
    )


async def _queue_rag_ingestion(
    service: RagIngestionService,
    record: Any,
    created: bool,
    cancellation_seen: asyncio.Event,
) -> RagIngestionQueuedResponse:
    owner = uuid4().hex
    claimed = await _claim_rag_dispatch(
        service, record.id, owner, cancellation_seen
    )
    if not claimed:
        latest = await asyncio.to_thread(service.store.get, record.id)
        assert latest is not None
        return _rag_idempotent_response(latest)
    task = None
    try:
        task = await _dispatch_rag_ingestion(record.id, cancellation_seen)
        await _mark_rag_dispatched(
            service, record.id, (task.id, owner), cancellation_seen
        )
    except Exception as exc:  # noqa: BLE001 - broker unreachable
        logger.exception("Queue RAG ingestion failed")
        if task is None:
            await _mark_rag_dispatch_failed(
                service,
                record.id,
                ("RAG ingestion dispatch failed", owner),
                cancellation_seen,
            )
        _raise_if_cancelled(cancellation_seen)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to queue RAG ingestion",
        ) from exc
    _raise_if_cancelled(cancellation_seen)
    return RagIngestionQueuedResponse(
        ingestion_id=record.id,
        job_id=task.id,
        status="pending",
        message="RAG ingestion queued.",
    )


@app.post(
    "/api/rag/ingestions",
    response_model=RagIngestionQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_service_principal)],
)
async def submit_rag_ingestion(request: RagIngestionRequest, async_job: bool = True):
    """Submit a RAG ingestion job for a declared profile (#413).

    ``async_job=true`` (default) dispatches the Celery worker when the tier is
    enabled; otherwise the job runs synchronously in-request. Idempotent by
    consumer + profile revision + corpus digest: a re-submit of the same corpus
    returns the existing job without re-running it.
    """
    service = get_rag_ingestion_service()
    cancellation_seen = asyncio.Event()
    try:
        record, created = await _submit_rag_record(
            service, request, cancellation_seen
        )
    except ProfileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown rag_ingestion profile: {request.profile!r}",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    await _reconcile_cancelled_rag_submit(
        service, record, created, cancellation_seen
    )
    use_celery = async_job and celery_is_enabled()
    existing = _existing_rag_response(record, created, use_celery)
    if existing is not None:
        return existing

    if use_celery:
        return await _queue_rag_ingestion(
            service, record, created, cancellation_seen
        )

    # Synchronous fallback (Celery disabled or async_job=false).
    run_task = asyncio.create_task(service.run(record.id))
    try:
        final = await _join_owned_task(run_task, cancellation_seen)
    except IngestionExecutionBusy:
        lookup = asyncio.create_task(
            asyncio.to_thread(service.store.get, record.id)
        )
        latest = await _join_owned_task(lookup, cancellation_seen)
        assert latest is not None
        _raise_if_cancelled(cancellation_seen)
        return _rag_idempotent_response(latest)
    _raise_if_cancelled(cancellation_seen)
    return RagIngestionQueuedResponse(
        ingestion_id=final.id,
        job_id=None,
        status=final.status,
        message="RAG ingestion completed synchronously.",
    )


@app.get(
    "/api/rag/ingestions",
    response_model=List[RagIngestionRecordResponse],
    dependencies=[Depends(require_service_principal)],
)
async def list_rag_ingestions():
    """List RAG ingestion jobs (machine-readable)."""
    service = get_rag_ingestion_service()
    records = await asyncio.to_thread(service.store.list)
    return [RagIngestionRecordResponse(**r.to_dict()) for r in records]


@app.get(
    "/api/rag/ingestions/{ingestion_id}",
    response_model=RagIngestionRecordResponse,
    dependencies=[Depends(require_service_principal)],
)
async def get_rag_ingestion(ingestion_id: str):
    """Return the durable, machine-readable state of one ingestion job."""
    service = get_rag_ingestion_service()
    record = await asyncio.to_thread(service.store.get, ingestion_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown ingestion id: {ingestion_id!r}",
        )
    return RagIngestionRecordResponse(**record.to_dict())


@app.post(
    "/api/rag/ingestions/{ingestion_id}/cancel",
    response_model=RagIngestionRecordResponse,
    dependencies=[Depends(require_service_principal)],
)
async def cancel_rag_ingestion(ingestion_id: str):
    """Request cooperative cancellation of a running ingestion job."""
    service = get_rag_ingestion_service()
    cancelled = await asyncio.to_thread(
        service.store.request_cancel,
        ingestion_id,
        datetime.now(timezone.utc).isoformat(),
    )
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown ingestion id: {ingestion_id!r}",
        )
    record = await asyncio.to_thread(service.store.get, ingestion_id)
    return RagIngestionRecordResponse(**record.to_dict())


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
async def start_research(
    request: ResearchStartRequest,
    principal: BackendPrincipal = Depends(require_research_principal),
):
    """Start a new research session"""
    # Validate user_id like every other user-id-bearing route (the other
    # research routes call _validate_uuid_param too) — without this an
    # invalid user_id reaches UUID() in research_service and surfaces as an
    # opaque 500 instead of a clean 400.
    user_id = authorize_user_id(principal, request.user_id)
    try:
        result = await research_service.start_research(
            query=request.query,
            max_loops=request.max_loops or 3,
            search_api=request.search_api or "searxng",
            user_id=user_id
        )
        return ResearchResponse(**result)
    except ResearchCapacityError as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except Exception as exc:
        raise _unexpected_error("Start research", exc)


@app.get("/research/{session_id}/status", response_model=ResearchSessionResponse)
async def get_research_status(
    session_id: str,
    principal: BackendPrincipal = Depends(require_research_principal),
):
    """Get the status of a research session"""
    _validate_uuid_param(session_id, "session_id")
    try:
        result = await research_service.get_research_status(
            session_id, owner_user_id=research_owner_id(principal)
        )
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Research session {session_id} not found"
            )
        return ResearchSessionResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        raise _unexpected_error("Get research status", exc)


@app.get("/research/{session_id}/result", response_model=ResearchResultResponse)
async def get_research_result(
    session_id: str,
    principal: BackendPrincipal = Depends(require_research_principal),
):
    """Get the result of a completed research session"""
    _validate_uuid_param(session_id, "session_id")
    try:
        result = await research_service.get_research_result(
            session_id, owner_user_id=research_owner_id(principal)
        )
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Research result for session {session_id} not found"
            )
        return ResearchResultResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        raise _unexpected_error("Get research result", exc)


@app.post("/research/{session_id}/cancel", response_model=ResearchResponse)
async def cancel_research(
    session_id: str,
    principal: BackendPrincipal = Depends(require_research_principal),
):
    """Cancel a running research session"""
    _validate_uuid_param(session_id, "session_id")
    try:
        success = await research_service.cancel_research(
            session_id, owner_user_id=research_owner_id(principal)
        )
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
    except Exception as exc:
        raise _unexpected_error("Cancel research", exc)


@app.get("/research/{session_id}/logs", response_model=List[ResearchLogResponse])
async def get_research_logs(
    session_id: str,
    principal: BackendPrincipal = Depends(require_research_principal),
):
    """Get logs for a research session"""
    _validate_uuid_param(session_id, "session_id")
    try:
        logs = await research_service.get_research_logs(
            session_id, owner_user_id=research_owner_id(principal)
        )
        if logs is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Research session {session_id} not found",
            )
        return [ResearchLogResponse(**log) for log in logs]
    except HTTPException:
        raise
    except Exception as exc:
        raise _unexpected_error("Get research logs", exc)


@app.get("/research/sessions", response_model=List[ResearchSessionResponse])
async def list_research_sessions(
    user_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: BackendPrincipal = Depends(require_research_principal),
):
    """List research sessions"""
    effective_user_id = authorize_user_id(principal, user_id)
    try:
        sessions = await research_service.list_user_sessions(
            user_id=effective_user_id,
            limit=limit,
            offset=offset,
        )
        return [ResearchSessionResponse(**session) for session in sessions]
    except Exception as exc:
        raise _unexpected_error("List research sessions", exc)


@app.get(
    "/research/health",
    dependencies=[Depends(require_research_principal)],
)
async def research_health_check():
    """Health check for research service"""
    try:
        health = await research_service.health_check()
        return {
            "service": "research",
            "status": "healthy" if health["database"] == "healthy" else "degraded",
            "details": health
        }
    except Exception:
        logger.exception("Research health check failed")
        return {
            "service": "research",
            "status": "unhealthy",
            "error": "Research health check failed",
        }


# ComfyUI API Models
_COMFYUI_COMPLETION_TIMEOUT_SECONDS = int(
    os.getenv("COMFYUI_COMPLETION_TIMEOUT_SECONDS", "300")
)
if not 1 <= _COMFYUI_COMPLETION_TIMEOUT_SECONDS <= 3600:
    raise ValueError(
        "COMFYUI_COMPLETION_TIMEOUT_SECONDS must be between 1 and 3600"
    )


def _remaining_comfyui_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise asyncio.TimeoutError
    return remaining


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
    timeout_seconds: int = Field(
        default=_COMFYUI_COMPLETION_TIMEOUT_SECONDS, ge=1, le=3600
    )


class ComfyUIWorkflowRequest(BaseModel):
    """Request model for custom ComfyUI workflow"""
    workflow: Dict[str, Any] = Field(min_length=1, max_length=500)
    wait_for_completion: bool = True
    timeout_seconds: int = Field(
        default=_COMFYUI_COMPLETION_TIMEOUT_SECONDS, ge=1, le=3600
    )


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
    # Spend attribution (#342). Falls back to the X-Atlas-Consumer/-Project
    # headers, then "default". Enforced only when MEDIA_BUDGET_ENABLED=true.
    consumer: Optional[str] = Field(default=None, max_length=255)
    project: Optional[str] = Field(default=None, max_length=255)


class MediaManualReconciliationRequest(BaseModel):
    """Operator disposition for a FAL submission with no provider request id."""

    outcome: Literal["commit", "release"]
    # Matches the Supabase ledger's numeric(12,6) storage boundary and rejects
    # JSON values such as 1e999 that Pydantic would otherwise coerce to inf.
    final_cost_usd: Optional[float] = Field(
        default=None,
        ge=0,
        le=999_999.999_999,
        allow_inf_nan=False,
        strict=True,
    )

    @field_validator("final_cost_usd", mode="before")
    @classmethod
    def reject_nonfinite_cost_before_error_serialization(cls, value: Any) -> Any:
        # Python's JSON decoder turns 1e999 into ``inf``. Replacing that with a
        # safe invalid token lets FastAPI serialize its normal 422 response
        # instead of failing while attempting to echo a non-JSON float.
        if isinstance(value, float) and not math.isfinite(value):
            return "non-finite"
        return value

    @field_validator("final_cost_usd")
    @classmethod
    def require_database_precision(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and Decimal(str(value)).as_tuple().exponent < -6:
            raise ValueError("final_cost_usd supports at most 6 decimal places")
        return value

    @model_validator(mode="after")
    def reject_cost_for_release(self):
        if self.outcome == "release" and self.final_cost_usd is not None:
            raise ValueError("final_cost_usd is only valid when outcome=commit")
        return self


class MediaSpendResponse(BaseModel):
    """Scoped spend read for a single consumer (optionally one project)."""

    enabled: bool
    consumer: str
    project: Optional[str] = None
    currency: str = "USD"
    cap_usd: Optional[float] = None
    committed_usd: float = 0.0
    reserved_usd: float = 0.0
    remaining_usd: Optional[float] = None
    records: List[Dict[str, Any]] = Field(default_factory=list)


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


MEDIA_OPERATION_STORE = build_media_operation_store()

# Media spend ledger + budget engine (#342). Disabled by default; when disabled
# every engine method is a no-op and the gateway behaves exactly as before.
MEDIA_BUDGET_ENGINE = media_ledger.build_engine()

_MEDIA_STATE_PERSIST_DELAYS = (0.0, 0.05, 0.2)


async def _require_media_operation_store() -> None:
    try:
        await MEDIA_OPERATION_STORE.ensure_available()
    except Exception as exc:
        logger.warning("Media operation state store preflight failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media operation state store is unavailable",
        ) from exc


async def _transition_media_payload_or_503(
    operation_id: str,
    payload: Dict[str, Any],
    *,
    expected_status: Optional[str] = None,
) -> tuple[Optional[Dict[str, Any]], bool]:
    try:
        return await MEDIA_OPERATION_STORE.transition_payload(
            operation_id,
            payload,
            expected_status=expected_status,
        )
    except Exception as exc:
        logger.warning("Media operation transition failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media operation state store is unavailable; retry",
        ) from exc


async def _persist_media_operation(operation: Dict[str, Any]) -> None:
    last_error: Optional[Exception] = None
    for delay in _MEDIA_STATE_PERSIST_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            await MEDIA_OPERATION_STORE.create(operation)
            return
        except MediaOperationCollisionError:
            raise
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


async def _join_owned_task(
    task: asyncio.Task[Any], cancellation_seen: asyncio.Event
) -> Any:
    """Finish an accepted-work write despite repeated caller cancellation."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_seen.set()
    return task.result()


def _cancellation_marker(exc: BaseException | None = None) -> asyncio.Event:
    marker = asyncio.Event()
    if isinstance(exc, asyncio.CancelledError):
        marker.set()
    return marker


def _raise_if_cancelled(cancellation_seen: asyncio.Event) -> None:
    if cancellation_seen.is_set():
        raise asyncio.CancelledError()


async def _cancel_unpersisted_media_operation(
    *, api_key: str, model: str, operation_id: str, modality: str
) -> bool:
    try:
        async with FalClient(api_key=api_key, model=model) as client:
            return await client.cancel_media_operation(
                operation_id=operation_id,
                modality=modality,
            )
    except Exception:
        return False


def _resolve_consumer_project(
    request: MediaGenerateRequest,
    http_request: Optional[Request],
    principal: BackendPrincipal,
) -> tuple[str, str]:
    """Resolve media attribution and bind user callers to their JWT subject."""
    headers = http_request.headers if http_request is not None else {}
    claimed_consumer = (
        request.consumer
        or headers.get("X-Atlas-Consumer")
    )
    claimed_project = (
        request.project
        or headers.get("X-Atlas-Project")
    )
    return authorize_media_scope(principal, claimed_consumer, claimed_project)


def _estimate_media_cost(
    provider: str, modality: str, model: str
) -> tuple[Optional[float], Optional[Any], Optional[str]]:
    """Estimated per-run cost + pricing capture timestamp + model family.

    image_to_3d prices come from the curated registry; the text-to-image path
    has no per-model price today, so its cost is unknown (None) — never $0.
    Local ComfyUI generation (#519) is genuinely free (0.0) and still
    recorded for provenance: the budget engine only rejects *unknown* cost
    (None), so a zero estimate reserves nothing and commits nothing.
    """
    if provider == "comfyui":
        return 0.0, media_ledger._utcnow(), None
    if modality == "image_to_3d":
        entry = media_registry.lookup(model)
        if entry is not None:
            # The canonical endpoint id already encodes the version, so
            # model_version stays None rather than duplicating the family.
            return entry.estimated_cost_usd, media_ledger._utcnow(), None
    return None, None, None


def _media_artifact_refs(payload: Dict[str, Any]) -> tuple[str, ...]:
    refs: List[str] = []
    primary = payload.get("artifact_url")
    if primary:
        refs.append(str(primary))
    for artifact in payload.get("artifacts") or []:
        url = artifact.get("url") if isinstance(artifact, dict) else None
        if url and url not in refs:
            refs.append(str(url))
    return tuple(refs)


async def _maybe_reconcile_ledger(
    operation_id: str, operation: Dict[str, Any]
) -> None:
    """Reconcile the spend ledger once, when an operation reaches a terminal
    status. Succeeded commits the spend; failed/cancelled/timeout release it."""
    payload = dict(operation.get("last_payload") or {})
    op_status = str(payload.get("status", ""))
    if op_status not in TERMINAL_MEDIA_STATUSES:
        return
    provenance = dict(payload.get("provenance") or {})
    manual_outcome = provenance.get("manual_reconciliation_outcome")
    if operation.get("reconciled"):
        if manual_outcome and provenance.get("ledger_reconciliation_pending"):
            provenance["manual_reconciliation_required"] = False
            provenance["ledger_reconciliation_pending"] = False
            payload["provenance"] = provenance
            try:
                _, repaired = await MEDIA_OPERATION_STORE.replace_terminal_payload(
                    operation_id, op_status, payload
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Media operation state store is unavailable",
                ) from exc
            if not repaired:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Media operation {operation_id} changed; retry",
                )
        return
    ledger_record_exists = bool(
        operation.get("budget_tracked")
        or provenance.get("ledger_attach_completed")
    )
    ledger_record_recovered = False
    if manual_outcome and not ledger_record_exists:
        # A Postgres INSERT can commit even when its acknowledgement is lost.
        # Probe before treating this as a local-only disposition so a hidden
        # RESERVED recovery row cannot remain stranded.
        try:
            ledger_record_exists = (
                await MEDIA_BUDGET_ENGINE.store.get(operation_id)
            ) is not None
            ledger_record_recovered = ledger_record_exists
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media recovery ledger is unavailable; retry reconciliation",
            ) from exc
    if ledger_record_recovered:
        provenance["ledger_record_recovered_after_lost_ack"] = True
        provenance["ledger_record_persisted"] = True
        payload["provenance"] = provenance
    if ledger_record_exists:
        try:
            settled = await MEDIA_BUDGET_ENGINE.reconcile(
                operation_id=operation_id,
                status=op_status,
                final_cost_usd=payload.get("cost_usd"),
                artifact_refs=_media_artifact_refs(payload),
                reason=(
                    f"manual reconciliation: {manual_outcome}"
                    if manual_outcome
                    else None
                ),
                force=bool(
                    operation.get("budget_tracked")
                    or manual_outcome
                    or provenance.get("ledger_attach_completed")
                    or provenance.get("ledger_cleanup_completed")
                ),
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media spend ledger is unavailable; retry reconciliation",
            ) from exc
        if settled is None:
            retention_days = MEDIA_BUDGET_ENGINE.config.retention_days
            operation_age = time.time() - float(
                operation.get("created_at_epoch") or time.time()
            )
            if (
                retention_days
                and operation_age >= retention_days * 86400
                and not manual_outcome
                and op_status != "submission_unknown"
                and not provenance.get("manual_reconciliation_required")
            ):
                provenance["ledger_retention_pruned"] = True
                provenance["ledger_reconciliation_pending"] = False
                provenance["manual_reconciliation_required"] = False
                payload["provenance"] = provenance
                try:
                    _, finalized = (
                        await MEDIA_OPERATION_STORE.replace_terminal_payload(
                            operation_id, op_status, payload
                        )
                    )
                    marked = await MEDIA_OPERATION_STORE.mark_reconciled(operation_id)
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Media operation state store is unavailable",
                    ) from exc
                if not finalized or not marked:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Media operation {operation_id} changed; retry",
                    )
                return
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"Media recovery record {operation_id} is unavailable; "
                    "retry on the process that owns the configured store"
                ),
            )
        expected_ledger_status = (
            media_ledger.STATUS_COMMITTED
            if op_status == "succeeded"
            else media_ledger.STATUS_RELEASED
        )
        disposition_conflict = settled.status != expected_ledger_status
        cost_conflict = (
            settled.status == media_ledger.STATUS_COMMITTED
            and op_status == "succeeded"
            and payload.get("cost_usd") is not None
            and settled.effective_cost() != payload.get("cost_usd")
        )
        if disposition_conflict or cost_conflict:
            if manual_outcome and settled.status in {
                media_ledger.STATUS_COMMITTED,
                media_ledger.STATUS_RELEASED,
            }:
                winner_outcome = (
                    "commit"
                    if settled.status == media_ledger.STATUS_COMMITTED
                    else "release"
                )
                winner_payload = dict(payload)
                winner_payload["status"] = (
                    "succeeded" if winner_outcome == "commit" else "failed"
                )
                if winner_outcome == "commit":
                    winner_payload["cost_usd"] = settled.effective_cost()
                else:
                    winner_payload["cost_usd"] = None
                winner_provenance = dict(provenance)
                winner_provenance["requested_manual_reconciliation_outcome"] = (
                    manual_outcome
                )
                winner_provenance["requested_manual_reconciliation_cost_usd"] = (
                    payload.get("cost_usd")
                )
                winner_provenance["manual_reconciliation_outcome"] = winner_outcome
                winner_provenance["ledger_winner_cost_usd"] = (
                    settled.effective_cost()
                    if winner_outcome == "commit"
                    else None
                )
                winner_provenance["ledger_conflict_kind"] = (
                    "outcome_and_cost"
                    if disposition_conflict and cost_conflict
                    else "outcome"
                    if disposition_conflict
                    else "cost"
                )
                winner_provenance["ledger_winner_adopted"] = True
                winner_provenance["manual_reconciliation_required"] = False
                winner_provenance["ledger_reconciliation_pending"] = False
                winner_payload["provenance"] = winner_provenance
                try:
                    _, repaired = await MEDIA_OPERATION_STORE.adopt_ledger_reconciliation(
                        operation_id,
                        str(manual_outcome),
                        winner_payload,
                    )
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Media operation state store is unavailable",
                    ) from exc
                if not repaired:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Media operation {operation_id} changed; retry",
                    )
                try:
                    marked = await MEDIA_OPERATION_STORE.mark_reconciled(operation_id)
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Media operation state store is unavailable",
                    ) from exc
                if not marked:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Media operation reconciliation state was lost; retry",
                    )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Media operation {operation_id} had already been "
                        f"settled as {winner_outcome}; operation state was "
                        "aligned to the ledger winner"
                    ),
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Media operation {operation_id} conflicts with the "
                    "persisted ledger disposition"
                ),
            )
    elif not manual_outcome:
        return
    if provenance.get("manual_reconciliation_outcome"):
        provenance["manual_reconciliation_required"] = False
        provenance["ledger_reconciliation_pending"] = False
        payload["provenance"] = provenance
        try:
            _, finalized = await MEDIA_OPERATION_STORE.replace_terminal_payload(
                operation_id,
                op_status,
                payload,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media operation state store is unavailable",
            ) from exc
        if not finalized:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Media operation {operation_id} changed; retry",
            )
    try:
        marked = await MEDIA_OPERATION_STORE.mark_reconciled(operation_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media operation state store is unavailable",
        ) from exc
    if not marked:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media operation reconciliation state was lost; retry",
        )


async def _maybe_recover_media_ledger_intent(
    operation_id: str, operation: Dict[str, Any]
) -> Dict[str, Any]:
    """Retry durable ledger attachment or release-only cleanup before polling."""
    payload = dict(operation.get("last_payload") or {})
    provenance = dict(payload.get("provenance") or {})
    recovered_attach = False
    if provenance.get("ledger_attach_pending"):
        candidate_ids = tuple(provenance.get("ledger_attach_candidate_ids") or ())
        reservation_id = str(candidate_ids[0]) if candidate_ids else ""
        try:
            await MEDIA_BUDGET_ENGINE.protect_attach_ids((reservation_id,))
            await MEDIA_BUDGET_ENGINE.attach_operation(
                reservation_id,
                operation_id,
                consumer=str(operation.get("consumer") or ""),
                project=str(operation.get("project") or ""),
                provider=str(operation.get("provider") or ""),
                model=str(operation.get("model") or ""),
                modality=str(operation.get("modality") or ""),
                force=True,
            )
            attached = await MEDIA_BUDGET_ENGINE.store.get(operation_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "Media ledger attachment is pending; retry polling",
                    "recovery_ledger_ids": list(candidate_ids),
                },
            ) from exc
        if attached is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "Media ledger attachment is unresolved; retry polling",
                    "recovery_ledger_ids": list(candidate_ids),
                },
            )
        provenance["ledger_attach_pending"] = False
        provenance["ledger_attach_completed"] = True
        provenance["ledger_attach_protection_clear_pending"] = True
        recovered_attach = True
    elif provenance.get("ledger_cleanup_pending"):
        candidate_ids = tuple(provenance.get("ledger_cleanup_candidate_ids") or ())
        try:
            for candidate_id in candidate_ids:
                await MEDIA_BUDGET_ENGINE.release(str(candidate_id), force=True)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "Media ledger cleanup is pending; retry polling",
                    "recovery_ledger_ids": list(candidate_ids),
                },
            ) from exc
        provenance["ledger_cleanup_pending"] = False
        provenance["ledger_cleanup_completed"] = True
        if provenance.get("ledger_cleanup_only"):
            payload["status"] = "failed"
            provenance["manual_reconciliation_required"] = False
            provenance["manual_reconciliation_outcome"] = "release"
            provenance["ledger_reconciliation_pending"] = False
    elif provenance.get("ledger_attach_protection_clear_pending"):
        try:
            await MEDIA_BUDGET_ENGINE.clear_attach_protection(operation_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media ledger attachment cleanup is pending; retry polling",
            ) from exc
        try:
            persisted, changed = (
                await MEDIA_OPERATION_STORE.complete_attach_protection_clear(
                    operation_id
                )
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media operation state store is unavailable; retry",
            ) from exc
        if persisted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Media operation {operation_id} not found",
            )
        if not changed:
            return persisted
        return persisted
    else:
        return operation
    payload["provenance"] = provenance
    persisted, changed = await _transition_media_payload_or_503(
        operation_id,
        payload,
        expected_status=str((operation.get("last_payload") or {}).get("status", "")),
    )
    if persisted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media operation {operation_id} not found",
        )
    if not changed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Media operation {operation_id} changed; retry polling",
        )
    if recovered_attach:
        return await _maybe_recover_media_ledger_intent(operation_id, persisted)
    return persisted


def _media_timeout_seconds(request_timeout: Optional[int] = None) -> float:
    if request_timeout is not None:
        return float(request_timeout)
    return fal_timeout_seconds_from_env()


def _normalize_media_route(provider: str, modality: str, model: Optional[str]) -> tuple[str, str, str]:
    normalized_provider = (provider or "").strip().lower()
    normalized_modality = (modality or "").strip().lower()
    if normalized_provider == "fal" and normalized_modality == "image":
        selected_model = (model or os.getenv("FAL_MODEL") or "fal-ai/flux/dev").strip()
        return normalized_provider, normalized_modality, selected_model
    if normalized_provider == "fal" and normalized_modality == "image_to_3d":
        requested = (model or media_registry.default_model_id()).strip()
        entry = media_registry.lookup(requested)
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unknown image_to_3d model '{requested}'. Supported: "
                    + ", ".join(media_registry.known_ids())
                ),
            )
        if not entry.endpoint_verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"The image_to_3d model '{requested}' is not verified against "
                    "a live FAL endpoint and cannot be submitted"
                ),
            )
        # Resolve aliases to the canonical endpoint id.
        return normalized_provider, normalized_modality, entry.model_id
    if normalized_provider == "comfyui" and normalized_modality == "image":
        # ComfyUI needs an explicit model (checkpoint filename or catalog
        # name like krea2-turbo-bf16); there is no sensible global default
        # across SD1.5/SDXL/Krea-2 families.
        selected_model = (model or "").strip()
        if not selected_model:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="provider=comfyui with modality=image requires a model "
                "(checkpoint filename or catalog name, e.g. krea2-turbo-bf16)",
            )
        return normalized_provider, normalized_modality, selected_model
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "Unsupported media route: Atlas currently supports "
            "provider=fal with modality=image or modality=image_to_3d, "
            "and provider=comfyui with modality=image"
        ),
    )


def _media_input_uploader(data: bytes, content_type: str, key: str) -> str:
    """Host an image_to_3d input in Atlas storage and return its public URL.

    Used for providers that reject data-URI inputs (Tripo) and for inputs the
    gateway conditioned into fresh bytes. Runs synchronously off the event loop
    (called from within ``prepare_image_input`` under ``asyncio.to_thread``).
    """

    bucket = (os.getenv("BACKEND_MEDIA_INPUT_BUCKET") or "default").strip() or "default"
    bucket_ref = storage_client.from_(bucket)
    bucket_ref.upload(
        path=key,
        file=data,
        file_options={"content-type": content_type, "upsert": "true"},
    )
    base = (os.getenv("BACKEND_MEDIA_INPUT_PUBLIC_BASE_URL") or "").strip()
    if base:
        # Operators point this at a publicly reachable ingress so the provider's
        # cloud can fetch the hosted object.
        return f"{base.rstrip('/')}/{bucket}/{key}"
    return bucket_ref.get_public_url(key)


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


async def _submit_media_provider(
    *, provider: str, modality: str, model: str, prepared_input: Dict[str, Any]
) -> Dict[str, Any]:
    """Dispatch a media submit to the right provider client (#519 generalizes
    the former FAL-only call). Each client returns the normalized envelope;
    ValueError → 400, other Exception → 502 (handled by the caller)."""
    if provider == "fal":
        api_key = _require_fal_api_key()
        async with FalClient(api_key=api_key, model=model) as client:
            return await client.submit_media_operation(
                modality=modality, input=prepared_input, model=model
            )
    if provider == "comfyui":
        async with ComfyUIMediaClient(model=model) as client:
            return await client.submit_media_operation(
                modality=modality, input_payload=prepared_input, model=model
            )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported media provider for submission: {provider}",
    )


async def _poll_media_provider(
    *, provider: str, operation_id: str, modality: str, model: str
) -> Dict[str, Any]:
    """Dispatch a media poll to the right provider client."""
    if provider == "fal":
        api_key = _require_fal_api_key()
        async with FalClient(api_key=api_key, model=model) as client:
            return await client.get_media_operation(
                operation_id=operation_id, modality=modality
            )
    if provider == "comfyui":
        async with ComfyUIMediaClient(model=model) as client:
            return await client.get_media_operation(
                operation_id=operation_id, modality=modality
            )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported media provider for polling: {provider}",
    )


async def _cancel_media_provider(
    *, provider: str, operation_id: str, modality: str, model: str
) -> bool:
    """Best-effort provider-side cancel (#518 parity across providers)."""
    if provider == "fal":
        if not (_fal_source_enabled() and _fal_api_key()):
            return False
        async with FalClient(api_key=_fal_api_key(), model=model) as client:
            return await client.cancel_media_operation(
                operation_id=operation_id, modality=modality
            )
    if provider == "comfyui":
        async with ComfyUIMediaClient(model=model) as client:
            return await client.cancel_media_operation(
                operation_id=operation_id, modality=modality
            )
    return False


# ComfyUI API Endpoints
@app.get(
    "/comfyui/health",
    dependencies=[Depends(require_comfy_read_principal)],
)
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
    except Exception:
        logger.exception("ComfyUI health check failed")
        return {
            "service": "comfyui",
            "status": "unhealthy",
            "error": "ComfyUI health check failed",
        }


@app.get(
    "/comfyui/models",
    dependencies=[Depends(require_comfy_read_principal)],
)
async def get_comfyui_models():
    """Get available ComfyUI models"""
    try:
        async with ComfyUIClient() as client:
            models = await client.get_models()
            return {
                "success": True,
                "models": models
            }
    except Exception as exc:
        raise _unexpected_error("Get ComfyUI models", exc)


@app.post(
    "/media/generate",
    response_model=MediaOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_media_generation(
    request: MediaGenerateRequest,
    http_request: Request,
    principal: BackendPrincipal = Depends(require_backend_principal),
):
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
    if provider == "comfyui" and not _comfyui_media_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="COMFYUI_SOURCE (a non-disabled source) is required for provider=comfyui media generation",
        )

    # Cheap input validation first (clear 400s before any accounting work).
    if modality == "image":
        try:
            validate_image_request_shape(request.input)
        except ValueError as exc:
            detail = str(exc)
            # validate_image_request_shape is shared across providers, so strip
            # the FAL-specific prefix for any provider (e.g. comfyui) — the
            # shape rules (prompt / image_size object / int seed) are generic.
            if detail.startswith("FAL image"):
                detail = "Media image" + detail[len("FAL image"):]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=detail,
            ) from exc
    if modality == "image_to_3d" and not request.input.get("image"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Media input must include image for modality=image_to_3d",
        )

    # Complete provider-schema validation before shared state, budget, storage,
    # or paid provider work. The FAL preflight is pure and also resolves the
    # default FLUX image-to-image endpoint when an init image is present; it and
    # the FAL API key requirement apply only to provider=fal (#519: comfyui has
    # its own model resolution and needs no key).
    if provider == "fal":
        try:
            model = preflight_media_operation(
                modality=modality,
                input=request.input,
                model=model,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        api_key = _require_fal_api_key()
    else:
        api_key = None

    # Prove the shared operation store is reachable before reserving budget,
    # hosting input, or submitting paid provider work. Persistence is checked
    # again after submission and compensated if that later write still fails.
    await _require_media_operation_store()

    # Budget: reserve estimated cost BEFORE the provider call (and before any
    # side-effecting storage write). No-op when budgets are disabled.
    consumer, project = _resolve_consumer_project(request, http_request, principal)
    estimated_cost, pricing_ts, model_version = _estimate_media_cost(
        provider, modality, model
    )
    reservation_id = f"resv-{uuid4()}"
    try:
        reservation = await MEDIA_BUDGET_ENGINE.reserve(
            operation_id=reservation_id,
            consumer=consumer,
            project=project,
            provider=provider,
            model=model,
            modality=modality,
            estimated_cost_usd=estimated_cost,
            pricing_source_ts=pricing_ts,
            model_version=model_version,
        )
    except ProviderDisabled as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except (BudgetExceeded, UnknownCostRejected) as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(e)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media spend ledger is unavailable; retry submission",
        ) from exc

    # Release exits that are known to precede provider submission. Once a
    # non-idempotent provider call starts, cancellation or a lost response is
    # ambiguous and the handler below durably retains the reservation instead.
    # `reservation_settled` is flipped at each terminal disposition (released,
    # deliberately retained, or attached+persisted) so finally handles only
    # the provably safe cases.
    reservation_settled = False
    try:
        if modality == "image":
            prepared_input = request.input
        else:  # image_to_3d
            # Fail fast on a missing key before any (side-effecting) storage write.
            entry = media_registry.lookup(model)
            try:
                # prepare_image_input performs (optional) Pillow compositing and a
                # blocking storage upload; run it off the event loop.
                prepared = await asyncio.to_thread(
                    prepare_image_input,
                    request.input["image"],
                    needs_hosted_url=bool(entry and entry.needs_hosted_url),
                    accepts_data_uri=bool(entry.accepts_data_uri) if entry else True,
                    condition_transparent=True,
                    uploader=_media_input_uploader,
                )
            except ImageInputError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e),
                )
            except ImageHostingError as e:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(e),
                )
            prepared_input = dict(request.input)
            prepared_input["image"] = prepared.image

        try:
            payload = await _submit_media_provider(
                provider=provider,
                modality=modality,
                model=model,
                prepared_input=prepared_input,
            )
        except (FalSubmissionAmbiguousError, asyncio.CancelledError) as exc:
            # FAL may have accepted paid work before the response carrying its
            # request id was lost. Releasing the reservation would under-count
            # that work, so retain it under Atlas' durable local id and expose
            # an explicit manual-reconciliation record.
            reservation_settled = True
            deferred_cancellation = _cancellation_marker(exc)
            ledger_record_persisted = reservation is not None
            try:
                await _join_owned_task(
                    asyncio.create_task(MEDIA_BUDGET_ENGINE.record_ambiguous(
                        operation_id=reservation_id,
                        consumer=consumer,
                        project=project,
                        provider=provider,
                        model=model,
                        modality=modality,
                        estimated_cost_usd=estimated_cost,
                        pricing_source_ts=pricing_ts,
                        model_version=model_version,
                    )),
                    deferred_cancellation,
                )
                ledger_record_persisted = True
            except Exception as ledger_exc:
                logger.error(
                    "Ambiguous FAL submission %s could not be recorded in "
                    "the recovery ledger: %s",
                    reservation_id,
                    ledger_exc,
                )
            unknown_payload = {
                "operation_id": reservation_id,
                "status": "submission_unknown",
                "provider": provider,
                "model": model,
                "modality": modality,
                "artifact_url": None,
                "artifacts": [],
                "cost_usd": estimated_cost,
                "license": None,
                "provenance": {
                    "provider_request_id": None,
                    "manual_reconciliation_required": True,
                    "ledger_record_persisted": ledger_record_persisted,
                },
                "raw": None,
            }
            unknown_operation = {
                "operation_id": reservation_id,
                "provider": provider,
                "modality": modality,
                "model": model,
                "created_at_epoch": time.time(),
                "timeout_seconds": _media_timeout_seconds(request.timeout_seconds),
                "last_payload": unknown_payload,
                "consumer": consumer,
                "project": project,
                "owner_scope": principal_scope_key(principal),
                "budget_tracked": ledger_record_persisted,
                "reconciled": False,
            }
            local_record_persisted = True
            try:
                await _join_owned_task(
                    asyncio.create_task(
                        _persist_media_operation(unknown_operation)
                    ),
                    deferred_cancellation,
                )
            except Exception as persistence_exc:
                local_record_persisted = False
                logger.error(
                    "Ambiguous FAL submission %s could not be persisted: %s",
                    reservation_id,
                    persistence_exc,
                )
            if isinstance(exc, asyncio.CancelledError):
                raise
            if deferred_cancellation.is_set():
                raise asyncio.CancelledError()
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={
                    "message": str(exc),
                    "submission_status": "unknown",
                    "local_submission_id": reservation_id,
                    "local_record_persisted": local_record_persisted,
                    "ledger_record_persisted": ledger_record_persisted,
                    "manual_reconciliation_required": True,
                },
            ) from exc
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        except Exception as exc:
            raise _unexpected_error(
                f"Submit media generation with {provider}",
                exc,
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        operation_id = str(payload["operation_id"])
        submitted_model = str(payload.get("model") or model)
        # From this point the provider has returned an accepted operation id.
        # A cancellation at any following await must retain, never release,
        # the protected reservation for later operator reconciliation.
        reservation_settled = True
        # Re-key the reservation to the provider's operation id so poll-time
        # reconciliation can find it. The provider call already succeeded, so a
        # ledger bookkeeping hiccup here must not orphan or release paid work;
        # persist a durable retry intent and retain the reservation instead.
        budget_tracked = reservation is not None
        ledger_attach_pending = False
        post_accept_cancellation = _cancellation_marker()
        cleanup_candidate_ids: tuple[str, ...] = ()
        try:
            await MEDIA_BUDGET_ENGINE.attach_operation(
                reservation_id,
                operation_id,
                consumer=consumer,
                project=project,
                provider=provider,
                model=submitted_model,
                modality=modality,
            )
        except asyncio.CancelledError:
            post_accept_cancellation.set()
            budget_tracked = False
            cleanup_candidate_ids = (reservation_id, operation_id)
            ledger_attach_pending = reservation is not None
        except LedgerOperationCollisionError as exc:
            await _join_owned_task(
                asyncio.create_task(
                    MEDIA_BUDGET_ENGINE.release(reservation_id, force=True)
                ),
                post_accept_cancellation,
            )
            reservation_settled = True
            _raise_if_cancelled(post_accept_cancellation)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Provider returned a duplicate media operation id",
            ) from exc
        except Exception:
            budget_tracked = False
            # The re-key and status bump are separate writes, so persist both
            # possible ids and retry attachment before polling. Releasing here
            # would undercount paid work the provider already accepted.
            cleanup_candidate_ids = (reservation_id, operation_id)
            ledger_attach_pending = reservation is not None
            reservation_settled = True
            try:
                await _join_owned_task(
                    asyncio.create_task(
                        MEDIA_BUDGET_ENGINE.protect_attach_ids((reservation_id,))
                    ),
                    post_accept_cancellation,
                )
            except Exception as protection_exc:
                logger.warning(
                    "Could not retention-protect pending ledger attachment %s: %s",
                    operation_id,
                    protection_exc,
                )
        operation_payload = dict(payload)
        if ledger_attach_pending:
            cleanup_provenance = dict(operation_payload.get("provenance") or {})
            cleanup_provenance["ledger_attach_pending"] = True
            cleanup_provenance["ledger_attach_candidate_ids"] = list(
                cleanup_candidate_ids
            )
            operation_payload["provenance"] = cleanup_provenance
        elif budget_tracked:
            cleanup_provenance = dict(operation_payload.get("provenance") or {})
            cleanup_provenance["ledger_attach_protection_clear_pending"] = True
            operation_payload["provenance"] = cleanup_provenance
        if post_accept_cancellation.is_set() and reservation is None:
            try:
                await _join_owned_task(
                    asyncio.create_task(
                        MEDIA_BUDGET_ENGINE.record_ambiguous(
                            operation_id=operation_id,
                            consumer=consumer,
                            project=project,
                            provider=provider,
                            model=submitted_model,
                            modality=modality,
                            estimated_cost_usd=estimated_cost,
                            pricing_source_ts=pricing_ts,
                            model_version=model_version,
                            allow_existing=False,
                        )
                    ),
                    post_accept_cancellation,
                )
                budget_tracked = True
            except Exception as exc:
                logger.error(
                    "Could not persist cancellation recovery ledger row for %s: %s",
                    operation_id,
                    exc,
                )
        operation = {
            "operation_id": operation_id,
            "provider": provider,
            "modality": modality,
            "model": submitted_model,
            "created_at_epoch": time.time(),
            "timeout_seconds": _media_timeout_seconds(request.timeout_seconds),
            "last_payload": operation_payload,
            "consumer": consumer,
            "project": project,
            "submission_id": reservation_id,
            "owner_scope": principal_scope_key(principal),
            "budget_tracked": budget_tracked,
            "reconciled": False,
        }
        try:
            await _join_owned_task(
                asyncio.create_task(_persist_media_operation(operation)),
                post_accept_cancellation,
            )
        except MediaOperationCollisionError as exc:
            # Never cancel or retention-mark an id already owned by another
            # request. Release only a ledger row whose immutable attribution
            # proves it belongs to this submission.
            candidate_ids = (reservation_id, operation_id) if budget_tracked else (
                reservation_id,
            )
            for candidate_id in candidate_ids:
                candidate = await _join_owned_task(
                    asyncio.create_task(
                        MEDIA_BUDGET_ENGINE.store.get(candidate_id)
                    ),
                    post_accept_cancellation,
                )
                if candidate is not None and (
                    candidate.consumer,
                    candidate.project,
                    candidate.provider,
                    candidate.model,
                    candidate.modality,
                ) == (consumer, project, provider, submitted_model, modality):
                    await _join_owned_task(
                        asyncio.create_task(
                            MEDIA_BUDGET_ENGINE.release(candidate_id, force=True)
                        ),
                        post_accept_cancellation,
                    )
            reservation_settled = True
            _raise_if_cancelled(post_accept_cancellation)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Provider returned a duplicate media operation id",
            ) from exc
        except Exception as exc:
            # An attached reservation is deliberately RETAINED here for manual
            # reconciliation of the already-submitted paid work; the finally
            # must not release it.
            reservation_settled = True
            if provider == "fal":
                provider_cancellation_requested = await _join_owned_task(
                    asyncio.create_task(
                        _cancel_unpersisted_media_operation(
                            api_key=api_key,
                            model=submitted_model,
                            operation_id=operation_id,
                            modality=modality,
                        )
                    ),
                    post_accept_cancellation,
                )
            else:
                # Local providers (comfyui) are free (cost_usd=0); an unpersisted
                # operation has no paid work to reconcile, so no provider cancel.
                provider_cancellation_requested = False
            # FAL confirms only that cancellation was requested, not that paid work
            # stopped. Without durable operation state Atlas cannot poll that request
            # to a terminal outcome, so retain spend for manual reconciliation.
            manual_reconciliation_required = True
            recovery_ledger_ids: list[str] = []
            if ledger_attach_pending:
                recovery_ledger_ids = list(cleanup_candidate_ids)
            elif budget_tracked:
                recovery_ledger_ids = [operation_id]
            else:
                try:
                    await _join_owned_task(
                        asyncio.create_task(
                            MEDIA_BUDGET_ENGINE.record_ambiguous(
                                operation_id=operation_id,
                                consumer=consumer,
                                project=project,
                                provider=provider,
                                model=submitted_model,
                                modality=modality,
                                estimated_cost_usd=estimated_cost,
                                pricing_source_ts=pricing_ts,
                                model_version=model_version,
                            )
                        ),
                        post_accept_cancellation,
                    )
                    recovery_ledger_ids = [operation_id]
                except Exception as recovery_exc:
                    logger.error(
                        "Could not persist recovery ledger row for accepted "
                        "operation %s: %s",
                        operation_id,
                        recovery_exc,
                    )
            if recovery_ledger_ids:
                try:
                    await _join_owned_task(
                        asyncio.create_task(
                            MEDIA_BUDGET_ENGINE.protect_recovery_ids(
                                tuple(recovery_ledger_ids)
                            )
                        ),
                        post_accept_cancellation,
                    )
                except Exception as protection_exc:
                    logger.error(
                        "Could not retention-protect recovery ledger ids %s: %s",
                        recovery_ledger_ids,
                        protection_exc,
                    )
            logger.error(
                "Provider accepted media operation %s but state persistence failed; "
                "provider_cancellation_requested=%s "
                "manual_reconciliation_required=%s ledger_attach_pending=%s: %s",
                operation_id,
                provider_cancellation_requested,
                manual_reconciliation_required,
                ledger_attach_pending,
                exc,
            )
            _raise_if_cancelled(post_accept_cancellation)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": (
                        "Provider accepted the operation but Atlas could not "
                        "persist its state"
                    ),
                    "provider_operation_id": operation_id,
                    "recovery_ledger_ids": recovery_ledger_ids,
                    "provider_cancellation_requested": provider_cancellation_requested,
                    "manual_reconciliation_required": manual_reconciliation_required,
                },
            ) from exc
        # Attached + persisted: the reservation is now tracked as this operation.
        reservation_settled = True
        persisted_operation = await _join_owned_task(
            asyncio.create_task(MEDIA_OPERATION_STORE.get(operation_id)),
            post_accept_cancellation,
        )
        if persisted_operation is not None:
            try:
                await _join_owned_task(
                    asyncio.create_task(
                        _maybe_recover_media_ledger_intent(
                            operation_id, persisted_operation
                        )
                    ),
                    post_accept_cancellation,
                )
            except HTTPException:
                # The durable outbox retains transient clear/attach work.
                pass
        _raise_if_cancelled(post_accept_cancellation)
        return _media_response(payload)
    finally:
        if not reservation_settled and reservation is not None:
            cleanup_cancellation = _cancellation_marker(sys.exc_info()[1])
            try:
                await _join_owned_task(
                    asyncio.create_task(
                        MEDIA_BUDGET_ENGINE.release(reservation_id)
                    ),
                    cleanup_cancellation,
                )
            except Exception as cleanup_exc:
                cleanup_payload = {
                    "operation_id": reservation_id,
                    "status": "submission_unknown",
                    "provider": provider,
                    "model": model,
                    "modality": modality,
                    "artifact_url": None,
                    "artifacts": [],
                    "cost_usd": estimated_cost,
                    "license": None,
                    "provenance": {
                        "provider_request_id": None,
                        "manual_reconciliation_required": True,
                        "ledger_cleanup_only": True,
                        "ledger_cleanup_pending": True,
                        "ledger_cleanup_candidate_ids": [reservation_id],
                        "ledger_record_persisted": True,
                    },
                    "raw": None,
                }
                cleanup_operation = {
                    "operation_id": reservation_id,
                    "provider": provider,
                    "modality": modality,
                    "model": model,
                    "created_at_epoch": time.time(),
                    "timeout_seconds": _media_timeout_seconds(
                        request.timeout_seconds
                    ),
                    "last_payload": cleanup_payload,
                    "consumer": consumer,
                    "project": project,
                    "owner_scope": principal_scope_key(principal),
                    "budget_tracked": True,
                    "reconciled": False,
                }
                local_record_persisted = True
                try:
                    await _join_owned_task(
                        asyncio.create_task(
                            _persist_media_operation(cleanup_operation)
                        ),
                        cleanup_cancellation,
                    )
                except Exception as persistence_exc:
                    local_record_persisted = False
                    logger.error(
                        "Could not persist cleanup intent %s: %s",
                        reservation_id,
                        persistence_exc,
                    )
                _raise_if_cancelled(cleanup_cancellation)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "message": "Media ledger cleanup is pending",
                        "local_submission_id": reservation_id,
                        "recovery_ledger_ids": [reservation_id],
                        "local_record_persisted": local_record_persisted,
                        "manual_reconciliation_required": True,
                    },
                ) from cleanup_exc
            _raise_if_cancelled(cleanup_cancellation)


# Terminal media-operation statuses — once reached, polls return the stored
# payload without re-hitting the provider (a cancelled op must stay cancelled,
# #518), and _maybe_reconcile_ledger settles the spend exactly once.
@app.get("/media/operations/{operation_id}", response_model=MediaOperationResponse)
async def get_media_operation(
    operation_id: str,
    principal: BackendPrincipal = Depends(require_backend_principal),
):
    """Poll a hosted media generation operation."""
    try:
        operation = await MEDIA_OPERATION_STORE.get(operation_id)
    except Exception as exc:
        logger.warning("Media operation lookup failed for %s: %s", operation_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media operation state store is unavailable",
        ) from exc
    if not operation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media operation {operation_id} not found",
        )
    operation_owner = operation.get("owner_scope")
    if (
        operation_owner is None and principal.subject != "auth-disabled"
    ) or (
        operation_owner is not None
        and operation_owner != principal_scope_key(principal)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media operation {operation_id} not found",
        )
    operation = await _maybe_recover_media_ledger_intent(operation_id, operation)
    # Terminal payloads are stable: never re-poll the provider (a cancelled op
    # must not flip back to the provider's in-flight status, #518).
    last_payload = dict(operation.get("last_payload") or {})
    current_status = str(last_payload.get("status", ""))
    if current_status == "submission_unknown":
        return _media_response(last_payload)
    if current_status in TERMINAL_MEDIA_STATUSES:
        await _maybe_reconcile_ledger(operation_id, operation)
        try:
            refreshed = await MEDIA_OPERATION_STORE.get(operation_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media operation state store is unavailable; retry",
            ) from exc
        return _media_response(dict((refreshed or operation)["last_payload"]))
    elapsed = time.time() - float(operation["created_at_epoch"])
    if (
        current_status != "cancellation_requested"
        and elapsed > int(operation["timeout_seconds"])
    ):
        payload = dict(operation["last_payload"])
        payload["status"] = "timeout"
        persisted, _ = await _transition_media_payload_or_503(
            operation_id, payload, expected_status=current_status
        )
        if persisted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Media operation {operation_id} not found",
            )
        await _maybe_reconcile_ledger(operation_id, persisted)
        # Best-effort: cancel the underlying provider job so a timed-out op does
        # not orphan a real workload (a ComfyUI prompt keeps burning MPS/VRAM,
        # FAL keeps billing) after the gateway has already told the consumer the
        # op is dead (#676). The cancel machinery already exists (#518); the
        # timeout path just wasn't calling it. Failures are logged, never
        # raised, and never change the timeout outcome; the ledger reconcile
        # above is unaffected.
        provider = operation.get("provider")
        if provider in ("fal", "comfyui"):
            try:
                cancel_requested = await _cancel_media_provider(
                    provider=provider,
                    operation_id=operation_id,
                    modality=operation["modality"],
                    model=operation["model"],
                )
                logger.info(
                    "Media operation %s timed out after %ss; provider cancel "
                    "requested=%s",
                    operation_id,
                    operation["timeout_seconds"],
                    cancel_requested,
                )
            except Exception:  # noqa: BLE001 — best-effort by contract
                logger.warning(
                    "Media operation %s timed out; provider cancel failed",
                    operation_id,
                    exc_info=True,
                )
        return _media_response(dict(persisted["last_payload"]))

    provider = operation["provider"]
    if provider == "fal" and not _fal_source_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FAL_SOURCE=enabled is required to poll FAL media operations",
        )
    if provider not in ("fal", "comfyui"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported media provider for polling: {provider}",
        )

    try:
        payload = await _poll_media_provider(
            provider=provider,
            operation_id=operation_id,
            modality=operation["modality"],
            model=operation["model"],
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as exc:
        raise _unexpected_error(
            f"Poll media operation with {provider}",
            exc,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    merged_provenance = dict(last_payload.get("provenance") or {})
    merged_provenance.update(dict(payload.get("provenance") or {}))
    payload["provenance"] = merged_provenance

    if (
        current_status == "cancellation_requested"
        and str(payload.get("status", "")) not in TERMINAL_MEDIA_STATUSES
    ):
        payload["status"] = "cancellation_requested"

    persisted, _ = await _transition_media_payload_or_503(
        operation_id, payload, expected_status=current_status
    )
    if persisted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media operation {operation_id} not found",
        )
    await _maybe_reconcile_ledger(operation_id, persisted)
    return _media_response(dict(persisted["last_payload"]))


@app.post(
    "/media/operations/{operation_id}/cancel",
    response_model=MediaOperationResponse,
)
async def cancel_media_operation(
    operation_id: str,
    principal: BackendPrincipal = Depends(require_backend_principal),
):
    """Cancel an in-flight media operation (#518).

    Records nonterminal ``cancellation_requested`` and retains the budget
    reservation until provider polling confirms a terminal outcome. This is
    required because FAL may accept cancellation while in-progress work still
    completes. ComfyUI cancellations with an ambiguous provider response may be
    safely redelivered to its targeted job endpoint; confirmed requests return
    the existing operation. Other repeats and terminal operations return 409.
    """
    try:
        operation = await MEDIA_OPERATION_STORE.get(operation_id)
    except Exception as exc:
        logger.warning("Media operation lookup failed for %s: %s", operation_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media operation state store is unavailable",
        ) from exc
    if not operation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media operation {operation_id} not found",
        )
    operation_owner = operation.get("owner_scope")
    if (
        operation_owner is None and principal.subject != "auth-disabled"
    ) or (
        operation_owner is not None
        and operation_owner != principal_scope_key(principal)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media operation {operation_id} not found",
        )
    persisted = None
    for _ in range(3):
        last_payload = dict(operation.get("last_payload") or {})
        current_status = str(last_payload.get("status", ""))
        if current_status == "submission_unknown":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Media operation {operation_id} has no provider request id; "
                    "manual reconciliation is required"
                ),
            )
        if current_status in TERMINAL_MEDIA_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Media operation {operation_id} is already terminal "
                    f"({current_status})"
                ),
            )
        if current_status == "cancellation_requested":
            provenance = dict(last_payload.get("provenance") or {})
            if operation.get("provider") == "comfyui":
                if provenance.get("provider_cancellation_requested") is True:
                    return _media_response(last_payload)
                persisted = operation
                break
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Media operation {operation_id} cancellation is already requested"
                ),
            )

        payload = dict(last_payload)
        payload["status"] = "cancellation_requested"
        provenance = dict(payload.get("provenance") or {})
        provenance["provider_cancellation_requested"] = False
        payload["provenance"] = provenance
        persisted, changed = await _transition_media_payload_or_503(
            operation_id, payload, expected_status=current_status
        )
        if persisted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Media operation {operation_id} not found",
            )
        if changed:
            break
        operation = persisted
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Media operation {operation_id} changed concurrently; retry cancellation"
            ),
        )

    # Persist intent before this best-effort provider call so concurrent callers
    # cannot submit duplicate cancellation requests. Dispatched per provider so a
    # comfyui job is interrupted/dequeued too (#519 parity with #518's FAL cancel).
    provider_cancellation_requested = False
    provider = operation.get("provider")
    if provider in ("fal", "comfyui"):
        try:
            provider_cancellation_requested = await _cancel_media_provider(
                provider=provider,
                operation_id=operation_id,
                modality=operation["modality"],
                model=operation["model"],
            )
        except Exception:  # noqa: BLE001 — best-effort by contract
            provider_cancellation_requested = False

    # False is already the persisted intent default. Never write it again:
    # a concurrent targeted ComfyUI retry may have confirmed cancellation,
    # and that monotonic True result must not be downgraded by a stale writer.
    if not provider_cancellation_requested:
        return _media_response(dict(persisted["last_payload"]))

    payload = dict(persisted["last_payload"])
    provenance = dict(payload.get("provenance") or {})
    provenance["provider_cancellation_requested"] = provider_cancellation_requested
    payload["provenance"] = provenance
    enriched, _ = await _transition_media_payload_or_503(
        operation_id, payload, expected_status="cancellation_requested"
    )
    final_operation = enriched or persisted
    return _media_response(dict(final_operation["last_payload"]))


@app.post(
    "/media/operations/{operation_id}/reconcile",
    response_model=MediaOperationResponse,
    dependencies=[Depends(require_service_principal)],
)
async def reconcile_unknown_media_submission(
    operation_id: str,
    request: MediaManualReconciliationRequest,
):
    """Commit or release an ambiguous FAL submission after operator review."""

    def require_compatible_retry_cost(persisted_payload: Dict[str, Any]) -> None:
        if (
            request.outcome == "commit"
            and request.final_cost_usd is not None
            and persisted_payload.get("cost_usd") != request.final_cost_usd
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Media operation {operation_id} was already committed "
                    "with a different final cost"
                ),
            )

    def require_known_commit_cost(known_cost: Optional[float]) -> None:
        if (
            request.outcome == "commit"
            and request.final_cost_usd is None
            and known_cost is None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "final_cost_usd is required when the ambiguous submission "
                    "has no known estimated cost"
                ),
            )

    async def refetch_operation_state() -> Optional[Dict[str, Any]]:
        try:
            return await MEDIA_OPERATION_STORE.get(operation_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media operation state store is unavailable; retry",
            ) from exc

    operation_store_unavailable = False
    try:
        operation = await MEDIA_OPERATION_STORE.get(operation_id)
    except Exception as exc:
        logger.warning(
            "Operation-state lookup failed during manual reconciliation of %s; "
            "falling back to the spend ledger: %s",
            operation_id,
            exc,
        )
        operation_store_unavailable = True
        operation = None
    if not operation:
        # The provider may have accepted work while the separate operation
        # store was unavailable. The reservation ledger is the durable source
        # of truth in that explicitly reported local_record_persisted=false
        # recovery case.
        try:
            record = await MEDIA_BUDGET_ENGINE.store.get(operation_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media spend ledger is unavailable; retry reconciliation",
            ) from exc
        if record is None:
            if operation_store_unavailable:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Media operation state store is unavailable; retry",
                )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Media operation {operation_id} not found",
            )
        require_known_commit_cost(
            record.final_cost_usd
            if record.final_cost_usd is not None
            else record.estimated_cost_usd
        )
        wanted_status = (
            media_ledger.STATUS_COMMITTED
            if request.outcome == "commit"
            else media_ledger.STATUS_RELEASED
        )
        if record.status in {
            media_ledger.STATUS_COMMITTED,
            media_ledger.STATUS_RELEASED,
        }:
            pass
        elif record.status not in {
            media_ledger.STATUS_RESERVED,
            media_ledger.STATUS_SUBMITTED,
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Media operation {operation_id} cannot be reconciled",
            )
        else:
            try:
                record = await MEDIA_BUDGET_ENGINE.reconcile(
                    operation_id=operation_id,
                    status=(
                        "succeeded" if request.outcome == "commit" else "failed"
                    ),
                    final_cost_usd=request.final_cost_usd,
                    reason=f"manual reconciliation: {request.outcome}",
                    force=True,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Media spend ledger is unavailable; retry reconciliation",
                ) from exc
            if record is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Media operation {operation_id} not found",
                )
        if operation_store_unavailable and record.status in {
            media_ledger.STATUS_COMMITTED,
            media_ledger.STATUS_RELEASED,
        }:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Ledger disposition is durable, but operation-state sync "
                    "is pending; retry reconciliation"
                ),
            )
        if record.status != wanted_status:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Media operation {operation_id} was already reconciled "
                    "with a different outcome"
                ),
            )
        if (
            request.outcome == "commit"
            and request.final_cost_usd is not None
            and record.effective_cost() != request.final_cost_usd
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Media operation {operation_id} was already committed "
                    "with a different final cost"
                ),
            )
        return _media_response(
            {
                "operation_id": operation_id,
                "status": "succeeded" if request.outcome == "commit" else "failed",
                "provider": record.provider,
                "model": record.model,
                "modality": record.modality,
                "artifact_url": None,
                "artifacts": [],
                "cost_usd": record.effective_cost(),
                "license": None,
                "provenance": {
                    "provider_request_id": None,
                    "local_record_persisted": False,
                    "manual_reconciliation_required": False,
                    "manual_reconciliation_outcome": request.outcome,
                },
                "raw": None,
            }
        )

    last_payload = dict(operation.get("last_payload") or {})
    if (
        (last_payload.get("provenance") or {}).get("ledger_cleanup_only")
        and request.outcome != "release"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cleanup-only recovery operations can only be released",
        )
    require_known_commit_cost(last_payload.get("cost_usd"))
    current_status = str(last_payload.get("status", ""))
    provenance = dict(last_payload.get("provenance") or {})
    prior_outcome = provenance.get("manual_reconciliation_outcome")
    expected_manual_status = "submission_unknown"
    if current_status != "submission_unknown":
        expected_terminal = "succeeded" if request.outcome == "commit" else "failed"
        if prior_outcome == request.outcome and current_status == expected_terminal:
            require_compatible_retry_cost(last_payload)
            await _maybe_reconcile_ledger(operation_id, operation)
            refreshed = await refetch_operation_state()
            return _media_response(dict((refreshed or operation)["last_payload"]))
        try:
            recovery_record = await MEDIA_BUDGET_ENGINE.store.get(operation_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media spend ledger is unavailable; retry reconciliation",
            ) from exc
        ledger_outcome = (
            "commit"
            if recovery_record is not None
            and recovery_record.status == media_ledger.STATUS_COMMITTED
            else "release"
            if recovery_record is not None
            and recovery_record.status == media_ledger.STATUS_RELEASED
            else None
        )
        if not (
            recovery_record is not None
            and ledger_outcome == request.outcome
            and str(recovery_record.reason or "").startswith(
                "manual reconciliation:"
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Media operation {operation_id} does not require manual reconciliation"
                ),
            )
        expected_manual_status = current_status

    payload = dict(last_payload)
    payload["status"] = "succeeded" if request.outcome == "commit" else "failed"
    if request.outcome == "commit" and request.final_cost_usd is not None:
        payload["cost_usd"] = request.final_cost_usd
    provenance = dict(payload.get("provenance") or {})
    provenance["manual_reconciliation_required"] = True
    provenance["manual_reconciliation_outcome"] = request.outcome
    provenance["ledger_reconciliation_pending"] = True
    if provenance.get("ledger_cleanup_only") and request.outcome == "release":
        provenance["ledger_cleanup_pending"] = False
        provenance["ledger_cleanup_completed"] = True
    payload["provenance"] = provenance

    try:
        if expected_manual_status in TERMINAL_MEDIA_STATUSES:
            persisted, changed = await MEDIA_OPERATION_STORE.adopt_ledger_fallback(
                operation_id,
                int(operation.get("state_version", 0)),
                payload,
            )
        else:
            persisted, changed = await MEDIA_OPERATION_STORE.transition_payload(
                operation_id,
                payload,
                expected_status=expected_manual_status,
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media operation state store is unavailable; retry",
        ) from exc
    if persisted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media operation {operation_id} not found",
        )
    if not changed:
        persisted_payload = dict(persisted.get("last_payload") or {})
        persisted_provenance = dict(persisted_payload.get("provenance") or {})
        expected_terminal = "succeeded" if request.outcome == "commit" else "failed"
        if (
            persisted_payload.get("status") == expected_terminal
            and persisted_provenance.get("manual_reconciliation_outcome")
            == request.outcome
        ):
            require_compatible_retry_cost(persisted_payload)
            await _maybe_reconcile_ledger(operation_id, persisted)
            refreshed = await refetch_operation_state()
            return _media_response(dict((refreshed or persisted)["last_payload"]))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Media operation {operation_id} was already reconciled",
        )
    await _maybe_reconcile_ledger(operation_id, persisted)
    refreshed = await refetch_operation_state()
    return _media_response(dict((refreshed or persisted)["last_payload"]))


@app.get("/media/spend", response_model=MediaSpendResponse)
async def get_media_spend(
    consumer: Optional[str] = Query(default=None, min_length=1, max_length=255),
    project: Optional[str] = Query(default=None, max_length=255),
    principal: BackendPrincipal = Depends(require_backend_principal),
):
    """Scoped spend read for a single consumer (optionally one project).

    Returns that consumer's ledger rows + committed/reserved totals only — never
    provider keys or another consumer's records. Requires an explicit consumer.
    """
    consumer, resolved_project = authorize_media_scope(principal, consumer, project)
    if not MEDIA_BUDGET_ENGINE.enabled:
        return MediaSpendResponse(
            enabled=False,
            consumer=consumer,
            project=resolved_project if project is not None else None,
        )
    try:
        summary = await MEDIA_BUDGET_ENGINE.spend(
            consumer=consumer,
            project=resolved_project if project is not None else None,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media spend ledger is unavailable; retry",
        ) from exc
    return MediaSpendResponse(enabled=True, **summary)


@app.post(
    "/comfyui/generate",
    response_model=ComfyUIResponse,
    dependencies=[Depends(require_comfy_automation_principal)],
)
async def generate_image(request: ComfyUIGenerateRequest):
    """Generate an image using the configured media provider."""
    deadline = time.monotonic() + request.timeout_seconds
    if _fal_source_enabled():
        api_key = _require_fal_api_key()
        compatibility_model = (
            os.getenv("FAL_MODEL") or "fal-ai/flux/dev"
        ).strip()
        if compatibility_model != "fal-ai/flux/dev":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "FAL /comfyui/generate compatibility supports only "
                    "fal-ai/flux/dev; use /media/generate with "
                    "input.provider_arguments for custom endpoints"
                ),
            )
        if not request.wait_for_completion:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="FAL does not support queue-only compatibility requests",
            )
        try:
            async with FalClient(
                api_key=api_key,
                timeout_seconds=_remaining_comfyui_timeout(deadline),
            ) as client:
                result = await asyncio.wait_for(
                    client.generate_simple_image(
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        width=request.width,
                        height=request.height,
                        steps=request.steps,
                        cfg=request.cfg,
                        seed=request.seed,
                        checkpoint=request.checkpoint,
                    ),
                    timeout=_remaining_comfyui_timeout(deadline),
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
        except asyncio.TimeoutError:
            return ComfyUIResponse(
                success=False,
                error="Image generation timed out",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            raise _unexpected_error("Generate image with FAL", exc)

    try:
        async with ComfyUIClient() as client:
            # Generate the image
            result = await asyncio.wait_for(
                client.generate_simple_image(
                    prompt=request.prompt,
                    negative_prompt=request.negative_prompt,
                    width=request.width,
                    height=request.height,
                    steps=request.steps,
                    cfg=request.cfg,
                    seed=request.seed,
                    checkpoint=request.checkpoint
                ),
                timeout=_remaining_comfyui_timeout(deadline),
            )
            
            if not result.get("success"):
                return ComfyUIResponse(
                    success=False,
                    error="ComfyUI generation failed"
                )
            
            prompt_id = result["prompt_id"]
            
            # If wait_for_completion is True, wait for the image to be generated
            if request.wait_for_completion:
                completion_result = await client.wait_for_completion(
                    prompt_id, timeout=_remaining_comfyui_timeout(deadline)
                )
                
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
    except asyncio.TimeoutError:
        return ComfyUIResponse(
            success=False,
            error="Image generation timed out",
        )
    except Exception as exc:
        raise _unexpected_error("Generate image", exc)


@app.post(
    "/comfyui/workflow",
    response_model=ComfyUIResponse,
    dependencies=[Depends(require_comfy_automation_principal)],
)
async def execute_comfyui_workflow(request: ComfyUIWorkflowRequest):
    """Execute a custom ComfyUI workflow"""
    deadline = time.monotonic() + request.timeout_seconds
    try:
        async with ComfyUIClient() as client:
            # Queue the workflow
            result = await asyncio.wait_for(
                client.queue_prompt(request.workflow),
                timeout=_remaining_comfyui_timeout(deadline),
            )
            
            if not result.get("success"):
                return ComfyUIResponse(
                    success=False,
                    error=result.get("error", "Unknown error")
                )
            
            prompt_id = result["prompt_id"]
            
            # If wait_for_completion is True, wait for the workflow to complete
            if request.wait_for_completion:
                completion_result = await client.wait_for_completion(
                    prompt_id, timeout=_remaining_comfyui_timeout(deadline)
                )
                
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
                
    except asyncio.TimeoutError:
        return ComfyUIResponse(
            success=False,
            error="Workflow execution timed out",
        )
    except Exception as exc:
        raise _unexpected_error("Execute ComfyUI workflow", exc)


@app.get(
    "/comfyui/history/{prompt_id}",
    dependencies=[Depends(require_comfy_automation_principal)],
)
async def get_generation_history(prompt_id: str):
    """Get ComfyUI generation history for a specific prompt"""
    try:
        async with ComfyUIClient() as client:
            history = await client.get_history(prompt_id)
            return {
                "success": True,
                "history": history
            }
    except Exception as exc:
        raise _unexpected_error("Get ComfyUI history", exc)


@app.get(
    "/comfyui/queue",
    dependencies=[Depends(require_comfy_automation_principal)],
)
async def get_queue_status():
    """Get ComfyUI queue status"""
    try:
        async with ComfyUIClient() as client:
            queue = await client.get_queue_status()
            return {
                "success": True,
                "queue": queue
            }
    except Exception as exc:
        raise _unexpected_error("Get ComfyUI queue status", exc)


@app.post(
    "/comfyui/cancel/{prompt_id}",
    dependencies=[Depends(require_comfy_automation_principal)],
)
async def cancel_generation(prompt_id: str):
    """Cancel a ComfyUI generation"""
    try:
        async with ComfyUIClient() as client:
            success = await client.cancel_prompt(prompt_id)
            return {
                "success": success,
                "message": "Generation cancelled" if success else "Failed to cancel generation"
            }
    except Exception as exc:
        raise _unexpected_error("Cancel ComfyUI generation", exc)


def _inline_content_disposition(filename: str) -> str:
    """Build a latin-1-safe ``Content-Disposition: inline`` header value.

    Strips CR/LF and quotes so the name can't break the header, exposes an ASCII
    fallback in the plain ``filename=`` parameter, and carries the full UTF-8 name
    via the RFC 5987 ``filename*=`` form. Without the ASCII fallback a name with
    characters above U+00FF (e.g. ``"日本語.png"``) raises ``UnicodeEncodeError``
    when Starlette latin-1-encodes the header — surfacing as a 500 instead of
    returning the image.
    """
    from urllib.parse import quote

    safe_name = filename.replace("\r", "").replace("\n", "").replace('"', "")
    ascii_fallback = safe_name.encode("ascii", "replace").decode("ascii")
    disposition = f'inline; filename="{ascii_fallback}"'
    if ascii_fallback != safe_name:
        disposition += f"; filename*=UTF-8''{quote(safe_name, safe='')}"
    return disposition


_COMFY_VIEW_FOLDER_TYPES = frozenset({"output", "input", "temp"})


def _validate_comfy_view_params(
    subfolder: str, folder_type: str, filename: str = ""
) -> None:
    """#801: `filename` + `subfolder` + `folder_type` are forwarded straight to
    ComfyUI's `/view` as query params. Reject an out-of-set folder_type and a
    path-traversal subfolder or filename with HTTP 400 so a caller cannot point
    the internal ComfyUI at arbitrary folders. Callers MUST invoke this before
    any try/except that would otherwise convert the 400 into a 500.

    `filename` was originally left unchecked while its two siblings in the same
    forwarded query string were validated. Starlette's path convertor already
    excludes `/`, so this was not a working traversal — but the asymmetry is
    the bug: `..`, backslashes and NUL still reached ComfyUI, and the guard
    would silently stop holding if that route ever became a query param.
    """
    if folder_type not in _COMFY_VIEW_FOLDER_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="folder_type must be one of: output, input, temp",
        )
    if subfolder and _is_traversal(subfolder):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="subfolder must be a relative path without '..' or a leading '/'",
        )
    if filename and (_is_traversal(filename) or "/" in filename or "\\" in filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="filename must be a bare name without a path separator or '..'",
        )


def _is_traversal(value: str) -> bool:
    """Shared rule, so the checked params cannot drift apart again."""
    return ".." in value or value.startswith("/") or "\x00" in value or "\\" in value


@app.get(
    "/comfyui/image/{filename}",
    dependencies=[Depends(require_comfy_automation_principal)],
)
async def get_generated_image(filename: str, subfolder: str = "", folder_type: str = "output"):
    """Get a generated image from ComfyUI"""
    # Validate BEFORE the try/except below (the catch-all would otherwise turn a
    # 400 into a 500).
    _validate_comfy_view_params(subfolder, folder_type, filename)
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
            return Response(
                content=image_data,
                media_type=content_type,
                headers={"Content-Disposition": _inline_content_disposition(filename)}
            )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Image {filename} not found",
            )
        raise _unexpected_error("Get ComfyUI image", exc)
    except Exception as exc:
        raise _unexpected_error("Get ComfyUI image", exc)


# ComfyUI Model Management Endpoints
@app.get(
    "/comfyui/db/models",
    dependencies=[Depends(require_comfy_automation_principal)],
)
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

    except Exception as exc:
        raise _unexpected_error("Read ComfyUI manifest", exc)


# =============================================================================
# LangMem Memory API Endpoints
# =============================================================================

@app.post("/memory/extract", response_model=MemoryExtractResponse)
async def memory_extract(
    request: MemoryExtractRequest,
    principal: BackendPrincipal = Depends(require_memory_principal),
):
    """Extract and store memory facts from conversation messages."""
    user_id = authorize_user_id(principal, request.user_id)
    try:
        result = await memory_service.extract_facts(
            user_id=user_id,
            messages=[message.model_dump() for message in request.messages],
            namespace=request.namespace,
            conversation_id=request.conversation_id,
        )
        return MemoryExtractResponse(**result)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception as exc:
        raise _unexpected_error("Extract memories", exc)


@app.post("/memory/recall", response_model=MemoryRecallResponse)
async def memory_recall(
    request: MemoryRecallRequest,
    principal: BackendPrincipal = Depends(require_memory_principal),
):
    """Recall relevant memories for a query using semantic search."""
    user_id = authorize_user_id(principal, request.user_id)
    try:
        result = await memory_service.recall(
            user_id=user_id,
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
    except Exception as exc:
        raise _unexpected_error("Recall memories", exc)


@app.post(
    "/memory/consolidate",
    response_model=Union[MemoryConsolidateResponse, AsyncJobQueuedResponse],
)
async def memory_consolidate(
    request: MemoryConsolidateRequest,
    async_job: bool = False,
    principal: BackendPrincipal = Depends(require_memory_automation_principal),
):
    """Consolidate and deduplicate user memories."""
    user_id = authorize_user_id(principal, request.user_id)
    if async_job:
        if not celery_is_enabled():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Celery worker tier is disabled",
            )
        if request.idempotency_key:
            scope = user_id or "all-users"
            token = hashlib.sha256(
                f"{scope}\0{request.idempotency_key}".encode("utf-8")
            ).hexdigest()
        else:
            token = uuid4().hex
        task_id = f"memory-consolidate-{token}"
        cancellation_seen = asyncio.Event()
        try:
            dispatch = asyncio.create_task(
                asyncio.to_thread(
                    memory_consolidate_task.apply_async,
                    kwargs={"user_id": user_id, "idempotency_key": task_id},
                    task_id=task_id,
                )
            )
            task = await _join_owned_task(dispatch, cancellation_seen)
        except Exception as exc:
            logger.exception("Queue memory consolidation failed")
            _raise_if_cancelled(cancellation_seen)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to queue memory consolidation",
            ) from exc
        _raise_if_cancelled(cancellation_seen)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=AsyncJobQueuedResponse(
                job_id=task.id,
                status="pending",
                message="Memory consolidation queued",
                task="memory_consolidate",
                request={"user_id": user_id},
            ).model_dump(),
        )

    try:
        result = await memory_service.consolidate(user_id=user_id)
        return MemoryConsolidateResponse(**result)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception as exc:
        raise _unexpected_error("Consolidate memories", exc)


@app.post("/memory/summarize", response_model=MemorySummarizeResponse)
async def memory_summarize(
    request: MemorySummarizeRequest,
    principal: BackendPrincipal = Depends(require_memory_principal),
):
    """Generate a natural-language summary of a user's memory profile."""
    user_id = authorize_user_id(principal, request.user_id)
    try:
        result = await memory_service.summarize(
            user_id=user_id,
            namespace=request.namespace,
        )
        return MemorySummarizeResponse(**result)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    except Exception as exc:
        raise _unexpected_error("Summarize memories", exc)


@app.get("/memory/user/{user_id}", response_model=MemoryListResponse)
async def memory_list(
    user_id: str,
    namespace: Optional[str] = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: BackendPrincipal = Depends(require_memory_principal),
):
    """List all active memories for a user."""
    user_id = authorize_user_id(principal, user_id)
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
    except Exception as exc:
        raise _unexpected_error("List memories", exc)


@app.put("/memory/{memory_id}", response_model=Dict[str, Any])
async def memory_update(
    memory_id: str,
    request: MemoryUpdateRequest,
    user_id: str = Query(...),
    principal: BackendPrincipal = Depends(require_memory_principal),
):
    """Update a specific memory fact."""
    _validate_uuid_param(memory_id, "memory_id")
    user_id = authorize_user_id(principal, user_id)
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
    except Exception as exc:
        raise _unexpected_error("Update memory", exc)


@app.delete("/memory/{memory_id}", response_model=Dict[str, Any])
async def memory_delete(
    memory_id: str,
    user_id: str = Query(...),
    principal: BackendPrincipal = Depends(require_memory_principal),
):
    """Delete (deactivate) a specific memory fact."""
    _validate_uuid_param(memory_id, "memory_id")
    user_id = authorize_user_id(principal, user_id)
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
    except Exception as exc:
        raise _unexpected_error("Delete memory", exc)


@app.get(
    "/memory/health",
    response_model=MemoryHealthResponse,
    dependencies=[Depends(require_memory_automation_principal)],
)
async def memory_health_check():
    """Health check for the LangMem memory service."""
    result = await memory_service.health_check()
    return MemoryHealthResponse(**result)


@app.get(
    "/memory/graphiti/status",
    response_model=Dict[str, Any],
    dependencies=[Depends(require_backend_principal)],
)
async def graphiti_experiment_status():
    """Report the disabled-by-default backend-only Graphiti experiment plan."""
    return GraphitiExperimentConfig.from_env().status_payload()
