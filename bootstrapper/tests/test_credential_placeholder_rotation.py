"""Credential placeholder rotation: `.env.example` ships well-known
defaults (``password``, ``redis_password``, ``neo4j_password``,
``kong_password``, ``admin``, ``your-random-encryption-key``,
``app_password``, ``secret``) that the key-generator must upgrade to a
real value on the first ``./start.sh``. Without these, ``cp .env.example
.env && ./start.sh`` boots the stack with credentials anyone can read
from the public repo.

These tests exercise the bootstrapper end of the rotation: every
``generate_and_update_*`` rotator covered by ``PLACEHOLDER_DEFAULTS``
must replace the placeholder, preserve a hand-supplied real value, and
(for the composite ``GRAPH_DB_AUTH=neo4j/<password>``) keep the derived
form in sync.
"""
from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from utils.key_generator import KeyGenerator


# (env_var, placeholder, rotator_method_name)
ROTATORS = [
    ("N8N_ENCRYPTION_KEY", "your-random-encryption-key", "generate_and_update_n8n_key"),
    ("SUPABASE_DB_PASSWORD", "password", "generate_and_update_supabase_db_password"),
    ("SUPABASE_DB_APP_PASSWORD", "app_password", "generate_and_update_supabase_db_app_password"),
    ("GRAPH_DB_PASSWORD", "neo4j_password", "generate_and_update_graph_db_password"),
    ("REDIS_PASSWORD", "redis_password", "generate_and_update_redis_password"),
    ("DASHBOARD_PASSWORD", "kong_password", "generate_and_update_kong_dashboard_password"),
    ("OPEN_WEB_UI_ADMIN_PASSWORD", "admin", "generate_and_update_webui_admin_password"),
    ("OPEN_WEB_UI_SECRET_KEY", "secret", "generate_and_update_webui_secret_key"),
]


def _seed_env(tmp_path: Path, body: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return env


@pytest.mark.parametrize("var,placeholder,method", ROTATORS)
def test_placeholder_is_rotated(tmp_path, var, placeholder, method):
    _seed_env(tmp_path, f"{var}={placeholder}\n")
    kg = KeyGenerator(str(tmp_path))
    assert getattr(kg, method)() is True
    new_value = kg.get_current_env_value(var)
    assert new_value, f"{var} not written"
    assert new_value != placeholder, (
        f"{var}: placeholder {placeholder!r} survived rotation"
    )


@pytest.mark.parametrize("var,placeholder,method", ROTATORS)
def test_hand_supplied_value_is_preserved(tmp_path, var, placeholder, method):
    real = "operator-supplied-real-value-9c3f"
    _seed_env(tmp_path, f"{var}={real}\n")
    kg = KeyGenerator(str(tmp_path))
    assert getattr(kg, method)() is True
    assert kg.get_current_env_value(var) == real, (
        f"{var}: real hand-supplied value was overwritten"
    )


def test_graph_db_auth_stays_in_sync_with_password(tmp_path):
    """`services/neo4j/compose.yml` passes the literal `GRAPH_DB_AUTH`
    string to `NEO4J_AUTH`. Rotating `GRAPH_DB_PASSWORD` without
    rewriting the composite leaves Neo4j authenticating against the
    stale embedded password.
    """
    _seed_env(
        tmp_path,
        """
        GRAPH_DB_USER=neo4j
        GRAPH_DB_PASSWORD=neo4j_password
        GRAPH_DB_AUTH=neo4j/neo4j_password
        """,
    )
    kg = KeyGenerator(str(tmp_path))
    assert kg.generate_and_update_graph_db_password() is True
    new_pw = kg.get_current_env_value("GRAPH_DB_PASSWORD")
    assert new_pw and new_pw != "neo4j_password"
    assert kg.get_current_env_value("GRAPH_DB_AUTH") == f"neo4j/{new_pw}"


def test_graph_db_rotation_skips_when_neo4j_volume_exists(tmp_path, monkeypatch):
    _seed_env(
        tmp_path,
        """
        PROJECT_NAME=atlas
        GRAPH_DB_USER=neo4j
        GRAPH_DB_PASSWORD=neo4j_password
        GRAPH_DB_AUTH=neo4j/neo4j_password
        """,
    )
    kg = KeyGenerator(str(tmp_path))
    monkeypatch.setattr(kg, "_neo4j_db_volume_exists", lambda: True)
    assert kg.generate_and_update_graph_db_password() is True
    assert kg.get_current_env_value("GRAPH_DB_PASSWORD") == "neo4j_password"
    assert kg.get_current_env_value("GRAPH_DB_AUTH") == "neo4j/neo4j_password"


def test_graph_db_rotation_proceeds_when_no_neo4j_volume(tmp_path, monkeypatch):
    _seed_env(
        tmp_path,
        """
        PROJECT_NAME=atlas
        GRAPH_DB_USER=neo4j
        GRAPH_DB_PASSWORD=neo4j_password
        GRAPH_DB_AUTH=neo4j/neo4j_password
        """,
    )
    kg = KeyGenerator(str(tmp_path))
    monkeypatch.setattr(kg, "_neo4j_db_volume_exists", lambda: False)
    assert kg.generate_and_update_graph_db_password() is True
    new_pw = kg.get_current_env_value("GRAPH_DB_PASSWORD")
    assert new_pw and new_pw != "neo4j_password"
    assert kg.get_current_env_value("GRAPH_DB_AUTH") == f"neo4j/{new_pw}"


def test_force_rotates_graph_db_password_even_with_existing_volume(tmp_path, monkeypatch):
    _seed_env(
        tmp_path,
        """
        PROJECT_NAME=atlas
        GRAPH_DB_USER=neo4j
        GRAPH_DB_PASSWORD=neo4j_password
        GRAPH_DB_AUTH=neo4j/neo4j_password
        """,
    )
    kg = KeyGenerator(str(tmp_path))
    monkeypatch.setattr(kg, "_neo4j_db_volume_exists", lambda: True)
    assert kg.generate_and_update_graph_db_password(force=True) is True
    assert kg.get_current_env_value("GRAPH_DB_PASSWORD") != "neo4j_password"


def test_no_unrotated_nonempty_secret_defaults():
    """Every env var marked secret: true in a manifest must either ship an
    empty default (generated-when-absent) or be a known PLACEHOLDER_DEFAULTS
    value (rotated-when-placeholder). A non-empty secret default that is
    NOT in PLACEHOLDER_DEFAULTS would ship a real-looking credential that
    nothing rotates."""
    import sys
    import pathlib

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from services.manifests import load_manifests
    from utils.key_generator import KeyGenerator

    repo = pathlib.Path(__file__).resolve().parents[2]
    manifests = load_manifests(repo / "services")
    placeholder_defaults = KeyGenerator.PLACEHOLDER_DEFAULTS
    offenders = []
    for m in manifests:
        for env in m.env:
            if not getattr(env, "secret", False):
                continue
            default = getattr(env, "default", "") or ""
            if default == "":
                continue  # generated-when-absent: fine
            if env.name in placeholder_defaults and placeholder_defaults[env.name] == default:
                continue  # rotated placeholder: fine
            offenders.append((m.name, env.name, default))
    assert not offenders, f"unrotated non-empty secret defaults: {offenders}"


def test_generate_missing_keys_covers_all_placeholder_vars(tmp_path):
    """End-to-end: the aggregator generates every placeholder var."""
    body = "\n".join(
        f"{var}={placeholder}" for var, placeholder, _ in ROTATORS
    ) + "\n"
    _seed_env(tmp_path, body)
    kg = KeyGenerator(str(tmp_path))
    results = kg.generate_missing_keys(force_regenerate=False)
    for var, placeholder, _ in ROTATORS:
        assert results.get(var) is True, f"{var} not in results / failed"
        assert kg.get_current_env_value(var) != placeholder, (
            f"{var}: aggregator did not upgrade placeholder"
        )


def test_generate_missing_keys_creates_backend_kong_api_key(tmp_path):
    _seed_env(tmp_path, "BACKEND_KONG_API_KEY=\n")
    kg = KeyGenerator(str(tmp_path))

    results = kg.generate_missing_keys(force_regenerate=False)

    assert results.get("BACKEND_KONG_API_KEY") is True
    key = kg.get_current_env_value("BACKEND_KONG_API_KEY")
    assert key
    assert key.startswith("sk-backend-")


def test_assert_no_placeholders_detects_unrotated(tmp_path):
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from utils.key_generator import KeyGenerator
    env = tmp_path / ".env"
    env.write_text("REDIS_PASSWORD=redis_password\nDASHBOARD_PASSWORD=already-rotated-xyz\n", encoding="utf-8")
    kg = KeyGenerator(str(env))
    import pytest
    with pytest.raises(Exception) as ei:
        kg.assert_no_placeholders_remaining()
    msg = str(ei.value)
    assert "REDIS_PASSWORD" in msg
    assert "DASHBOARD_PASSWORD" not in msg, (
        "already-rotated var should not appear in the offender list"
    )


def test_generate_missing_keys_rotates_all_placeholder_defaults(tmp_path):
    """Consistency gate: every var in PLACEHOLDER_DEFAULTS must be rotated
    by generate_missing_keys(). Seed the .env with every placeholder at its
    literal value, run the aggregator, then assert_no_placeholders_remaining
    must not raise.

    If this test fails it means a PLACEHOLDER_DEFAULTS entry lacks a rotator
    inside generate_missing_keys() — a fresh `./start.sh --profile prod` would
    trip the security gate even after auto-rotation.
    """
    import textwrap
    body = "\n".join(
        f"{var}={placeholder}"
        for var, placeholder in KeyGenerator.PLACEHOLDER_DEFAULTS.items()
    ) + "\n"
    env = tmp_path / ".env"
    env.write_text(textwrap.dedent(body), encoding="utf-8")
    kg = KeyGenerator(str(tmp_path))
    kg.generate_missing_keys(force_regenerate=False)
    # Must not raise — all placeholders should now be rotated.
    kg.assert_no_placeholders_remaining()


def test_graph_db_auth_split_state_is_surfaced(tmp_path):
    """Edge: if GRAPH_DB_PASSWORD was hand-pinned (non-placeholder) but
    GRAPH_DB_AUTH still holds its placeholder, the password rotator does NOT
    fire (it is guarded on the password), so GRAPH_DB_AUTH stays stale. That
    must be SURFACED — in the generate_missing_keys results AND by the prod
    gate — not silently passed. Intentional, documented behavior."""
    import pytest
    env = tmp_path / ".env"
    env.write_text(
        "GRAPH_DB_USER=neo4j\n"
        "GRAPH_DB_PASSWORD=already-rotated-xyz\n"
        "GRAPH_DB_AUTH=neo4j/neo4j_password\n",
        encoding="utf-8",
    )
    kg = KeyGenerator(str(tmp_path))
    results = kg.generate_missing_keys(force_regenerate=False)
    assert results.get("GRAPH_DB_AUTH") is False
    with pytest.raises(RuntimeError) as ei:
        kg.assert_no_placeholders_remaining()
    assert "GRAPH_DB_AUTH" in str(ei.value)


# ── Existing-volume guard (stale-volume / password-drift foot-gun) ───────────
#
# The supabase_admin / app role passwords are baked into the supabase-db-data
# volume at initdb. If that volume already exists, rotating the .env placeholder
# to a fresh random value makes every client authenticate with a value the role
# does not have → "password authentication failed for user supabase_admin".
# The rotators must SKIP rotation (and warn) when the project's volume exists.

_DB_PW_ROTATORS = [
    ("SUPABASE_DB_PASSWORD", "password", "generate_and_update_supabase_db_password"),
    ("SUPABASE_DB_APP_PASSWORD", "app_password", "generate_and_update_supabase_db_app_password"),
]


@pytest.mark.parametrize("var,placeholder,method", _DB_PW_ROTATORS)
def test_rotation_skipped_when_db_volume_exists(tmp_path, monkeypatch, var, placeholder, method):
    _seed_env(tmp_path, f"PROJECT_NAME=atlas\n{var}={placeholder}\n")
    kg = KeyGenerator(str(tmp_path))
    monkeypatch.setattr(kg, "_supabase_db_volume_exists", lambda: True)
    # Returns True (handled, not an error) but leaves the value UNCHANGED.
    assert getattr(kg, method)() is True
    assert kg.get_current_env_value(var) == placeholder, (
        f"{var}: rotation must be skipped when the data volume already exists"
    )


@pytest.mark.parametrize("var,placeholder,method", _DB_PW_ROTATORS)
def test_rotation_proceeds_when_no_db_volume(tmp_path, monkeypatch, var, placeholder, method):
    _seed_env(tmp_path, f"PROJECT_NAME=atlas\n{var}={placeholder}\n")
    kg = KeyGenerator(str(tmp_path))
    monkeypatch.setattr(kg, "_supabase_db_volume_exists", lambda: False)
    assert getattr(kg, method)() is True
    assert kg.get_current_env_value(var) != placeholder, (
        f"{var}: first-run rotation must still happen when no volume exists"
    )


@pytest.mark.parametrize("var,placeholder,method", _DB_PW_ROTATORS)
def test_force_rotates_even_with_existing_volume(tmp_path, monkeypatch, var, placeholder, method):
    """force=True is an explicit override and bypasses the volume guard."""
    _seed_env(tmp_path, f"PROJECT_NAME=atlas\n{var}={placeholder}\n")
    kg = KeyGenerator(str(tmp_path))
    monkeypatch.setattr(kg, "_supabase_db_volume_exists", lambda: True)
    assert getattr(kg, method)(force=True) is True
    assert kg.get_current_env_value(var) != placeholder


def test_volume_check_false_without_project_name(tmp_path):
    """No PROJECT_NAME → cannot scope the volume → returns False without ever
    shelling out to docker, so a fresh install (and the rotation tests above)
    are never blocked."""
    _seed_env(tmp_path, "SUPABASE_DB_PASSWORD=password\n")
    kg = KeyGenerator(str(tmp_path))
    assert kg._supabase_db_volume_exists() is False


def test_neo4j_volume_check_accepts_hyphen_and_underscore_names(tmp_path, monkeypatch):
    import subprocess

    _seed_env(tmp_path, "PROJECT_NAME=atlas\n")
    kg = KeyGenerator(str(tmp_path))

    class Result:
        returncode = 0
        stdout = "atlas_graph-db-data\natlas-graph-db-data\nother-volume\n"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())
    assert kg._neo4j_db_volume_exists() is True


# ── Cold start force-regenerates the volume-baked DB passwords ────────────────
# These three are baked into their data volume at initdb/first-boot. A cold
# start wipes those volumes (perform_cold_start_cleanup, BEFORE this runs), so
# the password MUST be regenerated to stay in sync with the fresh initdb —
# otherwise a stale .env value drifts and yields
# "password authentication failed for user supabase_admin". generate_missing_keys
# must therefore pass the cold flag through (force=force_regenerate), not the
# old hardcoded force=False.

_VOLUME_BAKED_DB_PWS = {
    "SUPABASE_DB_PASSWORD": "real-admin-pw-aaaaaaaa",
    "SUPABASE_DB_APP_PASSWORD": "real-app-pw-bbbbbbbb",
    "GRAPH_DB_PASSWORD": "real-neo4j-pw-cccccccc",
}


def _seed_real_db_pws(tmp_path):
    body = "PROJECT_NAME=atlas\nGRAPH_DB_USER=neo4j\n" + "".join(
        f"{k}={v}\n" for k, v in _VOLUME_BAKED_DB_PWS.items()
    )
    _seed_env(tmp_path, body)


def test_cold_start_force_regenerates_volume_baked_db_passwords(tmp_path, monkeypatch):
    """COLD (force_regenerate=True): rotate the volume-baked DB passwords even
    when they hold a real value AND the volume still appears to exist. The cold
    wipe makes the old baked value obsolete; a fresh .env value re-bakes into
    the recreated volume. (Regression: these were hardcoded force=False and
    survived a cold start, drifting from the wiped+re-initdb'd volume.)"""
    _seed_real_db_pws(tmp_path)
    kg = KeyGenerator(str(tmp_path))
    monkeypatch.setattr(kg, "_supabase_db_volume_exists", lambda: True)
    kg.generate_missing_keys(force_regenerate=True)
    for var, old in _VOLUME_BAKED_DB_PWS.items():
        assert kg.get_current_env_value(var) != old, (
            f"{var} must be force-regenerated on a cold start, not preserved"
        )
    # GRAPH_DB_AUTH must track the rotated neo4j password.
    assert (kg.get_current_env_value("GRAPH_DB_AUTH") or "").endswith(
        kg.get_current_env_value("GRAPH_DB_PASSWORD")
    )


def test_non_cold_preserves_real_volume_baked_db_passwords(tmp_path, monkeypatch):
    """Non-cold (force_regenerate=False): a real volume-baked DB password must
    be preserved so .env stays in sync with the existing volume."""
    _seed_real_db_pws(tmp_path)
    kg = KeyGenerator(str(tmp_path))
    monkeypatch.setattr(kg, "_supabase_db_volume_exists", lambda: True)
    kg.generate_missing_keys(force_regenerate=False)
    for var, old in _VOLUME_BAKED_DB_PWS.items():
        assert kg.get_current_env_value(var) == old, (
            f"{var} must be preserved on a non-cold start"
        )
