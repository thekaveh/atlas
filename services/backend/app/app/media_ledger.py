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

The capability is **disabled by default** (`MEDIA_BUDGET_ENABLED=false`); when
disabled every method is a no-op and the gateway behaves exactly as before.

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

# Statuses that still hold budget (a live reservation).
_RESERVED_STATES = frozenset({STATUS_RESERVED, STATUS_SUBMITTED})


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


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
                for key, value in (parsed or {}).items():
                    caps[str(key)] = float(value)
            except (ValueError, TypeError):
                # Malformed cap config must not crash startup; treated as no
                # per-consumer overrides (default cap still applies).
                caps = {}
        disabled = {
            p.strip().lower()
            for p in (os.getenv("MEDIA_DISABLED_PROVIDERS") or "").split(",")
            if p.strip()
        }
        return cls(
            enabled=_env_bool("MEDIA_BUDGET_ENABLED", False),
            store=(os.getenv("MEDIA_BUDGET_STORE") or "postgres").strip().lower(),
            currency=(os.getenv("MEDIA_BUDGET_CURRENCY") or "USD").strip() or "USD",
            default_cap_usd=_optional_float(os.getenv("MEDIA_BUDGET_DEFAULT_USD")),
            consumer_caps=caps,
            disabled_providers=frozenset(disabled),
            allow_unknown_cost=_env_bool("MEDIA_BUDGET_ALLOW_UNKNOWN_COST", False),
            retention_days=_optional_int(os.getenv("MEDIA_BUDGET_RETENTION_DAYS")),
            database_url=os.getenv("DATABASE_URL"),
        )


def _optional_float(raw: Optional[str]) -> Optional[float]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _optional_int(raw: Optional[str]) -> Optional[int]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class InMemoryLedgerStore:
    """Process-local reference store. Append-only per operation id.

    Used for tests and whenever a durable store is not configured. Not shared
    across workers — the PostgresLedgerStore is the durable, cross-worker path.
    """

    def __init__(self) -> None:
        self._records: Dict[str, LedgerRecord] = {}

    async def append(self, record: LedgerRecord) -> None:
        self._records[record.operation_id] = record

    async def get(self, operation_id: str) -> Optional[LedgerRecord]:
        return self._records.get(operation_id)

    async def update(self, record: LedgerRecord) -> None:
        self._records[record.operation_id] = record

    async def rekey(self, old_id: str, new_id: str) -> None:
        record = self._records.pop(old_id, None)
        if record is None:
            return
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

    async def attach_operation(self, reservation_id: str, operation_id: str) -> None:
        """Re-key a reservation to the provider's operation id + mark submitted.

        The reservation is created before provider invocation (so budgets stop
        over-limit requests early) under a temporary id; once the provider
        returns its operation id the record is re-keyed to it so poll-time
        reconciliation can find it.
        """
        if not self.config.enabled:
            return
        record = await self.store.get(reservation_id)
        if record is None:
            return
        await self.store.rekey(reservation_id, operation_id)
        new_status = (
            STATUS_SUBMITTED if record.status == STATUS_RESERVED else record.status
        )
        await self.store.update(
            replace(
                record,
                operation_id=operation_id,
                status=new_status,
                updated_at=_utcnow(),
            )
        )

    async def release(self, operation_id: str) -> None:
        """Release a still-held reservation (e.g. provider submission failed)."""
        if not self.config.enabled:
            return
        record = await self.store.get(operation_id)
        if record is None or record.status not in _RESERVED_STATES:
            return
        await self.store.update(
            replace(record, status=STATUS_RELEASED, updated_at=_utcnow())
        )

    async def reconcile(
        self,
        *,
        operation_id: str,
        status: str,
        final_cost_usd: Optional[float] = None,
        artifact_refs: Tuple[str, ...] = (),
    ) -> None:
        """Reconcile a terminal operation.

        `status='succeeded'` commits the spend (final cost if the provider
        reported one, else the estimate — never silently $0). Any other terminal
        status (failed / cancelled / timeout) releases the reservation.
        """
        if not self.config.enabled:
            return
        record = await self.store.get(operation_id)
        if record is None or record.status in (
            STATUS_COMMITTED,
            STATUS_RELEASED,
            STATUS_DENIED,
        ):
            return

        if status == "succeeded":
            new_status = STATUS_COMMITTED
        else:
            new_status = STATUS_RELEASED

        await self.store.update(
            replace(
                record,
                status=new_status,
                # Keep the estimate when the provider reports no final cost, so
                # an unknown cost is never persisted as $0.
                final_cost_usd=(
                    final_cost_usd if final_cost_usd is not None else record.final_cost_usd
                ),
                artifact_refs=tuple(artifact_refs) or record.artifact_refs,
                updated_at=_utcnow(),
            )
        )

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
        if not self.config.enabled or not self.config.retention_days:
            return 0
        from datetime import timedelta

        cutoff = _utcnow() - timedelta(days=self.config.retention_days)
        return await self.store.prune(cutoff)


def build_store(config: MediaBudgetConfig) -> Any:
    """Select the ledger store from config.

    Defaults to durable Postgres when enabled with a database url; falls back to
    the in-memory reference store otherwise (and for `MEDIA_BUDGET_STORE=memory`).
    """
    if config.store == "postgres" and config.database_url:
        # Imported lazily so the in-memory / disabled paths never touch asyncpg.
        from media_ledger_postgres import PostgresLedgerStore

        return PostgresLedgerStore(config.database_url)
    return InMemoryLedgerStore()


def build_engine(config: Optional[MediaBudgetConfig] = None) -> BudgetEngine:
    cfg = config or MediaBudgetConfig.from_env()
    return BudgetEngine(cfg, build_store(cfg))
