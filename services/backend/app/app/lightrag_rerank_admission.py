"""Process-local bounded admission for the dedicated synchronous rerank pool."""
from __future__ import annotations

import asyncio
from collections import deque
import logging
import threading

from lightrag_rerank_adapter import RerankAdapterTimeoutError

logger = logging.getLogger(__name__)
MAX_SUBMITTED = 8
MAX_PENDING = 8
MAX_ADMITTED = 16


class RerankAdmissionUnavailable(RuntimeError):
    """Admission is full or the executor can no longer accept work."""


class _Job:
    def __init__(self, request, budget):
        self.request = request
        self.budget = budget
        self.loop = asyncio.get_running_loop()
        self.result = self.loop.create_future()
        self.phase = "pending"


def _deliver(future, outcome):
    if future.done():
        return
    value, error = outcome
    if error is None:
        future.set_result(value)
    else:
        future.set_exception(error)


class RerankAdmission:
    """Keep waiting payloads out of ThreadPoolExecutor's unbounded queue.

    Only physical completion releases a submitted slot. The caller's Future
    is separate, owned by its event loop, and never cancels physical work.
    """

    def __init__(self, executor, worker):
        self._executor = executor
        self._worker = worker
        self._lock = threading.Lock()
        self._pending = deque()
        self._submitted = 0
        self._pumping = False
        self._unavailable = False

    async def run(self, request, budget):
        budget.remaining()
        job = _Job(request, budget)
        request = None  # A departed coroutine traceback must not retain payload.
        future = job.result
        self._admit(job)
        try:
            self._pump()
            done, _ = await asyncio.wait({future}, timeout=self._wait_seconds(job))
            if not done:
                raise RerankAdapterTimeoutError("TEI /rerank caller deadline expired")
            result = future.result()
            budget.remaining()
            return result
        finally:
            self._abandon(job)
            # This is only the caller bridge, never the physical Future.
            future.cancel()

    def _wait_seconds(self, job):
        with self._lock:
            if job.phase == "unavailable":
                raise RerankAdmissionUnavailable("Rerank admission unavailable; retry shortly")
            return job.budget.remaining()

    def _expire_pending(self):
        """Called with the lock held; removed jobs own no payload."""
        expired = []
        for job in list(self._pending):
            try:
                job.budget.remaining()
            except RerankAdapterTimeoutError:
                self._pending.remove(job)
                job.request = None
                job.phase = "finished"
                expired.append(job)
        return expired

    def _notify_expired(self, jobs):
        for job in jobs:
            self._notify(job, (None, RerankAdapterTimeoutError(
                "TEI /rerank caller deadline expired while waiting"
            )))

    def _admit(self, job):
        with self._lock:
            expired = self._expire_pending()
            full = (self._unavailable or len(self._pending) >= MAX_PENDING
                    or self._submitted + len(self._pending) >= MAX_ADMITTED)
            if not full:
                self._pending.append(job)
        self._notify_expired(expired)
        if full:
            raise RerankAdmissionUnavailable("Rerank admission unavailable; retry shortly")

    def _abandon(self, job):
        with self._lock:
            job.budget.cancel()
            job.request = None
            job.loop = job.result = None
            if job.phase == "pending":
                self._pending.remove(job)
                job.phase = "finished"
        self._pump()

    def _notify(self, job, outcome):
        with self._lock:
            loop, future = job.loop, job.result
            job.loop = job.result = None
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(_deliver, future, outcome)
        except RuntimeError:
            # A closed caller loop cannot prevent accounting or dispatch.
            pass

    def _claim(self, job):
        with self._lock:
            request = job.request
            job.request = None
            if request is not None:
                job.phase = "running"
        if request is None:
            return None
        job.budget.remaining()
        return self._worker(request, job.budget)

    def _finish(self, job, physical):
        with self._lock:
            self._submitted -= 1
            job.phase = "finished"
            job.request = None
        try:
            outcome = (physical.result(), None)
        except BaseException as exc:
            outcome = (None, exc)
        self._notify(job, outcome)
        self._pump()

    def _fail_closed(self, uncertain, error):
        # CPython may enqueue a work item before submit raises. Its unknown
        # Future cannot be tracked: permanently retain that reservation.
        with self._lock:
            self._unavailable = True
            self._pumping = False
            jobs = [uncertain, *self._pending]
            self._pending.clear()
            for job in jobs:
                job.request = None
                job.budget.cancel()
                job.phase = "unavailable"
        for job in jobs:
            self._notify(job, (None, RerankAdmissionUnavailable(
                "Rerank admission unavailable; retry shortly"
            )))
        logger.error("Rerank executor submission failed; admission closed (error_type=%s)",
                     type(error).__name__)

    def _pump(self):
        with self._lock:
            if self._pumping or self._unavailable:
                return
            self._pumping = True
        while True:
            with self._lock:
                expired = self._expire_pending()
                job = self._reserve()
                if job is None:
                    self._pumping = False
            self._notify_expired(expired)
            if job is None:
                return
            try:
                physical = self._executor.submit(self._claim, job)
            except Exception as error:
                self._fail_closed(job, error)
                return
            # May execute immediately: never hold the lock or recurse into
            # dispatch. Only the small job, never its payload, is captured.
            physical.add_done_callback(lambda done, job=job: self._finish(job, done))

    def _reserve(self):
        """Called under the lock by the serialized pump."""
        if self._unavailable or not self._pending or self._submitted >= MAX_SUBMITTED:
            return None
        job = self._pending.popleft()
        job.phase = "submitted"
        self._submitted += 1
        return job
