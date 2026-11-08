"""Supavisor applies a changed pool size and manager password on restart (#1367).

Docker-gated, like the role tests whose disposable Postgres and Supavisor
helpers it reuses.
"""

from __future__ import annotations

import time
import uuid

from tests.test_database_role_boundaries import (  # noqa: F401  (pytest fixture)
    DisposablePostgres,
    TEST_SECRETS,
    disposable_postgres,
)
from tests.test_database_role_review_regressions import (
    _pooled_login,
    _supavisor_container,
    _wait_for_supavisor,
)


def _tenant_row(db: DisposablePostgres, expected: str) -> str:
    """The tenant row once it matches, or the last value after 30 s.

    pooler.exs starts the whole application, HTTP endpoint included, before
    it writes the tenant, so /api/health can answer first."""
    query = (
        "SELECT t.default_pool_size || '|' || u.pool_size FROM _supavisor.tenants t "
        "JOIN _supavisor.users u ON u.tenant_external_id = t.external_id "
        "WHERE t.external_id = 'atlas'"
    )
    deadline = time.monotonic() + 30
    row = db.sql(query, database="supavisor").stdout.strip()
    while row != expected and time.monotonic() < deadline:
        time.sleep(0.5)
        row = db.sql(query, database="supavisor").stdout.strip()
    return row


def test_supavisor_restart_applies_a_changed_pool_size_and_manager_password(
    disposable_postgres: DisposablePostgres,
) -> None:
    """pooler.exs created the tenant only when absent, so a rotated manager
    password or a new pool size never reached it and pooled logins failed
    after a password rotation (#1367). Two boots on the same database."""
    db = disposable_postgres
    original = TEST_SECRETS["SUPAVISOR_DB_ADMIN_PASSWORD"]
    rotated = f"rotated-{uuid.uuid4().hex}"
    try:
        with _supavisor_container(db.network) as container:
            _wait_for_supavisor(container)
            assert _tenant_row(db, "5|5") == "5|5"
        db.sql(f"ALTER ROLE atlas_supavisor PASSWORD '{rotated}'")
        with _supavisor_container(db.network, pool_size=7, password=rotated) as container:
            _wait_for_supavisor(container)
            assert _tenant_row(db, "7|7") == "7|7"
            login = _pooled_login(
                network=db.network, container=container,
                role="atlas_backend", password=TEST_SECRETS["BACKEND_DB_PASSWORD"],
            )
            assert login.returncode == 0, login.stderr
    finally:
        db.sql(f"ALTER ROLE atlas_supavisor PASSWORD '{original}'")
        with _supavisor_container(db.network) as container:
            _wait_for_supavisor(container)
