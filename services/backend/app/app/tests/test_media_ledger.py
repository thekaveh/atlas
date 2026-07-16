from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from media_ledger import (
    STATUS_COMMITTED,
    STATUS_DENIED,
    STATUS_RELEASED,
    STATUS_RESERVED,
    STATUS_SUBMITTED,
    BudgetEngine,
    BudgetExceeded,
    InMemoryLedgerStore,
    MediaBudgetConfig,
    ProviderDisabled,
    UnknownCostRejected,
    _utcnow,
)


def _engine(**overrides) -> BudgetEngine:
    cfg = MediaBudgetConfig(enabled=True, store="memory", **overrides)
    return BudgetEngine(cfg, InMemoryLedgerStore())


_BUDGET_ENV = (
    "MEDIA_BUDGET_ENABLED",
    "MEDIA_BUDGET_STORE",
    "MEDIA_BUDGET_CURRENCY",
    "MEDIA_BUDGET_DEFAULT_USD",
    "MEDIA_BUDGET_CONSUMER_CAPS",
    "MEDIA_DISABLED_PROVIDERS",
    "MEDIA_BUDGET_ALLOW_UNKNOWN_COST",
    "MEDIA_BUDGET_RETENTION_DAYS",
    "DATABASE_URL",
)


def _budget_env(monkeypatch, **overrides):
    for name in _BUDGET_ENV:
        monkeypatch.delenv(name, raising=False)
    values = {
        "MEDIA_BUDGET_ENABLED": "true",
        "MEDIA_BUDGET_STORE": "memory",
        **overrides,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MEDIA_BUDGET_ENABLED", "tru"),
        ("MEDIA_BUDGET_STORE", "sqlite"),
        ("MEDIA_BUDGET_DEFAULT_USD", "-1"),
        ("MEDIA_BUDGET_DEFAULT_USD", "nan"),
        ("MEDIA_BUDGET_DEFAULT_USD", "not-money"),
        ("MEDIA_BUDGET_CONSUMER_CAPS", "[]"),
        ("MEDIA_BUDGET_CONSUMER_CAPS", '{"acme":true}'),
        ("MEDIA_BUDGET_CONSUMER_CAPS", '{"acme":-1}'),
        ("MEDIA_BUDGET_CONSUMER_CAPS", '{"acme":"nan"}'),
        ("MEDIA_BUDGET_ALLOW_UNKNOWN_COST", "sometimes"),
        ("MEDIA_BUDGET_RETENTION_DAYS", "0"),
        ("MEDIA_BUDGET_RETENTION_DAYS", "1.5"),
    ],
)
def test_budget_config_rejects_fail_open_values(monkeypatch, name, value):
    _budget_env(monkeypatch, **{name: value})
    with pytest.raises(ValueError, match=name):
        MediaBudgetConfig.from_env()


def test_enabled_postgres_budget_requires_database_url(monkeypatch):
    _budget_env(monkeypatch, MEDIA_BUDGET_STORE="postgres")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        MediaBudgetConfig.from_env()


def test_budget_config_accepts_finite_nonnegative_caps(monkeypatch):
    _budget_env(
        monkeypatch,
        MEDIA_BUDGET_DEFAULT_USD="10.5",
        MEDIA_BUDGET_CONSUMER_CAPS='{"acme":5,"acme:demo":0}',
        MEDIA_BUDGET_RETENTION_DAYS="30",
    )
    config = MediaBudgetConfig.from_env()
    assert config.default_cap_usd == 10.5
    assert config.consumer_caps == {"acme": 5.0, "acme:demo": 0.0}
    assert config.retention_days == 30


async def _reserve(engine, op_id, cost, *, consumer="acme", project="default", provider="fal", model="fal-ai/trellis", modality="image_to_3d"):
    return await engine.reserve(
        operation_id=op_id,
        consumer=consumer,
        project=project,
        provider=provider,
        model=model,
        modality=modality,
        estimated_cost_usd=cost,
        pricing_source_ts=_utcnow(),
    )


# --- disabled = pure no-op (preserves #340 behavior) ------------------------


def test_disabled_engine_is_noop():
    engine = BudgetEngine(MediaBudgetConfig(enabled=False), InMemoryLedgerStore())

    async def run():
        assert await _reserve(engine, "op-1", 0.05) is None
        await engine.attach_operation("op-1", "prov-1")
        await engine.reconcile(operation_id="prov-1", status="succeeded")
        summary = await engine.spend(consumer="acme")
        return summary

    summary = asyncio.run(run())
    assert summary["records"] == []
    assert summary["committed_usd"] == 0.0


# --- every generation records attribution + cost ----------------------------


def test_reserve_records_full_attribution():
    engine = _engine(default_cap_usd=10.0)

    async def run():
        rec = await _reserve(engine, "op-1", 0.05, consumer="rag-showcase", project="demo")
        stored = await engine.store.get("op-1")
        return rec, stored

    rec, stored = asyncio.run(run())
    assert stored.status == STATUS_RESERVED
    assert stored.consumer == "rag-showcase"
    assert stored.project == "demo"
    assert stored.provider == "fal"
    assert stored.model == "fal-ai/trellis"
    assert stored.estimated_cost_usd == 0.05
    assert stored.currency == "USD"
    assert stored.pricing_source_ts is not None


# --- lifecycle: reserve -> submit -> commit ---------------------------------


def test_reserve_attach_commit_lifecycle():
    engine = _engine(default_cap_usd=10.0)

    async def run():
        await _reserve(engine, "resv-1", 0.05)
        await engine.attach_operation("resv-1", "prov-42")
        # Re-keyed: old id gone, new id is submitted.
        assert await engine.store.get("resv-1") is None
        submitted = await engine.store.get("prov-42")
        await engine.reconcile(
            operation_id="prov-42", status="succeeded", final_cost_usd=0.05,
            artifact_refs=("https://cdn/x.glb",),
        )
        committed = await engine.store.get("prov-42")
        summary = await engine.spend(consumer="acme")
        return submitted, committed, summary

    submitted, committed, summary = asyncio.run(run())
    assert submitted.status == STATUS_SUBMITTED
    assert committed.status == STATUS_COMMITTED
    assert committed.final_cost_usd == 0.05
    assert committed.artifact_refs == ("https://cdn/x.glb",)
    assert summary["committed_usd"] == 0.05
    assert summary["reserved_usd"] == 0.0


# --- budget cap hard-stop ---------------------------------------------------


def test_budget_cap_hard_stops_over_limit():
    engine = _engine(default_cap_usd=0.10)

    async def run():
        await _reserve(engine, "op-1", 0.05)  # ok
        with pytest.raises(BudgetExceeded):
            await _reserve(engine, "op-2", 0.08)  # 0.05 + 0.08 > 0.10
        # Denial was recorded (not silently dropped).
        denied = await engine.store.get("op-2")
        return denied

    denied = asyncio.run(run())
    assert denied.status == STATUS_DENIED
    assert "budget exceeded" in denied.reason


def test_reserve_at_exact_cap_allowed():
    engine = _engine(default_cap_usd=0.10)

    async def run():
        await _reserve(engine, "op-1", 0.06)
        await _reserve(engine, "op-2", 0.04)  # exactly hits the cap
        with pytest.raises(BudgetExceeded):
            await _reserve(engine, "op-3", 0.01)

    asyncio.run(run())


# --- concurrency-safe reservation at the boundary ---------------------------


class _YieldingStore(InMemoryLedgerStore):
    """Adds a real suspension point in the check→append gap so two gathered
    reserve() coroutines genuinely interleave.

    The engine's critical section reads totals() then append()s. Yielding at the
    START of append (after totals was read, before the row is stored) is exactly
    the window a missing lock would expose: without the lock both coroutines read
    stale totals and both store → both admitted; with the lock the first holds it
    across the append and the second blocks → exactly one. Plain
    InMemoryLedgerStore never awaits, so the coroutines would run sequentially
    and the test would pass even with the lock deleted (zero coverage).
    """

    async def append(self, record):
        await asyncio.sleep(0)  # yield in the check→append gap
        await super().append(record)


def test_concurrent_reservations_at_boundary_admit_exactly_one():
    cfg = MediaBudgetConfig(enabled=True, store="memory", default_cap_usd=0.10)
    engine = BudgetEngine(cfg, _YieldingStore())

    async def run():
        async def attempt(op_id):
            try:
                await _reserve(engine, op_id, 0.06)
                return "ok"
            except BudgetExceeded:
                return "denied"

        results = await asyncio.gather(attempt("a"), attempt("b"))
        reserved, committed = await engine.store.totals("acme", "default")
        return results, reserved

    results, reserved = asyncio.run(run())
    # Two simultaneous 0.06 reservations against a 0.10 cap: the lock must admit
    # exactly one. (Deleting engine._lock makes this ["ok", "ok"].)
    assert sorted(results) == ["denied", "ok"]
    assert round(reserved, 6) == 0.06  # only one reservation held


def test_concurrency_guard_catches_a_broken_lock():
    """Meta-test: with the serializing lock replaced by a no-op, the yielding
    store lets BOTH boundary reservations through — proving the guard above has
    real teeth (it would fail if the lock regressed)."""
    cfg = MediaBudgetConfig(enabled=True, store="memory", default_cap_usd=0.10)
    engine = BudgetEngine(cfg, _YieldingStore())

    class _AsyncNoLock:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    # Replace the serializing async lock with a no-op.
    engine._lock = _AsyncNoLock()

    async def run():
        async def attempt(op_id):
            try:
                await _reserve(engine, op_id, 0.06)
                return "ok"
            except BudgetExceeded:
                return "denied"

        return await asyncio.gather(attempt("a"), attempt("b"))

    results = asyncio.run(run())
    # No lock + a real yield mid-critical-section → both read stale totals and
    # both are admitted. This is exactly the race the real lock prevents.
    assert results == ["ok", "ok"]


# --- per-provider kill-switch -----------------------------------------------


def test_kill_switch_blocks_one_provider_only():
    engine = _engine(default_cap_usd=10.0, disabled_providers=frozenset({"fal"}))

    async def run():
        with pytest.raises(ProviderDisabled):
            await _reserve(engine, "op-1", 0.05, provider="fal")
        denied = await engine.store.get("op-1")
        # A different provider is unaffected by fal's kill-switch.
        ok = await _reserve(engine, "op-2", 0.05, provider="replicate")
        return denied, ok

    denied, ok = asyncio.run(run())
    assert denied.status == STATUS_DENIED
    assert "kill-switch" in denied.reason
    assert ok.status == STATUS_RESERVED


# --- unknown-cost handling (never silent $0) --------------------------------


def test_unknown_cost_rejected_under_cap_by_default():
    engine = _engine(default_cap_usd=10.0)

    async def run():
        with pytest.raises(UnknownCostRejected):
            await _reserve(engine, "op-1", None)
        return await engine.store.get("op-1")

    denied = asyncio.run(run())
    assert denied.status == STATUS_DENIED
    # Recorded with NULL cost, never a fabricated $0.
    assert denied.estimated_cost_usd is None


def test_unknown_cost_allowed_records_null_not_zero():
    engine = _engine(default_cap_usd=10.0, allow_unknown_cost=True)

    async def run():
        rec = await _reserve(engine, "op-1", None)
        return rec

    rec = asyncio.run(run())
    assert rec.status == STATUS_RESERVED
    assert rec.estimated_cost_usd is None  # not 0.0


def test_unknown_cost_allowed_when_no_cap():
    engine = _engine()  # no cap configured

    async def run():
        return await _reserve(engine, "op-1", None)

    rec = asyncio.run(run())
    assert rec.status == STATUS_RESERVED


def test_reconcile_unknown_final_keeps_estimate():
    engine = _engine(default_cap_usd=10.0)

    async def run():
        await _reserve(engine, "op-1", 0.05)
        await engine.reconcile(operation_id="op-1", status="succeeded", final_cost_usd=None)
        rec = await engine.store.get("op-1")
        summary = await engine.spend(consumer="acme")
        return rec, summary

    rec, summary = asyncio.run(run())
    assert rec.status == STATUS_COMMITTED
    assert rec.final_cost_usd is None
    # Effective spend keeps the estimate — never silently $0.
    assert rec.effective_cost() == 0.05
    assert summary["committed_usd"] == 0.05


# --- release frees the reservation ------------------------------------------


def test_release_frees_budget():
    engine = _engine(default_cap_usd=0.10)

    async def run():
        await _reserve(engine, "op-1", 0.08)
        await engine.release("op-1")
        # Reservation freed → a second 0.08 now fits.
        second = await _reserve(engine, "op-2", 0.08)
        return await engine.store.get("op-1"), second

    released, second = asyncio.run(run())
    assert released.status == STATUS_RELEASED
    assert second.status == STATUS_RESERVED


def test_reconcile_failed_releases_not_commits():
    engine = _engine(default_cap_usd=10.0)

    async def run():
        await _reserve(engine, "op-1", 0.05)
        await engine.reconcile(operation_id="op-1", status="failed")
        rec = await engine.store.get("op-1")
        summary = await engine.spend(consumer="acme")
        return rec, summary

    rec, summary = asyncio.run(run())
    assert rec.status == STATUS_RELEASED
    assert summary["committed_usd"] == 0.0


# --- scoped reads: no cross-consumer leakage --------------------------------


def test_scoped_read_isolates_consumers():
    engine = _engine(default_cap_usd=10.0)

    async def run():
        await _reserve(engine, "a1", 0.05, consumer="alpha")
        await _reserve(engine, "b1", 0.07, consumer="beta")
        alpha = await engine.spend(consumer="alpha")
        beta = await engine.spend(consumer="beta")
        return alpha, beta

    alpha, beta = asyncio.run(run())
    assert {r["consumer"] for r in alpha["records"]} == {"alpha"}
    assert {r["consumer"] for r in beta["records"]} == {"beta"}
    assert len(alpha["records"]) == 1


def test_spend_all_projects_reports_no_single_cap():
    # Budgets are enforced per (consumer, project). An all-projects aggregate
    # read must NOT report a single-project cap as the remaining budget.
    engine = _engine(default_cap_usd=10.0)

    async def run():
        await _reserve(engine, "a", 0.05, consumer="acme", project="p1")
        await _reserve(engine, "b", 0.07, consumer="acme", project="p2")
        aggregate = await engine.spend(consumer="acme")  # project omitted
        scoped = await engine.spend(consumer="acme", project="p1")
        return aggregate, scoped

    aggregate, scoped = asyncio.run(run())
    assert aggregate["cap_usd"] is None
    assert aggregate["remaining_usd"] is None
    assert round(aggregate["reserved_usd"], 6) == 0.12  # both projects summed
    # A concrete scope still reports its cap/remaining.
    assert scoped["cap_usd"] == 10.0
    assert round(scoped["remaining_usd"], 6) == 9.95


def test_public_record_carries_no_secret_fields():
    engine = _engine(default_cap_usd=10.0)

    async def run():
        await _reserve(engine, "op-1", 0.05)
        return await engine.spend(consumer="acme")

    summary = asyncio.run(run())
    record = summary["records"][0]
    blob = str(record).lower()
    assert "api_key" not in blob and "fal_key" not in blob and "secret" not in blob


# --- immutability of attribution + retention -------------------------------


def test_reconcile_does_not_mutate_attribution():
    engine = _engine(default_cap_usd=10.0)

    async def run():
        await _reserve(engine, "op-1", 0.05, consumer="acme", project="p1")
        before = await engine.store.get("op-1")
        await engine.reconcile(operation_id="op-1", status="succeeded", final_cost_usd=0.05)
        after = await engine.store.get("op-1")
        return before, after

    before, after = asyncio.run(run())
    assert after.consumer == before.consumer
    assert after.project == before.project
    assert after.estimated_cost_usd == before.estimated_cost_usd
    assert after.pricing_source_ts == before.pricing_source_ts
    assert after.created_at == before.created_at


def test_retention_prune_drops_old_rows():
    engine = _engine(default_cap_usd=10.0, retention_days=7)

    async def run():
        await _reserve(engine, "fresh", 0.01)
        # Backdate one record beyond the retention window.
        old = await engine.store.get("fresh")
        from dataclasses import replace

        await engine.store.update(
            replace(old, operation_id="stale", created_at=_utcnow() - timedelta(days=30))
        )
        pruned = await engine.prune_expired()
        remaining = await engine.spend(consumer="acme")
        return pruned, remaining

    pruned, remaining = asyncio.run(run())
    assert pruned == 1
    assert {r["operation_id"] for r in remaining["records"]} == {"fresh"}
