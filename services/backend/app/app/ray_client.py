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
from dataclasses import dataclass
from typing import Optional


class RayDisabledError(Exception):
    """Raised by RayClient methods when Ray is not configured."""


#: Transport deadlines (#1170). The pinned Ray 2.56.0 SDK forwards extra
#: keyword arguments from ``SubmissionClient._do_request`` straight into
#: ``requests.request()`` and sets NO default timeout, so an unresponsive
#: control plane retained the worker thread indefinitely. Connect/read
#: bounds sized for an in-network dashboard; constants, not env knobs.
#: (requests semantics: the read timeout bounds gaps between chunks, which
#: is the accepted transport bound for a trusted-network control plane.)
_RAY_CONNECT_TIMEOUT_SECONDS = 5.0
_RAY_READ_TIMEOUT_SECONDS = 30.0


class RayControlPlaneTimeoutError(RuntimeError):
    """A bounded control-plane request hit its transport deadline.

    The job (if any) is addressed by a caller-owned submission id, so the
    caller reconciles through status/stop with the SAME id — never by blind
    retry of a side effect.
    """

    def __init__(self, operation: str, job_id: Optional[str] = None):
        self.operation = operation
        self.job_id = job_id
        suffix = f" for {job_id!r}" if job_id else ""
        super().__init__(
            f"Ray control plane did not answer {operation}{suffix} within "
            f"{_RAY_CONNECT_TIMEOUT_SECONDS:g}s connect / "
            f"{_RAY_READ_TIMEOUT_SECONDS:g}s read"
        )


class RaySubmissionAmbiguousError(RayControlPlaneTimeoutError):
    """Submission transport timed out AFTER the request was sent.

    A timeout does not prove the server rejected the job: acceptance is
    ambiguous, and the stable caller-supplied submission_id is the
    reconciliation handle (poll status or stop with the same id).
    """

    def __init__(self, submission_id: str):
        super().__init__("job submission", submission_id)
        self.submission_id = submission_id


class RayJobAlreadyExistsError(RuntimeError):
    """Raised when a caller-provided Ray submission ID already exists."""

    def __init__(self, submission_id: str):
        self.submission_id = submission_id
        super().__init__(f"Ray job {submission_id!r} already exists")


@dataclass(frozen=True, slots=True)
class RayJobSubmission:
    """One fully reconcilable Ray job-submission request."""

    entrypoint: str
    submission_id: str
    runtime_env: dict | None = None
    metadata: dict | None = None


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
    # ray://my-cluster.anyscale.com:10001 → https://my-cluster.anyscale.com:8265
    # (external HTTPS-by-default for Anyscale; configurable via RAY_DASHBOARD_URL override)
    explicit_dashboard = os.environ.get("RAY_DASHBOARD_URL", "").strip()
    if explicit_dashboard:
        return explicit_dashboard
    if ray_addr.startswith("ray://"):
        host = ray_addr[len("ray://"):].rsplit(":", 1)[0]
        normalized_host = host.rstrip(".").lower()
        # Only the known hosted Ray control plane implies TLS. Dotted LAN
        # names and IPv4 addresses commonly serve the dashboard over HTTP;
        # other TLS deployments must use the explicit override above.
        scheme = "https" if normalized_host.endswith(".anyscale.com") else "http"
        return f"{scheme}://{host.rstrip('.')}:8265"
    return None


def _transport_timeouts() -> tuple:
    """The requests timeout classes (requests ships with ray; lazy import)."""
    import requests.exceptions

    return (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
            requests.exceptions.Timeout)


def _bind_transport_deadline(client) -> None:
    """Inject a default transport timeout at the pinned SDK boundary.

    Ray 2.56.0's ``SubmissionClient._do_request`` documents that extra
    keyword arguments are forwarded to ``requests.request()`` (verified
    against the pinned source; see #1170). Wrapping it here bounds every
    control-plane call the SDK makes without altering any explicitly
    passed timeout. Fail closed on an SDK shape change: an unbounded
    client must never be constructed silently.
    """
    original = getattr(client, "_do_request", None)
    if original is None or not callable(original):
        raise RuntimeError(
            "Ray SDK no longer exposes SubmissionClient._do_request — "
            "re-verify the transport-deadline seam against the pinned "
            "version before upgrading (#1170)"
        )

    def bounded(method, endpoint, **kwargs):
        kwargs.setdefault(
            "timeout",
            (_RAY_CONNECT_TIMEOUT_SECONDS, _RAY_READ_TIMEOUT_SECONDS),
        )
        return original(method, endpoint, **kwargs)

    client._do_request = bounded


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
            client = JobSubmissionClient(self._addr)
            _bind_transport_deadline(client)
            self._client = client
        return self._client

    def submit_job(self, submission: RayJobSubmission) -> str:
        client = self._ensure_client()
        try:
            return client.submit_job(
                entrypoint=submission.entrypoint,
                runtime_env=submission.runtime_env or {},
                metadata=submission.metadata or {},
                submission_id=submission.submission_id,
            )
        except _transport_timeouts() as exc:
            # Sent but unanswered: acceptance is AMBIGUOUS. Never resubmit
            # blindly — hand back the stable id for reconciliation.
            raise RaySubmissionAmbiguousError(submission.submission_id) from exc
        except RuntimeError:
            try:
                client.get_job_info(submission.submission_id)
            except Exception:
                raise
            raise RayJobAlreadyExistsError(submission.submission_id) from None

    def get_job_status(self, job_id: str) -> dict:
        client = self._ensure_client()
        try:
            return {
                "job_id": job_id,
                "status": client.get_job_status(job_id).value,
                "info": client.get_job_info(job_id).__dict__,
            }
        except _transport_timeouts() as exc:
            raise RayControlPlaneTimeoutError("job status fetch", job_id) from exc

    def get_job_logs(self, job_id: str) -> str:
        try:
            return self._ensure_client().get_job_logs(job_id)
        except _transport_timeouts() as exc:
            raise RayControlPlaneTimeoutError("job log fetch", job_id) from exc

    def stop_job(self, job_id: str) -> bool:
        try:
            return self._ensure_client().stop_job(job_id)
        except _transport_timeouts() as exc:
            # Stopping is idempotent server-side; a timed-out stop is safe to
            # repeat with the same id and can neither duplicate nor lose the
            # accepted job.
            raise RayControlPlaneTimeoutError("job stop", job_id) from exc

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
