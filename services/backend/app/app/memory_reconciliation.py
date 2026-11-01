"""PostgreSQL scans, canonical-state maintenance and failure classifiers for
memory intents.

The service owns the cache, scan tokens and pass lock. These helpers use its
caller-owned connection; they never acquire a pool slot or contact a vector
provider. Cursor advancement remains with the service after each handled row.
The pure classifiers below decide whether a provider failure was caused by one
row's content; the store's backfill and the service's writes share them.
"""

import re
from itertools import islice

import httpx


async def _valid_rejected_vector_versions(conn, sample):
    """Return still-pending exact versions from one bounded cache sample."""
    rows = await conn.fetch(
        """
        SELECT f.id FROM public.memory_facts AS f
        JOIN unnest($1::uuid[], $2::timestamptz[]) AS cached(id, ts)
          ON cached.id = f.id
         AND cached.ts IS NOT DISTINCT FROM f.updated_at
        WHERE f.vector_sync_pending = true
        """,
        [key for key, _ in sample], [stamp for _, stamp in sample],
    )
    return {row["id"] for row in rows}


async def _fetch_pending_vector_page(conn, rejected, cursor, upper):
    """Read one bounded page inside a fixed, null-safe scan interval."""
    if upper is None:
        rows = await conn.fetch(
            """
            SELECT updated_at, id FROM public.memory_facts
            WHERE vector_sync_pending = true
            ORDER BY updated_at DESC NULLS LAST, id DESC LIMIT 1
            """
        )
        if not rows:
            return [], None
        upper = (rows[0]["updated_at"], rows[0]["id"])
    rows = await conn.fetch(
        """
        SELECT id, user_id, namespace, content, fact_type, confidence,
               is_active, weaviate_id, updated_at
        FROM public.memory_facts AS f
        WHERE vector_sync_pending = true
          AND NOT EXISTS (
              SELECT 1 FROM unnest($1::uuid[], $2::timestamptz[]) AS r(id, ts)
              WHERE r.id = f.id AND r.ts IS NOT DISTINCT FROM f.updated_at
          )
          AND ($4::uuid IS NULL OR
              (f.updated_at IS NOT NULL, COALESCE(f.updated_at, 'epoch'::timestamptz), f.id)
              > ($3::timestamptz IS NOT NULL, COALESCE($3::timestamptz, 'epoch'::timestamptz), $4::uuid))
          AND (f.updated_at IS NOT NULL, COALESCE(f.updated_at, 'epoch'::timestamptz), f.id)
              <= ($5::timestamptz IS NOT NULL, COALESCE($5::timestamptz, 'epoch'::timestamptz), $6::uuid)
        ORDER BY f.updated_at ASC NULLS FIRST, f.id ASC
        LIMIT 100
        """,
        list(rejected), list(rejected.values()), *cursor, *upper,
    )
    return rows, upper


async def _refresh_rejected_vector_rows(conn, rejected) -> None:
    """Validate and rotate at most 100 cached versions in one query."""
    sample = list(islice(rejected.items(), 100))
    if not sample:
        return
    valid = await _valid_rejected_vector_versions(conn, sample)
    # Apply only after validation succeeds, and do not erase a replacement
    # version. Surviving entries rotate so later stale entries get a turn.
    for key, stamp in sample:
        if key not in rejected or rejected[key] != stamp:
            continue
        del rejected[key]
        if key in valid:
            rejected[key] = stamp


async def _clear_active_vector_intent(conn, row, new_weaviate_id) -> None:
    """Capture the current version only while it owns the vector just written."""
    await conn.execute(
        """
        WITH vector_owner AS MATERIALIZED (
            SELECT updated_at FROM public.memory_facts
            WHERE id = $1 AND vector_sync_pending = true
              AND user_id = $3
              AND namespace IS NOT DISTINCT FROM $4
              AND content IS NOT DISTINCT FROM $5
              AND fact_type IS NOT DISTINCT FROM $6
              AND confidence IS NOT DISTINCT FROM $7
              AND is_active = true
        )
        UPDATE public.memory_facts AS fact
        SET vector_sync_pending = false,
            weaviate_id = COALESCE($2, fact.weaviate_id),
            updated_at = now()
        FROM vector_owner
        WHERE fact.id = $1 AND fact.vector_sync_pending = true
          AND fact.updated_at = vector_owner.updated_at
        """,
        row["id"], new_weaviate_id, row["user_id"], row["namespace"],
        row["content"], row["fact_type"], row["confidence"],
    )


async def _clear_pending_vector_intent(conn, row, new_weaviate_id) -> None:
    """Clear only the canonical vector state this reconciliation owns.

    Active writes advance updated_at through the embedding UPDATE trigger.
    Capture the resulting version while its vector-bearing fields still
    match, then guard the clear against any later canonical edit. Retired
    facts and legacy NULL versions keep their original timestamp guard.
    """
    if row["is_active"] and row["updated_at"] is not None:
        await _clear_active_vector_intent(conn, row, new_weaviate_id)
        return
    await conn.execute(
        """
        UPDATE public.memory_facts
        SET vector_sync_pending = false,
            weaviate_id = COALESCE($2, weaviate_id),
            updated_at = now()
        WHERE id = $1 AND vector_sync_pending = true
          AND updated_at = $3
        """,
        row["id"],
        new_weaviate_id,
        row["updated_at"],
    )


class WeaviateQueryError(RuntimeError):
    """Weaviate answered, but the query failed (GraphQL ``errors``)."""


def _weaviate_vectorizer_failure(exc: BaseException) -> bool:
    """Weaviate is reachable but its embedding call (via LiteLLM) failed.

    Weaviate reports a vectorizer error as a 5xx on writes and as GraphQL
    ``errors`` on nearText queries. Neither means Weaviate is down, so they
    must not latch pgvector (which then holds until restart or a probe).
    """
    if isinstance(exc, WeaviateQueryError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        response = getattr(exc, "response", None)
        if response is None or response.status_code < 500:
            return False
        try:
            body = response.text.lower()
        except Exception:  # noqa: BLE001 - unreadable body: not classifiable
            return False
        return "vectoriz" in body or "update vector" in body
    return False


# Weaviate wraps the embedding provider's status in its error text ("failed
# with status: 400 ..."). A 4xx rejects that one fact (too long, filtered),
# except auth/timeouts/rate limits and model-configuration errors, which hit
# every row; anything else (5xx, connection, DNS) is systematic too.
_VECTORIZER_ROW_REJECTION = re.compile(r"status:?\s*4(?!01|03|04|08|29)\d\d\b")
_VECTORIZER_SYSTEMATIC_TEXT = re.compile(
    r"invalid model|model not found|no such model|authentication|api key|budget", re.IGNORECASE
)


def _weaviate_vectorizer_row_rejection(exc: BaseException) -> bool:
    """A vectorizer failure caused by this row's content, not the provider."""
    if not _weaviate_vectorizer_failure(exc) or isinstance(exc, WeaviateQueryError):
        return False
    try:
        body = exc.response.text
    except Exception:  # noqa: BLE001 - unreadable body: not classifiable
        return False
    return bool(_VECTORIZER_ROW_REJECTION.search(body)) and not _VECTORIZER_SYSTEMATIC_TEXT.search(body)


def _is_row_rejection(exc: BaseException) -> bool:
    """A failure caused by this row's content: a 4xx from the embedder or the
    target that is not auth, missing model, timeout or back-pressure. Database
    restarts, a not-yet-registered model or a superseded write hit every row
    and recover by themselves, so remembering them hid rows until a restart
    (2026-10-08 run, cycle 24)."""
    if _weaviate_vectorizer_failure(exc):
        return _weaviate_vectorizer_row_rejection(exc)
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None or not 400 <= status < 500 or status in {401, 403, 404, 408, 429}:
        return False
    try:
        body = response.text
    except Exception:  # noqa: BLE001 - unreadable body: not classifiable
        return False
    return not _VECTORIZER_SYSTEMATIC_TEXT.search(body or "")
