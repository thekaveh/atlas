"""Local rerank admission/deadline regressions; no live provider or socket."""
import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
import threading
import gc
import weakref

import httpx
import pytest

import lightrag_rerank_adapter as adapter
from lightrag_rerank_admission import RerankAdmission, RerankAdmissionUnavailable


class CountingExecutor(ThreadPoolExecutor):
    def __init__(self):
        super().__init__(max_workers=8, thread_name_prefix="test-rerank")
        self.submissions = 0

    def submit(self, fn, *args, **kwargs):
        self.submissions += 1
        return super().submit(fn, *args, **kwargs)


class BlockedProvider:
    def __init__(self):
        self.release = threading.Event()
        self.started = 0
        self.lock = threading.Lock()
        self.loop = None
        self.all_started = None

    def bind(self, count):
        self.loop = asyncio.get_running_loop()
        self.all_started = asyncio.Event()
        self.target = count

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        with self.lock:
            self.started += 1
            if self.started == self.target:
                self.loop.call_soon_threadsafe(self.all_started.set)
        assert self.release.wait(5), "test did not release provider"
        return httpx.Response(200, json=[{"index": 0, "score": 0.9}])


@pytest.fixture
def route_setup(monkeypatch):
    monkeypatch.setenv("KONG_URL", "http://unused")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("LIGHTRAG_RERANK_ADAPTER_TOKEN", "test-token")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://unused")
    import main
    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)
    executor = CountingExecutor()
    provider = BlockedProvider()
    monkeypatch.setattr(main, "_RERANK_EXECUTOR", executor)
    monkeypatch.setattr(main, "_RERANK_ADMISSION", None, raising=False)
    monkeypatch.setattr(adapter.httpx, "Client", lambda **kw: provider)
    monkeypatch.setattr(main, "get_celery_job_status", lambda job: { "job_id": job, "status": "SUCCESS", "ready": True, "successful": True, "failed": False, "result": {} })
    yield main, executor, provider
    provider.release.set()
    executor.shutdown(wait=True)


def client_for(main):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test",
                             headers={"Authorization": "Bearer test-token"})


async def checkpoint():
    done = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(done.set_result, None)
    await done


async def post(client, documents=None):
    return await client.post("/lightrag/rerank", json={"query": "q", "documents": ["a"] if documents is None else documents})


async def drain(tasks, provider):
    provider.release.set()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_saturation_bounds_admission_and_keeps_other_routes_responsive(route_setup, monkeypatch):
    main, executor, provider = route_setup

    async def scenario():
        provider.bind(8)
        async with client_for(main) as client:
            tasks = [asyncio.create_task(post(client)) for _ in range(8)]
            try:
                await asyncio.wait_for(provider.all_started.wait(), 2)
                await queue_route_calls(client, main._rerank_admission(), tasks)
                excess = await asyncio.wait_for(post(client), 0.5)
                assert excess.status_code == 503
                assert excess.headers["Retry-After"] == "5"
                assert executor.submissions == 8
                assert len(main._rerank_admission()._pending) == 8
                await assert_other_routes(client, monkeypatch)
            finally:
                await drain(tasks, provider)
        assert provider.started == 16
    asyncio.run(scenario())


async def queue_route_calls(client, gate, tasks):
    queued = asyncio.Event()
    admit = gate._admit

    def observe(job):
        admit(job)
        if len(gate._pending) == 8:
            queued.set()
    gate._admit = observe
    try:
        tasks.extend(asyncio.create_task(post(client)) for _ in range(8))
        await asyncio.wait_for(queued.wait(), 2)
    finally:
        gate._admit = admit


async def assert_other_routes(client, monkeypatch):
    health = await asyncio.wait_for(client.get("/health"), 0.5)
    job = await asyncio.wait_for(client.get("/jobs/test-job"), 0.5)
    assert health.status_code == job.status_code == 200
    assert (await client.post("/lightrag/rerank", json={"query": "q"}, headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await client.post("/lightrag/rerank", json={"query": ""})).status_code == 422
    monkeypatch.delenv("TEI_RERANKER_ENDPOINT")
    assert (await asyncio.wait_for(post(client, []), 0.5)).json() == {"results": []}
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://unused")


def test_caller_wall_timeout_does_not_release_running_slot(route_setup, monkeypatch):
    main, executor, provider = route_setup
    monkeypatch.setenv("LIGHTRAG_RERANK_ADAPTER_TIMEOUT_SECONDS", "0.04")

    async def scenario():
        provider.bind(1)
        async with client_for(main) as client:
            task = asyncio.create_task(post(client))
            try:
                await asyncio.wait_for(provider.all_started.wait(), 2)
                response = await asyncio.wait_for(asyncio.shield(task), 0.5)
                assert response.status_code == 504
                assert main._rerank_admission()._submitted == 1
            finally:
                await drain([task], provider)
        executor.shutdown(wait=True)
        assert main._rerank_admission()._submitted == 0
    asyncio.run(scenario())


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class ControlledExecutor:
    """Retain physical work until the test explicitly executes it."""
    def __init__(self):
        self.jobs = []

    def submit(self, fn, job):
        future = Future()
        self.jobs.append((fn, job, future))
        return future

    def complete(self, index):
        fn, job, future = self.jobs[index]
        assert not future.cancelled()
        try:
            result = fn(job)
        except BaseException as error:
            future.set_exception(error)
        else:
            future.set_result(result)


def request():
    return adapter.RerankAdapterRequest(query="q", documents=["payload"])


def success(req, budget):
    return adapter.RerankAdapterResponse(results=[])


async def fill(gate):
    tasks = [asyncio.create_task(gate.run(request(), adapter.RerankBudget(30))) for _ in range(8)]
    await checkpoint()
    assert gate._submitted == 8
    return tasks


async def cancel_tasks(tasks):
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("departure", ["cancel", "timeout"])
def test_repeated_pending_departure_removes_payload_without_executor_tombstones(departure):
    executor = ControlledExecutor()
    gate = RerankAdmission(executor, success)

    async def scenario():
        active = await fill(gate)
        try:
            for _ in range(20):
                payload = request()
                ref = weakref.ref(payload)
                # A frozen clock keeps admission live; only the 1 ms caller wait expires.
                budget = (adapter.RerankBudget(0.001, clock=lambda: 0.0)
                          if departure == "timeout" else adapter.RerankBudget(30))
                task = asyncio.create_task(gate.run(payload, budget))
                del payload
                await checkpoint()
                job = gate._pending[0]
                if departure == "cancel":
                    task.cancel()
                outcome, = await asyncio.gather(task, return_exceptions=True)
                assert isinstance(outcome, asyncio.CancelledError if departure == "cancel"
                                  else adapter.RerankAdapterTimeoutError)
                del task, outcome
                gc.collect()
                assert job.request is None and ref() is None
                assert not gate._pending
                assert gate._submitted == len(executor.jobs) == 8
        finally:
            await cancel_tasks(active)
            for index in range(8):
                executor.complete(index)
        assert gate._submitted == 0
    asyncio.run(scenario())


def test_dispatched_cancellation_clears_payload_but_reserves_physical_slot():
    executor = ControlledExecutor()
    calls = []
    gate = RerankAdmission(executor, lambda req, budget: calls.append(req))

    async def scenario():
        task = asyncio.create_task(gate.run(request(), adapter.RerankBudget(30)))
        await checkpoint()
        job = executor.jobs[0][1]
        await cancel_tasks([task])
        assert job.request is None
        assert gate._submitted == 1
        executor.complete(0)
        assert calls == []
        assert gate._submitted == 0
    asyncio.run(scenario())


def test_queue_time_is_deducted_before_client_and_each_provider_batch(monkeypatch):
    clock = Clock()
    executor = ControlledExecutor()
    observed = []
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://unused")
    monkeypatch.setenv("TEI_RERANKER_MAX_CLIENT_BATCH_SIZE", "1")
    install_advancing_client(monkeypatch, clock, observed)
    gate = RerankAdmission(executor, adapter.rerank_via_tei)

    async def scenario():
        active = await fill(gate)
        task = asyncio.create_task(gate.run(adapter.RerankAdapterRequest(query="q", documents=["a", "b"]), adapter.RerankBudget(30, clock)))
        await checkpoint()
        clock.now += 12
        await cancel_tasks(active)
        for index in range(8):
            executor.complete(index)
        executor.complete(8)
        result = await task
        assert len(result.results) == 2
        assert observed == [18, 18, 13]
        assert gate._submitted == 0
    asyncio.run(scenario())


def install_advancing_client(monkeypatch, clock, observed):
    class Client:
        def __init__(self, *, timeout):
            observed.append(timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, *, json, timeout):
            observed.append(timeout)
            clock.now += 5
            return httpx.Response(200, json=[{"index": 0, "score": 0.9}])
    monkeypatch.setattr(adapter.httpx, "Client", Client)


@pytest.mark.parametrize("trigger", ["admission", "dispatch"])
def test_expired_pending_is_reclaimed_before_admission_or_dispatch(trigger):
    executor = ControlledExecutor()
    gate = RerankAdmission(executor, success)
    clock = Clock()

    async def scenario():
        active = await fill(gate)
        pending = [asyncio.create_task(gate.run(request(), adapter.RerankBudget(30, clock))) for _ in range(8)]
        await checkpoint()
        jobs = list(gate._pending)
        clock.now += 31
        if trigger == "admission":
            replacement = asyncio.create_task(gate.run(request(), adapter.RerankBudget(30)))
            await checkpoint()
            assert len(gate._pending) == 1
            await cancel_tasks([replacement])
        else:
            executor.complete(0)
        results = await asyncio.gather(*pending, return_exceptions=True)
        assert all(isinstance(error, adapter.RerankAdapterTimeoutError) for error in results)
        assert all(job.request is None for job in jobs)
        assert len(executor.jobs) == 8
        await cancel_tasks(active)
        for _, job, future in executor.jobs:
            if not future.done():
                future.set_result(None)
        assert gate._submitted == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("elapsed", [30, 31])
def test_late_final_batch_cannot_return_success(monkeypatch, elapsed):
    clock = Clock()
    budget = adapter.RerankBudget(30, clock)
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://unused")
    class Client(BlockedProvider):
        def post(self, url, **kwargs):
            clock.now += elapsed
            return httpx.Response(200, json=[{"index": 0, "score": 0.9}])
    monkeypatch.setattr(adapter.httpx, "Client", lambda **kw: Client())
    with pytest.raises(adapter.RerankAdapterTimeoutError):
        adapter.rerank_via_tei(request(), budget)


def test_running_cancellation_stops_the_next_batch(route_setup, monkeypatch):
    main, executor, provider = route_setup
    monkeypatch.setenv("TEI_RERANKER_MAX_CLIENT_BATCH_SIZE", "1")

    async def scenario():
        provider.bind(1)
        async with client_for(main) as client:
            task = asyncio.create_task(post(client, ["a", "b"]))
            try:
                await asyncio.wait_for(provider.all_started.wait(), 2)
                await cancel_tasks([task])
                assert main._rerank_admission()._submitted == 1
            finally:
                provider.release.set()
                executor.shutdown(wait=True)
            assert provider.started == 1
            assert main._rerank_admission()._submitted == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("partial", [False, True])
def test_submission_failure_fails_closed_and_drops_uncertain_payload(partial):
    class BrokenExecutor(ControlledExecutor):
        def submit(self, fn, job):
            if partial:
                super().submit(fn, job)
            raise RuntimeError("private payload detail")
    executor = BrokenExecutor()
    gate = RerankAdmission(executor, success)

    async def scenario():
        with pytest.raises(RerankAdmissionUnavailable, match="^Rerank admission unavailable; retry shortly$"):
            await gate.run(request(), adapter.RerankBudget(30))
        assert gate._unavailable and gate._submitted == 1
        with pytest.raises(RerankAdmissionUnavailable):
            await gate.run(request(), adapter.RerankBudget(30))
        assert len(executor.jobs) == int(partial)
        if partial:
            assert executor.jobs[0][1].request is None
            executor.complete(0)
        assert gate._submitted == 1
    asyncio.run(scenario())


def test_later_submit_failure_clears_waiters_and_existing_work_finishes():
    class BrokenExecutor(ControlledExecutor):
        def submit(self, fn, job):
            future = super().submit(fn, job)
            if len(self.jobs) == 9:
                raise RuntimeError("partial enqueue")
            return future
    executor = BrokenExecutor()
    gate = RerankAdmission(executor, success)

    async def scenario():
        active = await fill(gate)
        waiting = [asyncio.create_task(gate.run(request(), adapter.RerankBudget(30))) for _ in range(8)]
        await checkpoint()
        jobs = list(gate._pending)
        executor.complete(0)
        errors = await asyncio.gather(*waiting, return_exceptions=True)
        assert all(isinstance(error, RerankAdmissionUnavailable) for error in errors)
        assert all(job.request is None for job in jobs)
        assert not gate._pending
        for index in range(1, 9):
            executor.complete(index)
        await asyncio.gather(*active)
        assert gate._submitted == 1 and len(executor.jobs) == 9
    asyncio.run(scenario())


@pytest.mark.parametrize("winner", ["cancel", "complete"])
def test_completion_cancellation_race_releases_once(winner):
    executor = ControlledExecutor()
    gate = RerankAdmission(executor, success)

    async def scenario():
        for index in range(30):
            task = asyncio.create_task(gate.run(request(), adapter.RerankBudget(30)))
            await checkpoint()
            if winner == "complete":
                executor.complete(index)
                task.cancel()
            else:
                task.cancel()
                executor.complete(index)
            await asyncio.gather(task, return_exceptions=True)
            assert gate._submitted == 0
            assert not gate._pending
    asyncio.run(scenario())


def test_immediate_callbacks_pump_iteratively_without_recursion():
    class ImmediateExecutor:
        def submit(self, fn, job):
            result = Future()
            result.set_result(fn(job))
            return result
    gate = RerankAdmission(ImmediateExecutor(), success)

    async def scenario():
        for _ in range(300):
            assert (await gate.run(request(), adapter.RerankBudget(30))).results == []
        assert gate._submitted == 0 and not gate._pumping
    asyncio.run(scenario())



def test_one_broker_delivers_to_two_independent_loops():
    started = threading.Barrier(3)
    release = threading.Event()
    loops = set()

    def worker(req, budget):
        started.wait(timeout=3)
        assert release.wait(3)
        return success(req, budget)

    async def call(gate):
        loops.add(asyncio.get_running_loop())
        return await gate.run(request(), adapter.RerankBudget(30))

    with ThreadPoolExecutor(max_workers=8) as workers:
        gate = RerankAdmission(workers, worker)
        with ThreadPoolExecutor(max_workers=2) as callers:
            calls = [callers.submit(lambda: asyncio.run(call(gate))) for _ in range(2)]
            try:
                started.wait(timeout=3)
                assert gate._submitted == 2
            finally:
                release.set()
            assert [item.result(timeout=3).results for item in calls] == [[], []]
        assert len(loops) == 2
        assert gate._submitted == 0


def test_closed_loop_completion_releases_slot_and_dispatches_another_loop():
    from lightrag_rerank_admission import _Job
    executor = ControlledExecutor()
    gate = RerankAdmission(executor, success)
    closed = asyncio.new_event_loop()

    async def admit_without_a_waiter():
        jobs = [_Job(request(), adapter.RerankBudget(30)) for _ in range(8)]
        for job in jobs:
            gate._admit(job)
        gate._pump()
    closed.run_until_complete(admit_without_a_waiter())
    closed.close()

    async def scenario():
        task = asyncio.create_task(gate.run(request(), adapter.RerankBudget(30)))
        await checkpoint()
        assert len(gate._pending) == 1
        for index in range(9):
            executor.complete(index)
        assert (await task).results == []
        assert gate._submitted == 0
    asyncio.run(scenario())


def test_process_singleton_is_shared_across_caller_threads(route_setup):
    main, executor, provider = route_setup
    barrier = threading.Barrier(8)

    def get_gate():
        barrier.wait(timeout=3)
        return main._rerank_admission(), main._rerank_executor()
    with ThreadPoolExecutor(max_workers=8) as callers:
        pairs = list(callers.map(lambda _: get_gate(), range(8)))
    assert len({id(gate) for gate, _ in pairs}) == 1
    assert all(pool is executor for _, pool in pairs)


def test_submit_failure_route_is_sanitized_503_with_retry_guidance(route_setup, monkeypatch, caplog):
    main, executor, provider = route_setup

    def broken_submit(fn, job):
        raise RuntimeError("private query/document text")
    monkeypatch.setattr(executor, "submit", broken_submit)

    async def scenario():
        async with client_for(main) as client:
            for _ in range(2):
                response = await post(client)
                assert response.status_code == 503
                assert response.headers["Retry-After"] == "5"
                assert response.json() == {"detail": "Rerank admission unavailable; retry shortly"}
        assert "private query/document text" not in caplog.text
    asyncio.run(scenario())


@pytest.mark.parametrize("error", [TimeoutError("worker timeout"), adapter.RerankAdapterDependencyError("dependency"), adapter.RerankAdapterUpstreamError("upstream")])
def test_worker_errors_keep_their_original_meaning(error):
    executor = ControlledExecutor()

    def fail(req, budget):
        raise error
    gate = RerankAdmission(executor, fail)

    async def scenario():
        task = asyncio.create_task(gate.run(request(), adapter.RerankBudget(30)))
        await checkpoint()
        executor.complete(0)
        with pytest.raises(type(error)) as caught:
            await task
        assert caught.value is error
        assert gate._submitted == 0
    asyncio.run(scenario())


def test_expired_budget_never_opens_a_client(monkeypatch):
    clock = Clock()
    budget = adapter.RerankBudget(30, clock)
    clock.now += 30
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://unused")

    def forbidden(**kwargs):
        pytest.fail("expired work opened an HTTP client")
    monkeypatch.setattr(adapter.httpx, "Client", forbidden)
    with pytest.raises(adapter.RerankAdapterTimeoutError):
        adapter.rerank_via_tei(request(), budget)


def test_dispatch_pump_serializes_immediate_completion_of_waiters(monkeypatch):
    executor = ControlledExecutor()
    gate = RerankAdmission(executor, success)
    depth = [0, 0]
    pump = gate._pump

    def observed_pump():
        depth[0] += 1
        depth[1] = max(depth)
        try:
            pump()
        finally:
            depth[0] -= 1
    monkeypatch.setattr(gate, "_pump", observed_pump)

    def immediate(fn, job):
        future = Future()
        future.set_result(fn(job))
        return future

    async def scenario():
        tasks = await fill(gate)
        tasks.extend(asyncio.create_task(gate.run(request(), adapter.RerankBudget(30))) for _ in range(8))
        await checkpoint()
        monkeypatch.setattr(executor, "submit", immediate)
        for index in range(8):
            executor.complete(index)
        await asyncio.gather(*tasks)
        assert depth[1] == 2
        assert gate._submitted == 0
    asyncio.run(scenario())


def test_cancellation_consumes_an_already_delivered_worker_error(monkeypatch):
    import lightrag_rerank_admission as admission
    executor = ControlledExecutor()
    gate = RerankAdmission(executor, success)

    async def scenario():
        errors = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda loop, context: errors.append(context))
        task = asyncio.create_task(gate.run(request(), adapter.RerankBudget(30)))
        await checkpoint()
        bridge = weakref.ref(executor.jobs[0][1].result)
        deliver = admission._deliver

        def deliver_then_cancel(future, outcome):
            deliver(future, outcome)
            task.cancel()
        monkeypatch.setattr(admission, "_deliver", deliver_then_cancel)
        executor.jobs[0][2].set_exception(RuntimeError("late worker error"))
        results = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        del task
        await checkpoint()
        gc.collect()
        assert bridge() is None
        assert errors == []
        assert gate._submitted == 0
    asyncio.run(scenario())
