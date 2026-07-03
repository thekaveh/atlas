import asyncio
from unittest.mock import AsyncMock, patch


def _run(coro):
    return asyncio.run(coro)


def test_connect_postgres_keeps_asyncpg_defaults_for_direct_database_url():
    from db_connection import connect_postgres

    with patch("asyncpg.connect", new_callable=AsyncMock) as mock_connect:
        _run(connect_postgres("postgresql://user:pw@supabase-db:5432/postgres"))

    mock_connect.assert_awaited_once_with(
        "postgresql://user:pw@supabase-db:5432/postgres",
        timeout=10,
        command_timeout=30,
    )


def test_connect_postgres_disables_statement_cache_for_supavisor_transaction_url():
    from db_connection import connect_postgres

    with patch("asyncpg.connect", new_callable=AsyncMock) as mock_connect:
        _run(connect_postgres("postgresql://user.atlas:pw@supavisor:6543/postgres"))

    mock_connect.assert_awaited_once_with(
        "postgresql://user.atlas:pw@supavisor:6543/postgres",
        timeout=10,
        command_timeout=30,
        statement_cache_size=0,
    )


def test_connect_postgres_disables_statement_cache_for_any_6543_pooler_url():
    from db_connection import connect_postgres

    with patch("asyncpg.connect", new_callable=AsyncMock) as mock_connect:
        _run(connect_postgres("postgresql://user:pw@pooler.example:6543/postgres"))

    assert mock_connect.await_args.kwargs["statement_cache_size"] == 0
