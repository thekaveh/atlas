"""Lazy Ray-cluster client for the Backend.

The Backend reaches the Ray cluster via Ray's REST job-submission API
(NOT via ray.init() — that would pin the FastAPI worker process to the
Ray cluster lifecycle, which causes issues on reloads). Job submission
+ status polling go through a REST client that's cheap to create.

When Ray is disabled (RAY_ADDRESS is empty), every method raises
RayDisabledError. The router translates that into a 503 with a clear
message rather than a 500.
"""

import os
from typing import Optional


class RayDisabledError(Exception):
    """Raised by RayClient methods when Ray is not configured."""


def _ray_address() -> Optional[str]:
    """Return the cluster dashboard URL the JobSubmissionClient needs.

    RAY_ADDRESS is the env var the manifest sets — it's the `ray://` URL
    for direct client connection. For the JobSubmissionClient we need
    the HTTP dashboard URL — derive it from the cluster head hostname.
    """
    ray_addr = os.environ.get("RAY_ADDRESS", "").strip()
    if not ray_addr:
        return None
    # ray://ray-head:10001 → http://ray-head:8265
    # ray://anyscale.example.com:10001 → https://anyscale.example.com:8265
    # (external HTTPS-by-default for Anyscale; configurable via RAY_DASHBOARD_URL override)
    explicit_dashboard = os.environ.get("RAY_DASHBOARD_URL", "").strip()
    if explicit_dashboard:
        return explicit_dashboard
    if ray_addr.startswith("ray://"):
        host = ray_addr[len("ray://"):].rsplit(":", 1)[0]
        scheme = "https" if "." in host else "http"  # heuristic: bare hostname = LAN
        return f"{scheme}://{host}:8265"
    return None


class RayClient:
    _instance: Optional["RayClient"] = None

    @classmethod
    def get(cls) -> "RayClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._addr = _ray_address()
        self._client = None

    def _ensure_client(self):
        if self._addr is None:
            raise RayDisabledError("RAY_ADDRESS not set — Ray cluster is disabled")
        if self._client is None:
            from ray.job_submission import JobSubmissionClient
            self._client = JobSubmissionClient(self._addr)
        return self._client

    def submit_job(self, entrypoint: str, runtime_env: dict | None = None, metadata: dict | None = None) -> str:
        return self._ensure_client().submit_job(
            entrypoint=entrypoint, runtime_env=runtime_env or {}, metadata=metadata or {}
        )

    def get_job_status(self, job_id: str) -> dict:
        client = self._ensure_client()
        return {
            "job_id": job_id,
            "status": client.get_job_status(job_id).value,
            "info": client.get_job_info(job_id).__dict__,
        }

    def get_job_logs(self, job_id: str) -> str:
        return self._ensure_client().get_job_logs(job_id)

    def stop_job(self, job_id: str) -> bool:
        return self._ensure_client().stop_job(job_id)

    def cluster_status(self) -> dict:
        # Hits the Ray dashboard's /api/cluster_status directly over the internal
        # backend-network (container-to-container); the Ray dashboard has no auth.
        # The backend's own Kong route (backend-api) is behind key-auth + ACL ONLY
        # when the operator sets BACKEND_KONG_AUTH=key-auth (default: disabled) —
        # see kong_config_generator.generate_backend_service. Operators exposing
        # the stack should set it to gate the Ray job-submission surface
        # (/api/ray/jobs/submit runs an arbitrary shell entrypoint on the cluster).
        import urllib.request, json
        self._ensure_client()
        with urllib.request.urlopen(f"{self._addr}/api/cluster_status", timeout=5) as resp:
            return json.loads(resp.read())
