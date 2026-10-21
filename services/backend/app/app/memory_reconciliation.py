"""Bounded PostgreSQL scan and cached-version maintenance for memory intents.

The service owns the cache, scan tokens and pass lock. These helpers use its
caller-owned connection; they never acquire a pool slot or contact a vector
provider. Cursor advancement remains with the service after each handled row.
"""

from itertools import islice


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
