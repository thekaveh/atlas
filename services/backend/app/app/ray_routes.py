"""FastAPI router for /api/ray/* — wraps the RayClient.

When Ray is disabled (RAY_ADDRESS empty), every endpoint returns 503
with a clear error message rather than 500.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from backend_identity import _ct_equals
from ray_client import (
    RayClient,
    RayDisabledError,
    RayJobAlreadyExistsError,
    RayJobSubmission,
)

logger = logging.getLogger(__name__)

_ray_bearer = HTTPBearer(auto_error=False)


async def _require_ray_job_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_ray_bearer),
) -> None:
    """Fail closed unless the caller presents the generated Ray API token."""
    expected = os.getenv("RAY_JOB_API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Ray job API authentication is not configured",
        )
    supplied = credentials.credentials if credentials else ""
    # _ct_equals compares utf-8 bytes → a non-ASCII token yields a clean 401,
    # not a TypeError -> 500 (hmac/secrets.compare_digest reject non-ASCII str).
    if not supplied or not _ct_equals(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid Ray job API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(
    prefix="/api/ray",
    tags=["ray"],
    dependencies=[Depends(_require_ray_job_token)],
)


class SubmitJobRequest(BaseModel):
    """Request model for submitting a Ray job."""
    entrypoint: str = Field(..., description="Shell command to run on the Ray cluster.")
    runtime_env: Optional[dict[str, Any]] = Field(
        default=None,
        description="Ray runtime_env dict (working_dir, pip, env_vars).",
    )
    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Arbitrary metadata attached to the job.",
    )
    submission_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^raysubmit_[A-Za-z0-9_]+$",
        description="Stable Ray-compatible identifier for safe retry reconciliation.",
    )


class SubmitJobResponse(BaseModel):
    """Response model for job submission."""
    job_id: str


@router.post("/jobs/submit", response_model=SubmitJobResponse)
async def submit_job(payload: SubmitJobRequest) -> SubmitJobResponse:
    """Submit a job to the Ray cluster."""
    try:
        client = RayClient.get()
        submission_id = payload.submission_id
        cancellation_seen = asyncio.Event()
        # RayClient methods wrap the sync ray.job_submission SDK; run them in a
        # worker thread so this handler doesn't block the FastAPI event loop.
        submission = asyncio.create_task(
            asyncio.to_thread(
                client.submit_job,
                RayJobSubmission(
                    entrypoint=payload.entrypoint,
                    submission_id=submission_id,
                    runtime_env=payload.runtime_env,
                    metadata=payload.metadata,
                ),
            )
        )
        try:
            job_id = await _join_owned_task(submission, cancellation_seen)
        except Exception:
            if cancellation_seen.is_set():
                raise asyncio.CancelledError()
            raise
        if cancellation_seen.is_set():
            cleanup = asyncio.create_task(
                asyncio.to_thread(client.stop_job, submission_id)
            )
            try:
                await _join_owned_task(cleanup, cancellation_seen)
            except Exception:
                logger.exception("ray cancellation cleanup failed for %s", submission_id)
            raise asyncio.CancelledError()
        return SubmitJobResponse(job_id=job_id)
    except RayDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RayJobAlreadyExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ray job {exc.submission_id!r} already exists; reconcile it "
                "through the job status or stop endpoint"
            ),
        ) from exc
    except Exception:
        logger.exception("ray submit_job failed")
        raise HTTPException(status_code=500, detail="Ray job submission failed")


async def _join_owned_task(
    task: asyncio.Task[Any], cancellation_seen: asyncio.Event
) -> Any:
    """Join a thread-backed side effect despite repeated request cancellation."""

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_seen.set()
    return task.result()


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> dict:
    """Get the status of a Ray job."""
    try:
        return await asyncio.to_thread(RayClient.get().get_job_status, job_id)
    except RayDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("ray get_job_status failed")
        raise HTTPException(status_code=500, detail="Ray job status fetch failed")


@router.delete("/jobs/{job_id}")
async def stop_job(job_id: str) -> dict:
    """Stop a running Ray job."""
    try:
        stopped = await asyncio.to_thread(RayClient.get().stop_job, job_id)
        return {"stopped": stopped}
    except RayDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("ray stop_job failed")
        raise HTTPException(status_code=500, detail="Ray job stop failed")


@router.get("/cluster/status")
async def cluster_status() -> dict:
    """Get Ray cluster status from the dashboard API."""
    try:
        return await asyncio.to_thread(RayClient.get().cluster_status)
    except RayDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("ray cluster_status failed")
        raise HTTPException(status_code=500, detail="Ray cluster status fetch failed")
