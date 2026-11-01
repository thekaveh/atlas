"""Finite Redis socket timeouts for the memory-coordination clients (#1172).

Split from test_async_jobs.py to keep that module inside the maintenance
ledger's 600-logical-line ceiling; the ownership/fencing behavior these
transport bounds protect stays pinned there.

The live-Redis tests at the end run the lease compare-and-set scripts
themselves. They skip unless ATLAS_TEST_REDIS_URL names disposable database 15.
"""

from __future__ import annotations

import pytest
# ---------------------------------------------------------------------------
# Finite Redis socket timeouts for coordination clients (#1172)
# ---------------------------------------------------------------------------

def test_coordination_clients_carry_finite_socket_timeouts(monkeypatch):
    """All three claim/release/complete helpers must construct their client
    through the bounded factory — never a default (infinite) redis-py client."""
    import redis

    import celery_app

    captured = []

    class _Client:
        def set(self, *a, **k):
            return True

        def get(self, *a, **k):
            return ""

        def eval(self, *a, **k):
            return 1

        def close(self):
            pass

    def fake_from_url(url, **kwargs):
        captured.append(kwargs)
        return _Client()

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://redis:6379/4")

    celery_app.claim_memory_execution("t1", "owner-a")
    celery_app.release_memory_execution("t1", "owner-a")
    celery_app.complete_memory_execution("t1", "owner-a", {"ok": True})

    assert len(captured) == 3
    for kwargs in captured:
        assert kwargs.get("socket_connect_timeout") == celery_app._REDIS_CONNECT_TIMEOUT_SECONDS
        assert kwargs.get("socket_timeout") == celery_app._REDIS_SOCKET_TIMEOUT_SECONDS
        assert kwargs["socket_connect_timeout"] is not None
        assert kwargs["socket_timeout"] is not None


def test_hung_redis_returns_within_the_transport_bound(monkeypatch):
    """A blackholed Redis (accepts TCP, never replies) must bound the claim
    call at the socket timeout instead of occupying the worker."""
    import socket
    import threading
    import time

    import redis as redis_lib

    import celery_app

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    port = listener.getsockname()[1]
    held = []

    def _accept_silent():
        try:
            conn, _ = listener.accept()
            held.append(conn)
            conn.settimeout(0.2)
            try:
                while conn.recv(65536):
                    pass
            except (TimeoutError, OSError):
                pass
        except OSError:
            pass

    threading.Thread(target=_accept_silent, daemon=True).start()
    monkeypatch.setattr(celery_app, "_redis_url", lambda: f"redis://127.0.0.1:{port}/0")
    monkeypatch.setattr(celery_app, "_REDIS_SOCKET_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(celery_app, "_REDIS_CONNECT_TIMEOUT_SECONDS", 1)

    started = time.monotonic()
    try:
        with pytest.raises((redis_lib.exceptions.TimeoutError, redis_lib.exceptions.ConnectionError)):
            celery_app.claim_memory_execution("t-hung", "owner-a")
        elapsed = time.monotonic() - started
        assert elapsed < 10  # bounded; nowhere near the 900 s task limit
    finally:
        for conn in held:
            try:
                conn.close()
            except OSError:
                pass
        listener.close()


# ---------------------------------------------------------------------------
# Execution-lease compare-and-set scripts against a live Redis
# ---------------------------------------------------------------------------

@pytest.fixture
def live_lease(monkeypatch):
    """A unique task id on the disposable Redis; only its lease key is touched."""
    from uuid import uuid4

    from redis import Redis

    import celery_app
    from tests.conftest import disposable_redis_url

    url = disposable_redis_url()
    monkeypatch.setenv("CELERY_BROKER_URL", url)
    task_id = f"lease-test-{uuid4()}"
    key = celery_app._memory_execution_key(task_id)
    client = Redis.from_url(url, decode_responses=True)
    client.delete(key)
    try:
        yield task_id, key, client
    finally:
        client.delete(key)
        client.close()


def test_a_live_lease_admits_only_its_owners_recovery(live_lease):
    import celery_app

    task_id, key, client = live_lease
    assert celery_app.claim_memory_execution(task_id, "a") == ("claimed", None)
    assert celery_app.claim_memory_execution(task_id, "b") == ("busy", None)
    assert celery_app.claim_memory_execution(task_id, "c", recovery_owner="x") == ("busy", None)
    assert client.get(key) == "running:a"

    assert celery_app.claim_memory_execution(task_id, "b", recovery_owner="a") == ("claimed", None)
    assert client.get(key) == "running:b"
    assert 0 < client.ttl(key) <= celery_app.memory_execution_lease_seconds()


def test_only_the_lease_owner_completes_or_releases_it(live_lease):
    import celery_app

    task_id, key, client = live_lease
    result = {"facts_expired": 1}
    assert celery_app.claim_memory_execution(task_id, "a") == ("claimed", None)
    assert celery_app.complete_memory_execution(task_id, "z", result) is False
    assert celery_app.release_memory_execution(task_id, "z") is False
    assert client.get(key) == "running:a"

    assert celery_app.complete_memory_execution(task_id, "a", result) is True
    assert 0 < client.ttl(key) <= celery_app._visibility_timeout
    assert celery_app.claim_memory_execution(task_id, "b") == ("done", result)
    assert celery_app.release_memory_execution(task_id, "a") is False
    assert client.get(key).startswith("done:")


def test_the_owner_release_frees_the_lease(live_lease):
    import celery_app

    task_id, key, client = live_lease
    assert celery_app.claim_memory_execution(task_id, "a") == ("claimed", None)
    assert celery_app.release_memory_execution(task_id, "a") is True
    assert client.exists(key) == 0
    assert celery_app.claim_memory_execution(task_id, "b") == ("claimed", None)
