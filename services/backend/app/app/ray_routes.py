"""FastAPI router for /api/ray/* — wraps the RayClient.

When Ray is disabled (RAY_ADDRESS empty), every endpoint returns 503
with a clear error message rather than 500.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from ray_client import RayClient, RayDisabledError

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
    if not supplied or not hmac.compare_digest(supplied, expected):
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


class SubmitJobResponse(BaseModel):
    """Response model for job submission."""
    job_id: str


@router.post("/jobs/submit", response_model=SubmitJobResponse)
async def submit_job(payload: SubmitJobRequest) -> SubmitJobResponse:
    """Submit a job to the Ray cluster."""
    try:
        client = RayClient.get()
        # RayClient methods wrap the sync ray.job_submission SDK; run them in a
        # worker thread so this handler doesn't block the FastAPI event loop.
        job_id = await asyncio.to_thread(
            client.submit_job,
            payload.entrypoint,
            payload.runtime_env,
            payload.metadata,
        )
        return SubmitJobResponse(job_id=job_id)
    except RayDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("ray submit_job failed")
        raise HTTPException(status_code=500, detail="Ray job submission failed")


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
