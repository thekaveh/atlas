"""Bounded in-memory job lifecycle with disk-backed uploads and results."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal


JobStatus = Literal["pending", "started", "success", "failure"]
logger = logging.getLogger(__name__)


@dataclass
class Reservation:
    claimed: bool = False
    released: bool = False


@dataclass
class Job:
    task_id: str
    upload_path: Path
    upload_name: str
    status: JobStatus = "pending"
    result_path: Path | None = None
    completed_at: float | None = None
    owns_slot: bool = True
    task: asyncio.Task[None] | None = None
    result_claimed: bool = False


class JobRegistry:
    def __init__(
        self,
        *,
        root: Path,
        max_jobs: int,
        result_ttl_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_jobs < 1:
            raise ValueError("max_jobs must be positive")
        if result_ttl_seconds < 1:
            raise ValueError("result_ttl_seconds must be positive")
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_jobs = max_jobs
        self.result_ttl_seconds = result_ttl_seconds
        self.clock = clock
        self._jobs: dict[str, Job] = {}
        self._cleanup_pending: dict[str, Job] = {}
        self._occupied = 0
        self._lock = asyncio.Lock()

    async def reserve(self) -> Reservation | None:
        async with self._lock:
            self._cleanup_expired_locked()
            if self._occupied >= self.max_jobs:
                return None
            self._occupied += 1
            return Reservation()

    async def release_reservation(self, reservation: Reservation) -> None:
        async with self._lock:
            if reservation.claimed or reservation.released:
                return
            reservation.released = True
            self._occupied -= 1

    async def start(
        self,
        reservation: Reservation,
        *,
        upload_path: Path,
        upload_name: str,
        worker: Callable[[Path, str], Awaitable[bytes | Path]],
    ) -> str:
        async with self._lock:
            if reservation.released or reservation.claimed:
                raise RuntimeError("invalid adapter job reservation")
            reservation.claimed = True
            task_id = secrets.token_urlsafe(24)
            job = Job(task_id=task_id, upload_path=upload_path, upload_name=upload_name)
            self._jobs[task_id] = job
            job.task = asyncio.create_task(self._run(job, worker))
            return task_id

    async def abandon_upload(
        self, reservation: Reservation, *, upload_path: Path
    ) -> None:
        """Delete or retain a pre-start upload without releasing its bound."""
        async with self._lock:
            if reservation.claimed:
                return
            if reservation.released:
                raise RuntimeError("cannot adopt a released adapter reservation")
            reservation.claimed = True
            task_id = f"cleanup-{secrets.token_urlsafe(24)}"
            job = Job(
                task_id=task_id,
                upload_path=upload_path,
                upload_name=upload_path.name,
                status="failure",
                completed_at=self.clock(),
            )
            if self._delete_job_files(job):
                self._release_job_slot_locked(job)
            else:
                self._cleanup_pending[task_id] = job

    async def _run(
        self,
        job: Job,
        worker: Callable[[Path, str], Awaitable[bytes | Path]],
    ) -> None:
        job.status = "started"
        result_path: Path | None = None
        try:
            result = await worker(job.upload_path, job.upload_name)
            if isinstance(result, Path):
                result_path = result
            else:
                executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="docling-adapter-result",
                )
                concurrent_write = executor.submit(self._write_result, result)
                write_future = asyncio.wrap_future(concurrent_write)
                cancelled = False
                try:
                    while True:
                        try:
                            result_path = await asyncio.shield(write_future)
                            break
                        except asyncio.CancelledError:
                            cancelled = True
                except Exception:
                    if cancelled:
                        raise asyncio.CancelledError
                    raise
                finally:
                    executor.shutdown(wait=False)
                if cancelled:
                    self._delete_path(result_path, job.task_id)
                    raise asyncio.CancelledError
            async with self._lock:
                job.result_path = result_path
                job.status = "success"
                job.completed_at = self.clock()
        except asyncio.CancelledError:
            job.result_path = result_path
            cleaned = self._delete_job_files(job)
            async with self._lock:
                job.status = "failure"
                job.completed_at = self.clock()
                if cleaned:
                    self._release_job_slot_locked(job)
            raise
        except Exception as exc:
            job.result_path = result_path
            cleaned = self._delete_job_files(job)
            logger.error(
                "adapter job failed (task_id=%s, error_type=%s)",
                job.task_id,
                type(exc).__name__,
            )
            async with self._lock:
                job.status = "failure"
                job.completed_at = self.clock()
                if cleaned:
                    self._release_job_slot_locked(job)
        finally:
            if job.status == "success":
                self._delete_path(job.upload_path, job.task_id)

    def _write_result(self, payload: bytes) -> Path:
        fd, raw_path = tempfile.mkstemp(suffix=".zip", dir=self.root)
        path = Path(raw_path)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    async def status(self, task_id: str) -> JobStatus | None:
        async with self._lock:
            self._cleanup_expired_locked()
            job = self._jobs.get(task_id)
            return job.status if job else None

    async def claim_result(self, task_id: str) -> Path | None:
        async with self._lock:
            self._cleanup_expired_locked()
            job = self._jobs.get(task_id)
            if (
                job is None
                or job.status != "success"
                or job.result_path is None
                or job.result_claimed
            ):
                return None
            job.result_claimed = True
            return job.result_path

    async def finish_result(self, task_id: str) -> None:
        async with self._lock:
            job = self._jobs.pop(task_id, None)
            if job is None:
                return
            if self._delete_job_files(job):
                self._release_job_slot_locked(job)
            else:
                self._cleanup_pending[task_id] = job

    async def cleanup_expired(self) -> None:
        async with self._lock:
            self._cleanup_expired_locked()

    def _cleanup_expired_locked(self) -> None:
        self._retry_pending_cleanup_locked()
        now = self.clock()
        expired = [
            task_id
            for task_id, job in self._jobs.items()
            if job.completed_at is not None
            and not job.result_claimed
            and now - job.completed_at >= self.result_ttl_seconds
        ]
        for task_id in expired:
            job = self._jobs.pop(task_id)
            if self._delete_job_files(job):
                self._release_job_slot_locked(job)
            else:
                self._cleanup_pending[task_id] = job

    def _retry_pending_cleanup_locked(self) -> None:
        for task_id, job in list(self._cleanup_pending.items()):
            if self._delete_job_files(job):
                self._release_job_slot_locked(job)
                del self._cleanup_pending[task_id]

    def _release_job_slot_locked(self, job: Job) -> None:
        if job.owns_slot:
            job.owns_slot = False
            self._occupied -= 1

    @staticmethod
    def _delete_path(path: Path, task_id: str) -> bool:
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            logger.warning(
                "adapter job cleanup deferred (task_id=%s, error_type=%s)",
                task_id,
                type(exc).__name__,
            )
            return False

    def _delete_job_files(self, job: Job) -> bool:
        cleaned = self._delete_path(job.upload_path, job.task_id)
        if job.result_path is not None:
            cleaned = self._delete_path(job.result_path, job.task_id) and cleaned
        return cleaned

    async def close(self) -> None:
        async with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.task is not None and not job.task.done():
                job.task.cancel()
        if jobs:
            await asyncio.gather(
                *(job.task for job in jobs if job.task is not None),
                return_exceptions=True,
            )
        async with self._lock:
            for job in self._jobs.values():
                self._release_job_slot_locked(job)
                if not self._delete_job_files(job):
                    self._cleanup_pending[job.task_id] = job
            self._jobs.clear()
            self._retry_pending_cleanup_locked()
            self._cleanup_pending.clear()
