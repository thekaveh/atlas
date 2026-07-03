from __future__ import annotations

from urllib.parse import urlparse

import asyncpg


def _uses_transaction_pooler(database_url: str) -> bool:
    """Return true for Atlas/Supabase transaction-pooler URLs."""
    parsed = urlparse(database_url)
    if parsed.hostname == "supavisor":
        return True
    return parsed.port == 6543


async def connect_postgres(
    database_url: str,
    *,
    timeout: int = 10,
    command_timeout: int = 30,
):
    """Create an asyncpg connection compatible with direct and pooled URLs."""
    kwargs = {
        "timeout": timeout,
        "command_timeout": command_timeout,
    }
    if _uses_transaction_pooler(database_url):
        kwargs["statement_cache_size"] = 0
    return await asyncpg.connect(database_url, **kwargs)
