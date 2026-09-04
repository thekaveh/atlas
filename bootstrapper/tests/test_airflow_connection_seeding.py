"""Verify that init-airflow.sh gates connection seeding on every sibling source."""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "services" / "airflow" / "init" / "scripts" / "init-airflow.sh"
ROLE_SCRIPT = REPO / "services" / "supabase" / "db" / "scripts" / "05-scoped-roles.sh"
COMPOSE = REPO / "services" / "airflow" / "compose.yml"
MANIFEST = REPO / "services" / "airflow" / "service.yml"


def _assert_contains(text, fragments):
    missing = tuple(fragment for fragment in fragments if fragment not in text)
    assert missing == ()


def test_init_script_exists_and_is_executable():
    assert SCRIPT.exists(), "init-airflow.sh missing"
    assert (SCRIPT.stat().st_mode & 0o111), "init-airflow.sh not executable"


def test_init_script_gates_each_sibling_connection():
    """Gating convention is `= "container"` (NOT `!= "disabled"`).

    The `!= "disabled"` form silently includes the `localhost` source
    variant for services that support it (weaviate / neo4j) and would seed
    a Connection pointing at in-Compose DNS that doesn't resolve in
    host-mode.

    Also locks the canonical var name `NEO4J_GRAPH_DB_SOURCE` (NOT
    `NEO4J_SOURCE` — the latter does not exist in .env.example; a pre-2026-06
    bug silently dropped the neo4j_default connection because the script
    read an undefined var).
    """
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'if [ "${SPARK_SOURCE}" = "container" ]' in body
    assert 'if [ "${MINIO_SOURCE}" = "container" ]' in body
    assert 'if [ "${WEAVIATE_SOURCE}" = "container" ]' in body
    assert 'if [ "${NEO4J_GRAPH_DB_SOURCE}" = "container" ]' in body
    # No conditional should reference the bogus NEO4J_SOURCE name.
    # (the canonical name appears in the prose comment that documents the
    # fix; we only forbid it as a shell variable expansion.)
    assert '${NEO4J_SOURCE}' not in body, (
        "init-airflow.sh should expand canonical ${NEO4J_GRAPH_DB_SOURCE}, "
        "not ${NEO4J_SOURCE} (which is silently undefined)."
    )


def test_init_script_seeds_always_on_connections_unconditionally():
    body = SCRIPT.read_text(encoding="utf-8")
    # Required deps and always-on services seed without gates.
    assert 'add_conn postgres_supabase' in body, "postgres_supabase Connection seed missing"
    assert 'add_conn litellm_default' in body, "litellm_default Connection seed missing"
    assert 'add_conn redis_default' in body, "redis_default Connection seed missing"
    # LiteLLM and Redis must NOT sit inside an `if`-guard — search for
    # the conditional patterns to be sure.
    assert 'if [ "${LITELLM_SOURCE}"' not in body, "LITELLM_SOURCE gate snuck back in"
    assert 'if [ "${REDIS_SOURCE}"' not in body, "REDIS_SOURCE gate snuck back in"


def test_airflow_contract_discloses_unsandboxed_privileged_dag_execution():
    body = SCRIPT.read_text(encoding="utf-8")
    scheduler = yaml.safe_load(COMPOSE.read_text())["services"]["airflow-scheduler"]

    _assert_contains(body, (
        "${MINIO_ROOT_USER}",
        "${MINIO_ROOT_PASSWORD}",
        "${LITELLM_MASTER_KEY}",
        "${AIRFLOW_ATLAS_DB_USER}",
        "${AIRFLOW_ATLAS_DB_PASSWORD}",
        "${GRAPH_DB_USER}",
        "${GRAPH_DB_PASSWORD}",
        "${REDIS_PASSWORD}",
    ))

    manifest = yaml.safe_load(MANIFEST.read_text())
    capability = next(
        row
        for row in manifest["capabilities"]
        if row["name"] == "Untrusted DAG code isolation"
    )
    assert (
        scheduler["environment"]["AIRFLOW__CORE__EXECUTOR"],
        "./dags:/opt/airflow/dags" in scheduler["volumes"],
        (capability["status"], capability["verification"]),
    ) == ("LocalExecutor", True, ("not-supported", "tested"))
    _assert_contains(capability["note"], (
        "operator-authored DAGs execute unsandboxed",
        "MinIO root",
        "LiteLLM master",
        "Supabase and Neo4j administrator",
        "Redis password",
        "trusted DAG authors",
    ))


def test_init_script_neo4j_uses_canonical_compose_dns_and_credentials():
    """neo4j_default Connection must point at the canonical compose service
    name (`neo4j-graph-db`, NOT `neo4j`) and must carry GRAPH_DB_USER /
    GRAPH_DB_PASSWORD credentials.

    Critically: Neo4jHook.get_uri() prepends `bolt://` itself
    (`f"{scheme}://{conn.host}:{port}"`), so the seed must use the BARE
    hostname — passing `bolt://neo4j-graph-db` would yield
    `bolt://bolt://neo4j-graph-db:7687` and the driver rejects it.
    """
    body = SCRIPT.read_text(encoding="utf-8")
    # Bare host without scheme — Hook prepends bolt://.
    assert '"neo4j-graph-db"' in body, (
        "neo4j_default conn-host must be bare 'neo4j-graph-db' — the Hook "
        "prepends bolt:// itself. A bolt:// prefix produces bolt://bolt://..."
    )
    assert "bolt://neo4j-graph-db" not in body or (
        # Allowed only in comments (the comment that explains the bug).
        body.count("bolt://neo4j-graph-db") <= body.count("# ")
    ), "bolt:// prefix in conn-host yields a double-scheme URI"
    assert "${GRAPH_DB_USER}" in body
    assert "${GRAPH_DB_PASSWORD}" in body


def test_init_script_litellm_default_host_includes_v1_path():
    """OpenAIHook builds base_url from conn.host (or openai_client_kwargs
    extra). It does NOT recognize an `api_base` extra. So the `/v1` path
    must live inside conn.host — otherwise the OpenAI SDK POSTs to
    /chat/completions instead of /v1/chat/completions and LiteLLM 404s.
    """
    body = SCRIPT.read_text(encoding="utf-8")
    assert "--conn-host http://litellm:4000/v1" in body, (
        "litellm_default conn.host must include /v1 — OpenAIHook ignores "
        "the legacy api_base extra and uses conn.host as base_url verbatim."
    )
    assert '"api_base"' not in body, (
        "api_base extra is silently ignored by OpenAIHook in 3.x. Set the "
        "full URL in conn.host instead."
    )


def test_init_script_weaviate_default_uses_bare_host_and_grpc_extras():
    """WeaviateHook passes conn.host straight into weaviate-client's
    connect_to_custom(http_host=..., grpc_host=extras['grpc_host'] or
    conn.host, grpc_port=extras['grpc_port'] or 80). conn.host must be
    bare ('weaviate'), conn.port must be 8080 (http), and the gRPC
    endpoint must be set via extras — Weaviate's gRPC is on 50051, not 80,
    and the v4 client requires gRPC for queries.
    """
    body = SCRIPT.read_text(encoding="utf-8")
    assert "--conn-host weaviate --conn-port 8080" in body, (
        "weaviate_default conn.host must be bare 'weaviate' (not "
        "'http://weaviate:8080') and conn.port must be 8080 explicit."
    )
    assert '"grpc_host": "weaviate"' in body
    assert '"grpc_port": 50051' in body


def test_init_script_minio_default_carries_path_style_addressing():
    """MinIO doesn't do DNS-style addressing (bucket.minio:9000); boto3
    defaults to virtual-hosted style. Any S3 op other than list_buckets
    fails DNS resolution without the addressing_style override. Mirrors
    spark.hadoop.fs.s3a.path.style.access=true on Spark's compose.
    """
    body = SCRIPT.read_text(encoding="utf-8")
    # JSON keys appear escaped (\"...\") inside the bash add_conn argument.
    assert "addressing_style" in body and "path" in body, (
        "minio_default extras must include S3 addressing_style=path or "
        "boto3 fails DNS on bucket-level operations."
    )
    assert "region_name" in body, (
        "minio_default extras must set region_name to avoid NoRegionError "
        "on newer boto3 versions."
    )


def test_init_script_re_applies_airflow_db_password():
    """The provisioner must conditionally re-apply rotated passwords.

    Rewriting an unchanged SCRAM password changes its salted verifier, so the
    central provisioner compares its managed input marker first. A changed
    AIRFLOW_DB_PASSWORD still reaches ALTER ROLE on the next cold start.
    """
    body = ROLE_SCRIPT.read_text(encoding="utf-8")
    assert "ALTER ROLE %I LOGIN PASSWORD %L" in body
    assert "atlas.password_fingerprint" in body


def test_init_script_quotes_configurable_airflow_db_role():
    """AIRFLOW_DB_USER is configurable, so role use must be quoted by psql."""
    body = ROLE_SCRIPT.read_text(encoding="utf-8")
    assert "rolname = :'role'" in body
    assert "CREATE ROLE %I LOGIN" in body
    assert "ALTER ROLE %I LOGIN PASSWORD %L" in body
    assert 'ensure_database airflow "$AIRFLOW_DB_USER"' in body
    assert "rolname='${AIRFLOW_DB_USER}'" not in body


def test_init_script_orphan_cleanup_pass_present():
    """An unconditional `airflow connections delete` pass must run before
    the gated add_conn calls so that toggling a sibling source from
    container → disabled doesn't leave an orphan Connection pointing at
    a dead host (which a DAG referencing the connection would then hit).
    """
    body = SCRIPT.read_text(encoding="utf-8")
    assert "for orphan in spark_default minio_default weaviate_default neo4j_default" in body


def test_init_script_env_refs_match_compose_environment():
    """Every ${VAR} reference in init-airflow.sh must be passed through
    the airflow-init compose env block. Under `set -euo pipefail`, an
    unset var aborts the script — and webserver/scheduler/dag-processor
    all depend on airflow-init: service_completed_successfully, so a
    missing var bricks the entire family at first start.

    Pass 23 caught AIRFLOW_ADMIN_PASSWORD being referenced in the
    script but missing from compose.yml's env block (Pass 10 dropped
    _AIRFLOW_WWW_USER_PASSWORD as dead bait without realizing the
    canonical AIRFLOW_ADMIN_PASSWORD was the real consumer).
    """
    import re
    import yaml as _yaml
    body = SCRIPT.read_text(encoding="utf-8")
    compose_path = REPO / "services" / "airflow" / "compose.yml"
    compose = _yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    init_env = set(compose["services"]["airflow-init"]["environment"].keys())

    # Collect every ${VAR} reference (skip ${VAR:-default} bash patterns and
    # script-internal vars like ${conn_id}, ${orphan}, ${rc}, $i, $entry).
    refs = set()
    for match in re.finditer(r'\$\{([A-Z][A-Z0-9_]*)\}', body):
        refs.add(match.group(1))
    assert refs, "no ${VAR} references found in init-airflow.sh — regex stale or script syntax changed?"

    # Explicitly allowed: PGPASSWORD is set via `export PGPASSWORD=...`
    # before psql, then `unset PGPASSWORD` after — not from compose env.
    refs.discard("PGPASSWORD")

    missing = refs - init_env
    assert not missing, (
        f"init-airflow.sh references env vars NOT injected by airflow-init's "
        f"compose.yml environment block: {sorted(missing)}. Under "
        f"`set -euo pipefail`, unset vars abort the script and the entire "
        f"airflow family (webserver+scheduler+dag-processor) fails to start. "
        f"Add the missing vars to services/airflow/compose.yml::airflow-init.environment "
        f"(and mirror in service.yml::runtime_sc per the dual-write convention)."
    )


def test_init_script_alters_database_owner_for_pg15_public_schema():
    """Postgres 15+ tightened the public schema: ALL PRIVILEGES on a
    database no longer grants CREATE on `public`; only the database
    OWNER (via pg_database_owner) can create objects. ALTER DATABASE
    airflow OWNER TO ${AIRFLOW_DB_USER} flips ownership so `airflow db
    migrate` succeeds. Without this the stack ships supabase/postgres:17.x
    and every cold start would fail at db migrate with permission denied.
    """
    body = ROLE_SCRIPT.read_text(encoding="utf-8")
    assert "ALTER DATABASE %I OWNER TO %I" in body, (
        "the central provisioner must re-own the airflow database to the airflow "
        "role; otherwise PG15+ blocks CREATE TABLE in the public schema."
    )
