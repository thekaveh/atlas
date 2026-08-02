"""Media-gateway spend ledger + budget engine (#342).

LiteLLM gives Atlas spend logs + budgets for text. Hosted media generation had
none. This module attaches cost accounting to the media gateway's operation
lifecycle:

* an append-only-per-operation **ledger** capturing
  `{consumer, project, provider, model, modality, estimated + final cost,
  currency, pricing-source timestamp, artifact refs, status}`;
* a **budget engine** that reserves estimated cost *before* provider submission
  (hard-stop over-limit), reconciles the final cost on completion (never
  silently $0 for unknown-cost models), and enforces a per-provider
  **kill-switch** independently of gateway availability;
* concurrency-safe reserve/release semantics so two simultaneous submissions at
  the remaining-budget boundary cannot both pass.

Budget enforcement is **disabled by default** (`MEDIA_BUDGET_ENABLED=false`).
Ordinary operations remain accounting no-ops in that mode, while an ambiguous
provider submission still records the minimal recovery row needed for an
operator disposition.

Two stores share one interface: `InMemoryLedgerStore` (the tested reference,
also used when no durable store is configured) and `PostgresLedgerStore`
(asyncpg, durable; schema in `services/supabase/db/scripts/17-backend-media-ledger.sql`).
Neither this module nor `main.py`'s import closure pulls a live DB — asyncpg is
imported lazily and the store is selected by config, so the backend CI unit-test
venv stays satisfied.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Ledger status lifecycle.
STATUS_RESERVED = "reserved"
STATUS_SUBMITTED = "submitted"
STATUS_COMMITTED = "committed"
STATUS_RELEASED = "released"
STATUS_DENIED = "denied"
AMBIGUOUS_RECOVERY_REASON = (
    "ambiguous provider submission; manual reconciliation required"
)
ATTACH_RECOVERY_REASON = "ledger attachment pending automatic recovery"

# Statuses that still hold budget (a live reservation).
_RESERVED_STATES = frozenset({STATUS_RESERVED, STATUS_SUBMITTED})


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError(
        f"{name} must be one of true/false, yes/no, on/off, 1/0, or enabled/disabled"
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BudgetError(Exception):
    """Base class for policy denials (all map to a 4xx at the route)."""


class BudgetExceeded(BudgetError):
    """The reservation would exceed the consumer/project cap."""


class ProviderDisabled(BudgetError):
    """The provider is kill-switched off."""


class UnknownCostRejected(BudgetError):
    """Budgets are enforced but the model has no known cost (never treat as $0)."""


class LedgerOperationCollisionError(RuntimeError):
    """A provider operation id already belongs to another ledger record."""


@dataclass(frozen=True)
class LedgerRecord:
    """One media operation's spend record.

    The attribution + pricing fields (consumer, project, provider, model,
    modality, estimated_cost_usd, currency, pricing_source_ts, created_at) are
    set at reservation and never mutated; only status / final_cost_usd /
    artifact_refs / reason are reconciled, via `replace()` into a new frozen
    instance.
    """

    operation_id: str
    consumer: str
    project: str
    provider: str
    model: str
    modality: str
    status: str
    currency: str = "USD"
    model_version: Optional[str] = None
    estimated_cost_usd: Optional[float] = None
    final_cost_usd: Optional[float] = None
    pricing_source_ts: Optional[datetime] = None
    artifact_refs: Tuple[str, ...] = ()
    reason: Optional[str] = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def effective_cost(self) -> float:
        """Cost that counts toward spend: final if known, else estimated, else 0.

        Only ever 0 when BOTH are genuinely unknown — the caller records the
        None values verbatim, so an unknown cost is never *persisted* as $0.
        """
        if self.final_cost_usd is not None:
            return float(self.final_cost_usd)
        if self.estimated_cost_usd is not None:
            return float(self.estimated_cost_usd)
        return 0.0

    def to_public_dict(self) -> Dict[str, Any]:
        """Serializable view for the scoped read API — carries no secrets."""
        return {
            "operation_id": self.operation_id,
            "consumer": self.consumer,
            "project": self.project,
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "modality": self.modality,
            "status": self.status,
            "currency": self.currency,
            "estimated_cost_usd": self.estimated_cost_usd,
            "final_cost_usd": self.final_cost_usd,
            "pricing_source_ts": (
                self.pricing_source_ts.isoformat() if self.pricing_source_ts else None
            ),
            "artifact_refs": list(self.artifact_refs),
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class MediaBudgetConfig:
    """Budget engine configuration, disabled by default."""

    enabled: bool = False
    store: str = "postgres"  # "postgres" (durable) | "memory"
    currency: str = "USD"
    default_cap_usd: Optional[float] = None
    consumer_caps: Dict[str, float] = field(default_factory=dict)
    disabled_providers: frozenset = frozenset()
    allow_unknown_cost: bool = False
    retention_days: Optional[int] = None
    database_url: Optional[str] = None

    @classmethod
    def from_env(cls) -> "MediaBudgetConfig":
        caps: Dict[str, float] = {}
        raw_caps = (os.getenv("MEDIA_BUDGET_CONSUMER_CAPS") or "").strip()
        if raw_caps:
            try:
                parsed = json.loads(raw_caps)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "MEDIA_BUDGET_CONSUMER_CAPS must be a JSON object of "
                    "non-negative finite USD caps"
                ) from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    "MEDIA_BUDGET_CONSUMER_CAPS must be a JSON object of "
                    "non-negative finite USD caps"
                )
            for key, value in parsed.items():
                scope = str(key).strip()
                if not scope:
                    raise ValueError(
                        "MEDIA_BUDGET_CONSUMER_CAPS keys must be non-empty scopes"
                    )
                caps[scope] = _nonnegative_float(
                    "MEDIA_BUDGET_CONSUMER_CAPS", value
                )
        disabled = {
            p.strip().lower()
            for p in (os.getenv("MEDIA_DISABLED_PROVIDERS") or "").split(",")
            if p.strip()
        }
        enabled = _env_bool("MEDIA_BUDGET_ENABLED", False)
        store = (os.getenv("MEDIA_BUDGET_STORE") or "postgres").strip().lower()
        if store not in {"postgres", "memory"}:
            raise ValueError("MEDIA_BUDGET_STORE must be 'postgres' or 'memory'")
        database_url = (os.getenv("DATABASE_URL") or "").strip() or None
        if store == "postgres" and not database_url:
            raise ValueError(
                "DATABASE_URL is required when MEDIA_BUDGET_STORE=postgres "
                "for ambiguous-submission recovery"
            )
        return cls(
            enabled=enabled,
            store=store,
            currency=(os.getenv("MEDIA_BUDGET_CURRENCY") or "USD").strip() or "USD",
            default_cap_usd=_optional_nonnegative_float(
                "MEDIA_BUDGET_DEFAULT_USD", os.getenv("MEDIA_BUDGET_DEFAULT_USD")
            ),
            consumer_caps=caps,
            disabled_providers=frozenset(disabled),
            allow_unknown_cost=_env_bool("MEDIA_BUDGET_ALLOW_UNKNOWN_COST", False),
            retention_days=_optional_positive_int(
                "MEDIA_BUDGET_RETENTION_DAYS",
                os.getenv("MEDIA_BUDGET_RETENTION_DAYS"),
            ),
            database_url=database_url,
        )


def _nonnegative_float(name: str, raw: Any) -> float:
    if isinstance(raw, bool):
        raise ValueError(f"{name} values must be non-negative finite numbers")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} values must be non-negative finite numbers") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} values must be non-negative finite numbers")
    return value


def _optional_nonnegative_float(name: str, raw: Optional[str]) -> Optional[float]:
    raw = (raw or "").strip()
    if not raw:
        return None
    return _nonnegative_float(name, raw)


def _optional_positive_int(name: str, raw: Optional[str]) -> Optional[int]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class InMemoryLedgerStore:
    """Process-local reference store. Append-only per operation id.

    Used for tests and whenever a durable store is not configured. Not shared
    across workers — the PostgresLedgerStore is the durable, cross-worker path.
    """

    def __init__(self) -> None:
        self._records: Dict[str, LedgerRecord] = {}
        self._lock = asyncio.Lock()

    async def append(self, record: LedgerRecord) -> None:
        async with self._lock:
            if record.operation_id in self._records:
                raise LedgerOperationCollisionError(
                    f"media ledger operation id collision: {record.operation_id}"
                )
            self._records[record.operation_id] = record

    async def get(self, operation_id: str) -> Optional[LedgerRecord]:
        return self._records.get(operation_id)

    async def update(self, record: LedgerRecord) -> None:
        self._records[record.operation_id] = record

    async def attach_if_reserved(
        self, old_id: str, new_id: str
    ) -> Optional[LedgerRecord]:
        async with self._lock:
            record = self._records.get(old_id)
            if record is None or record.status not in _RESERVED_STATES:
                return self._records.get(new_id)
            if old_id != new_id and new_id in self._records:
                raise LedgerOperationCollisionError(
                    f"media ledger operation id collision: {new_id}"
                )
            if old_id != new_id:
                del self._records[old_id]
            attached = replace(
                record,
                operation_id=new_id,
                status=STATUS_SUBMITTED,
                updated_at=_utcnow(),
            )
            self._records[new_id] = attached
            return attached

    async def protect_reason_if_reserved(
        self, operation_id: str, reason: str
    ) -> bool:
        async with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.status not in _RESERVED_STATES:
                return False
            self._records[operation_id] = replace(
                record, reason=reason, updated_at=_utcnow()
            )
            return True

    async def clear_attach_protection(self, operation_id: str) -> None:
        async with self._lock:
            record = self._records.get(operation_id)
            if (
                record is not None
                and record.status in _RESERVED_STATES
                and record.reason == ATTACH_RECOVERY_REASON
            ):
                self._records[operation_id] = replace(
                    record, reason=None, updated_at=_utcnow()
                )

    async def settle_if_reserved(
        self, record: LedgerRecord
    ) -> Optional[LedgerRecord]:
        """Atomically install one terminal disposition and return the winner."""
        async with self._lock:
            current = self._records.get(record.operation_id)
            if current is None:
                return None
            if current.status in _RESERVED_STATES:
                self._records[record.operation_id] = record
                return record
            return current

    async def rekey(self, old_id: str, new_id: str) -> None:
        async with self._lock:
            record = self._records.get(old_id)
            if record is None:
                return
            if new_id in self._records:
                raise LedgerOperationCollisionError(
                    f"media ledger operation id collision: {new_id}"
                )
            del self._records[old_id]
            self._records[new_id] = replace(record, operation_id=new_id)

    async def totals(self, consumer: str, project: str) -> Tuple[float, float]:
        reserved = 0.0
        committed = 0.0
        for rec in self._records.values():
            if rec.consumer != consumer or rec.project != project:
                continue
            if rec.status in _RESERVED_STATES:
                reserved += rec.effective_cost()
            elif rec.status == STATUS_COMMITTED:
                committed += rec.effective_cost()
        return reserved, committed

    async def list(
        self,
        *,
        consumer: str,
        project: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[LedgerRecord]:
        out: List[LedgerRecord] = []
        for rec in self._records.values():
            if rec.consumer != consumer:
                continue
            if project is not None and rec.project != project:
                continue
            if since is not None and rec.created_at < since:
                continue
            if until is not None and rec.created_at > until:
                continue
            out.append(rec)
        out.sort(key=lambda r: r.created_at)
        return out

    async def prune(self, older_than: datetime) -> int:
        stale = [
            oid
            for oid, rec in self._records.items()
            if rec.created_at < older_than
            and not (
                rec.status in _RESERVED_STATES
                and rec.reason
                in {AMBIGUOUS_RECOVERY_REASON, ATTACH_RECOVERY_REASON}
            )
        ]
        for oid in stale:
            del self._records[oid]
        return len(stale)


class BudgetEngine:
    """Budget reservation / reconciliation + kill-switch over a ledger store."""

    def __init__(
        self,
        config: MediaBudgetConfig,
        store: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.store = store if store is not None else InMemoryLedgerStore()
        # Serializes the check-then-append critical section within this process.
        # Cross-worker atomicity is the PostgresLedgerStore's responsibility.
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def provider_enabled(self, provider: str) -> bool:
        return (provider or "").strip().lower() not in self.config.disabled_providers

    def _cap_for(self, consumer: str, project: str) -> Optional[float]:
        caps = self.config.consumer_caps
        for key in (f"{consumer}:{project}", consumer):
            if key in caps:
                return caps[key]
        return self.config.default_cap_usd

    async def _record_denial(
        self,
        *,
        operation_id: str,
        consumer: str,
        project: str,
        provider: str,
        model: str,
        modality: str,
        estimated_cost_usd: Optional[float],
        pricing_source_ts: Optional[datetime],
        model_version: Optional[str],
        reason: str,
    ) -> None:
        now = _utcnow()
        await self.store.append(
            LedgerRecord(
                operation_id=operation_id,
                consumer=consumer,
                project=project,
                provider=provider,
                model=model,
                model_version=model_version,
                modality=modality,
                status=STATUS_DENIED,
                currency=self.config.currency,
                estimated_cost_usd=estimated_cost_usd,
                pricing_source_ts=pricing_source_ts,
                reason=reason,
                created_at=now,
                updated_at=now,
            )
        )

    async def reserve(
        self,
        *,
        operation_id: str,
        consumer: str,
        project: str,
        provider: str,
        model: str,
        modality: str,
        estimated_cost_usd: Optional[float],
        pricing_source_ts: Optional[datetime] = None,
        model_version: Optional[str] = None,
    ) -> Optional[LedgerRecord]:
        """Reserve budget before provider submission.

        Returns the reserved ledger record, or None when budgets are disabled.
        Raises ProviderDisabled / UnknownCostRejected / BudgetExceeded (each
        also recorded as a denial) when policy blocks the request.
        """
        if not self.config.enabled:
            return None

        # Kill-switch is enforced independently of budget math and gateway
        # availability — one disabled provider must not down the others.
        if not self.provider_enabled(provider):
            reason = f"provider '{provider}' is disabled (kill-switch)"
            await self._record_denial(
                operation_id=operation_id,
                consumer=consumer,
                project=project,
                provider=provider,
                model=model,
                modality=modality,
                estimated_cost_usd=estimated_cost_usd,
                pricing_source_ts=pricing_source_ts,
                model_version=model_version,
                reason=reason,
            )
            raise ProviderDisabled(reason)

        cap = self._cap_for(consumer, project)

        if estimated_cost_usd is None and cap is not None and not self.config.allow_unknown_cost:
            reason = (
                f"model '{model}' has no known cost; refusing to bill an "
                "unknown amount against a budget (set "
                "MEDIA_BUDGET_ALLOW_UNKNOWN_COST=true to override)"
            )
            await self._record_denial(
                operation_id=operation_id,
                consumer=consumer,
                project=project,
                provider=provider,
                model=model,
                modality=modality,
                estimated_cost_usd=None,
                pricing_source_ts=pricing_source_ts,
                model_version=model_version,
                reason=reason,
            )
            raise UnknownCostRejected(reason)

        now = _utcnow()
        record = LedgerRecord(
            operation_id=operation_id,
            consumer=consumer,
            project=project,
            provider=provider,
            model=model,
            model_version=model_version,
            modality=modality,
            status=STATUS_RESERVED,
            currency=self.config.currency,
            estimated_cost_usd=estimated_cost_usd,
            pricing_source_ts=pricing_source_ts,
            # Protect the reservation before any provider side effect. A crash
            # during submission is indistinguishable from provider acceptance.
            reason=ATTACH_RECOVERY_REASON,
            created_at=now,
            updated_at=now,
        )

        admitted = await self._reserve_within_cap(record, cap)
        if not admitted:
            reserved, committed = await self.store.totals(consumer, project)
            remaining = max(0.0, cap - reserved - committed) if cap is not None else 0.0
            reason = (
                f"budget exceeded for {consumer}/{project}: estimated "
                f"${estimated_cost_usd:.4f} > remaining ${remaining:.4f} of "
                f"${cap:.4f} cap"
            )
            await self._record_denial(
                operation_id=operation_id,
                consumer=consumer,
                project=project,
                provider=provider,
                model=model,
                modality=modality,
                estimated_cost_usd=estimated_cost_usd,
                pricing_source_ts=pricing_source_ts,
                model_version=model_version,
                reason=reason,
            )
            raise BudgetExceeded(reason)
        return record

    async def _reserve_within_cap(
        self, record: LedgerRecord, cap: Optional[float]
    ) -> bool:
        """Atomically append `record` iff it fits under `cap`.

        A store may provide its own atomic `reserve_within_cap` (the Postgres
        store uses a transaction + advisory lock for cross-worker safety);
        otherwise the engine serializes the check-then-append with a
        process-local lock, which is what the concurrency-boundary test exercises.
        """
        store_reserve = getattr(self.store, "reserve_within_cap", None)
        if store_reserve is not None:
            return await store_reserve(record, cap)
        async with self._lock:
            if cap is not None and record.estimated_cost_usd is not None:
                reserved, committed = await self.store.totals(
                    record.consumer, record.project
                )
                if reserved + committed + record.estimated_cost_usd > cap + 1e-9:
                    return False
            await self.store.append(record)
            return True

    async def attach_operation(
        self,
        reservation_id: str,
        operation_id: str,
        *,
        consumer: Optional[str] = None,
        project: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        modality: Optional[str] = None,
        force: bool = False,
    ) -> None:
        """Re-key a reservation to the provider's operation id + mark submitted.

        The reservation is created before provider invocation (so budgets stop
        over-limit requests early) under a temporary id; once the provider
        returns its operation id the record is re-keyed to it so poll-time
        reconciliation can find it.
        """
        if not self.config.enabled and not force:
            return
        record = await self.store.get(reservation_id)
        if record is None:
            # A prior attempt may have completed the re-key but failed before
            # marking SUBMITTED. Retry against the provider id in that case.
            record = await self.store.get(operation_id)
            if record is None:
                return
        expected = (consumer, project, provider, model, modality)
        actual = (
            record.consumer,
            record.project,
            record.provider,
            record.model,
            record.modality,
        )
        if any(value is not None for value in expected) and any(
            wanted is not None and wanted != got
            for wanted, got in zip(expected, actual)
        ):
            raise LedgerOperationCollisionError(
                f"media ledger operation id collision: {operation_id}"
            )
        attached = await self.store.attach_if_reserved(
            record.operation_id, operation_id
        )
        if attached is not None and any(
            wanted is not None and wanted != got
            for wanted, got in zip(
                expected,
                (
                    attached.consumer,
                    attached.project,
                    attached.provider,
                    attached.model,
                    attached.modality,
                ),
            )
        ):
            raise LedgerOperationCollisionError(
                f"media ledger operation id collision: {operation_id}"
            )

    async def release(self, operation_id: str, *, force: bool = False) -> None:
        """Release a still-held reservation (e.g. provider submission failed)."""
        if not self.config.enabled and not force:
            return
        record = await self.store.get(operation_id)
        if record is None or record.status not in _RESERVED_STATES:
            return
        await self.store.settle_if_reserved(
            replace(
                record,
                status=STATUS_RELEASED,
                reason=(
                    None if record.reason == ATTACH_RECOVERY_REASON else record.reason
                ),
                updated_at=_utcnow(),
            )
        )

    async def reconcile(
        self,
        *,
        operation_id: str,
        status: str,
        final_cost_usd: Optional[float] = None,
        artifact_refs: Tuple[str, ...] = (),
        reason: Optional[str] = None,
        force: bool = False,
    ) -> Optional[LedgerRecord]:
        """Reconcile a terminal operation.

        `status='succeeded'` commits the spend (final cost if the provider
        reported one, else the estimate — never silently $0). Any other terminal
        status (failed / cancelled / timeout) releases the reservation.
        """
        if not self.config.enabled and not force:
            return None
        record = await self.store.get(operation_id)
        if record is None or record.status in (
            STATUS_COMMITTED,
            STATUS_RELEASED,
            STATUS_DENIED,
        ):
            return record

        if status == "succeeded":
            new_status = STATUS_COMMITTED
        else:
            new_status = STATUS_RELEASED

        candidate = replace(
            record,
            status=new_status,
            # Keep the estimate when the provider reports no final cost, so
            # an unknown cost is never persisted as $0.
            final_cost_usd=(
                final_cost_usd if final_cost_usd is not None else record.final_cost_usd
            ),
            artifact_refs=tuple(artifact_refs) or record.artifact_refs,
            reason=(
                reason
                if reason is not None
                else None
                if record.reason
                in {ATTACH_RECOVERY_REASON, AMBIGUOUS_RECOVERY_REASON}
                else record.reason
            ),
            updated_at=_utcnow(),
        )
        return await self.store.settle_if_reserved(candidate)

    async def record_ambiguous(
        self,
        *,
        operation_id: str,
        consumer: str,
        project: str,
        provider: str,
        model: str,
        modality: str,
        estimated_cost_usd: Optional[float],
        pricing_source_ts: Optional[datetime],
        model_version: Optional[str],
        allow_existing: bool = True,
    ) -> LedgerRecord:
        """Persist a recovery row even when budget enforcement is disabled."""
        existing = await self.store.get(operation_id)
        if existing is not None:
            if not allow_existing or (
                existing.consumer,
                existing.project,
                existing.provider,
                existing.model,
                existing.modality,
            ) != (consumer, project, provider, model, modality):
                raise LedgerOperationCollisionError(
                    f"media ledger operation id collision: {operation_id}"
                )
            if (
                existing.status in _RESERVED_STATES
                and existing.reason != AMBIGUOUS_RECOVERY_REASON
            ):
                await self.store.protect_reason_if_reserved(
                    operation_id, AMBIGUOUS_RECOVERY_REASON
                )
                refreshed = await self.store.get(operation_id)
                return refreshed if refreshed is not None else existing
            return existing
        now = _utcnow()
        record = LedgerRecord(
            operation_id=operation_id,
            consumer=consumer,
            project=project,
            provider=provider,
            model=model,
            model_version=model_version,
            modality=modality,
            status=STATUS_RESERVED,
            currency=self.config.currency,
            estimated_cost_usd=estimated_cost_usd,
            pricing_source_ts=pricing_source_ts,
            reason=AMBIGUOUS_RECOVERY_REASON,
            created_at=now,
            updated_at=now,
        )
        await self.store.append(record)
        return record

    async def protect_recovery_ids(
        self, operation_ids: Tuple[str, ...]
    ) -> Tuple[str, ...]:
        """Mark whichever candidate rows exist as retention-protected recovery."""
        protected: List[str] = []
        for operation_id in operation_ids:
            record = await self.store.get(operation_id)
            if record is None:
                continue
            await self.store.protect_reason_if_reserved(
                operation_id, AMBIGUOUS_RECOVERY_REASON
            )
            protected.append(operation_id)
        return tuple(protected)

    async def protect_attach_ids(
        self, operation_ids: Tuple[str, ...]
    ) -> Tuple[str, ...]:
        """Mark owned attach candidates without implying manual reconciliation."""
        protected: List[str] = []
        for operation_id in operation_ids:
            record = await self.store.get(operation_id)
            if record is None:
                continue
            await self.store.protect_reason_if_reserved(
                operation_id, ATTACH_RECOVERY_REASON
            )
            protected.append(operation_id)
        return tuple(protected)

    async def clear_attach_protection(self, operation_id: str) -> None:
        await self.store.clear_attach_protection(operation_id)

    async def spend(
        self,
        *,
        consumer: str,
        project: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Scoped spend read for a single consumer (optionally one project).

        Returns aggregate committed / reserved totals plus itemized records —
        carries no provider keys and no other consumer's rows.
        """
        records = await self.store.list(
            consumer=consumer, project=project, since=since, until=until
        )
        committed = sum(
            r.effective_cost() for r in records if r.status == STATUS_COMMITTED
        )
        reserved = sum(
            r.effective_cost() for r in records if r.status in _RESERVED_STATES
        )
        # Budgets are enforced per (consumer, project) scope. When project is
        # omitted this is an all-projects aggregate, which has no single cap —
        # report cap/remaining only for a concrete scope so the number can't
        # contradict what reserve() enforces per project.
        cap = self._cap_for(consumer, project) if project is not None else None
        return {
            "consumer": consumer,
            "project": project,
            "currency": self.config.currency,
            "cap_usd": cap,
            "committed_usd": round(committed, 6),
            "reserved_usd": round(reserved, 6),
            "remaining_usd": (
                round(cap - committed - reserved, 6) if cap is not None else None
            ),
            "records": [r.to_public_dict() for r in records],
        }

    async def prune_expired(self) -> int:
        if not self.config.retention_days:
            return 0
        from datetime import timedelta

        cutoff = _utcnow() - timedelta(days=self.config.retention_days)
        return await self.store.prune(cutoff)


def build_store(config: MediaBudgetConfig) -> Any:
    """Select the ledger store from config.

    Configured Postgres is validated before this factory runs, so a missing
    database URL cannot silently downgrade enforcement or ambiguity recovery to
    process-local memory. Only explicitly memory-backed configurations use the
    reference store.
    """
    if config.store == "postgres" and config.database_url:
        # Imported lazily so the explicitly in-memory path never touches asyncpg.
        from media_ledger_postgres import PostgresLedgerStore

        return PostgresLedgerStore(config.database_url)
    return InMemoryLedgerStore()


def build_engine(config: Optional[MediaBudgetConfig] = None) -> BudgetEngine:
    cfg = config or MediaBudgetConfig.from_env()
    return BudgetEngine(cfg, build_store(cfg))
