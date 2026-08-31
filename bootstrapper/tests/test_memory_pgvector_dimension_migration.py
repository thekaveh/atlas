from __future__ import annotations

import re
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from bootstrapper.tests import seed_harness

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "services/supabase/db/scripts/14-backend-memory.sql"
RUNNER = REPO / "services/supabase/db/scripts/db-init-runner.sh"
SUPABASE_COMPOSE = REPO / "services/supabase/compose.yml"
BACKEND_COMPOSE = REPO / "services/backend/compose.yml"
CELERY_COMPOSE = REPO / "services/celery/compose.yml"


def test_memory_migration_is_dimension_parameterized_and_advisory_locked():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "atlas_memory_embedding_dim" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "memory_embedding_schema_state" in sql
    assert "weaviate_dirty_generation" in sql
    assert "complete_memory_weaviate_rebuild" in sql
    assert "weaviate_target_model" in sql
    assert "weaviate_synced_model" in sql
    assert "weaviate_synced_dimension" in sql
    assert "ensure_memory_weaviate_identity" in sql
    assert "embedding_model" in sql
    assert "embedding_generation" in sql
    assert "pgvector_target_model" in sql
    assert "pgvector_active_model" in sql
    assert "contract_memory_embedding_contract" in sql
    assert "NOT VALID" in sql
    assert "VALIDATE CONSTRAINT memory_facts_embedding_dimension" in sql
    assert "vector_dims(embedding)" in sql
    assert "embedding vector(768)" not in sql


def test_memory_migration_keeps_full_precision_and_uses_halfvec_only_for_wide_index():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert re.search(r"embedding\s+vector[,)]", sql)
    assert "embedding halfvec" not in sql
    assert "embedding::halfvec(%s)" in sql
    assert "vector_cosine_ops" in sql
    assert "halfvec_cosine_ops" in sql


def test_guarded_embedding_index_creation_revalidates_the_postcondition():
    sql = MIGRATION.read_text(encoding="utf-8")

    index_block = sql[
        sql.index("DO $create_embedding_index$"):
        sql.index("$create_embedding_index$;")
    ]
    assert "position(" not in index_block
    for catalog_contract in (
        "pg_index",
        "pg_am",
        "pg_opclass",
        "indrelid",
        "indisvalid",
        "indisready",
        "indislive",
        "indnkeyatts",
        "pg_get_expr",
    ):
        assert catalog_contract in sql
    assert "memory embedding index postcondition failed" in sql


def test_selected_dimension_is_wired_to_init_backend_and_worker():
    runner = RUNNER.read_text(encoding="utf-8")
    supabase = SUPABASE_COMPOSE.read_text(encoding="utf-8")
    backend = BACKEND_COMPOSE.read_text(encoding="utf-8")
    celery = CELERY_COMPOSE.read_text(encoding="utf-8")

    assert '-v "atlas_memory_embedding_dim=$LANGMEM_EMBEDDING_DIM"' in runner
    assert 'ATLAS_MEMORY_EMBEDDING_MODEL="${LANGMEM_EMBEDDING_MODEL:-${LITELLM_EMBEDDING_MODEL:-ollama/nomic-embed-text}}"' in runner
    assert '-v "atlas_memory_embedding_model=$ATLAS_MEMORY_EMBEDDING_MODEL"' in runner
    assert "LANGMEM_EMBEDDING_DIM: ${LANGMEM_EMBEDDING_DIM:-768}" in supabase
    assert "LANGMEM_EMBEDDING_MODEL: ${LANGMEM_EMBEDDING_MODEL:-}" in supabase
    assert "LITELLM_EMBEDDING_MODEL: ${LITELLM_EMBEDDING_MODEL:-ollama/nomic-embed-text}" in supabase
    assert "LANGMEM_EMBEDDING_DIM: ${LANGMEM_EMBEDDING_DIM:-768}" in backend
    assert "LANGMEM_EMBEDDING_DIM: ${LANGMEM_EMBEDDING_DIM:-768}" in celery


def _docker(*args: str, input_text: str | None = None, check: bool = True):
    return subprocess.run(
        ["docker", *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
        timeout=120,
    )


def _psql(container: str, sql: str, *, check: bool = True):
    return _docker(
        "exec", "-i", container, "psql", "-X", "-h", "127.0.0.1",
        "-U", seed_harness.DB_USER, "-d", seed_harness.DB_NAME,
        "-v", "ON_ERROR_STOP=1", "-At", "-f", "-",
        input_text=sql, check=check,
    )


def _apply_seed_scripts(
    container: str,
    dimension: int,
    *,
    model: str = "ollama/nomic-embed-text",
    only_memory: bool = False,
):
    scripts = [MIGRATION] if only_memory else sorted(seed_harness.SCRIPTS_DIR.glob("*.sql"))
    for script in scripts:
        _docker(
            "exec", "-i", container, "psql", "-X", "-h", "127.0.0.1",
            "-U", seed_harness.DB_USER, "-d", seed_harness.DB_NAME,
            "-v", "ON_ERROR_STOP=1",
            "-v", f"atlas_memory_embedding_dim={dimension}",
            "-v", f"atlas_memory_embedding_model={model}",
            "-f", "-", input_text=script.read_text(encoding="utf-8"),
        )


def _apply_scoped_roles(
    container: str,
    *,
    backend_role: str = "atlas_backend",
    airflow_reader: str = "atlas_airflow_reader",
):
    command = [
        "exec", "-e", "PGHOST=127.0.0.1",
        "-e", f"PGUSER={seed_harness.DB_USER}",
        "-e", f"PGPASSWORD={seed_harness.DB_PASSWORD}",
        "-e", f"PGDATABASE={seed_harness.DB_NAME}",
    ]
    role_env = {
        **seed_harness.SCOPED_ROLE_TEST_ENV,
        "BACKEND_DB_USER": backend_role,
        "AIRFLOW_ATLAS_DB_USER": airflow_reader,
    }
    for key, value in role_env.items():
        command.extend(("-e", f"{key}={value}"))
    command.extend((container, "sh", "/scripts/05-scoped-roles.sh"))
    return _docker(*command)


def _apply_memory_script(
    container: str, dimension: int, *, check: bool = True
):
    return _docker(
        "exec", "-i", container, "psql", "-X", "-h", "127.0.0.1",
        "-U", seed_harness.DB_USER, "-d", seed_harness.DB_NAME,
        "-v", "ON_ERROR_STOP=1",
        "-v", f"atlas_memory_embedding_dim={dimension}",
        "-v", "atlas_memory_embedding_model=ollama/nomic-embed-text",
        "-f", "-", input_text=MIGRATION.read_text(encoding="utf-8"),
        check=check,
    )


@pytest.fixture
def disposable_pgvector():
    if not seed_harness.docker_available():
        pytest.skip("local Docker daemon unavailable")
    seed_harness.ensure_database_image()
    token = uuid.uuid4().hex
    name = f"atlas-memory-dim-{token[:10]}"
    with seed_harness.seed_container_cleanup(name, token):
        _docker(
            "run", "-d", "--pull=never", "--name", name,
            "--label", f"{seed_harness.SEED_OWNER_LABEL}={token}",
            "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=1536m",
            "-v", f"{seed_harness.SCRIPTS_DIR}:/scripts:ro",
            "-e", f"POSTGRES_USER={seed_harness.DB_USER}",
            "-e", f"POSTGRES_PASSWORD={seed_harness.DB_PASSWORD}",
            "-e", f"POSTGRES_DB={seed_harness.DB_NAME}",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            seed_harness.DB_IMAGE,
        )
        seed_harness.wait_for_postgres(
            name, timeout_seconds=180, poll_interval=1
        )
        yield name


@pytest.mark.parametrize("dimension", [768, 1536, 3072])
def test_real_pgvector_fresh_dimension_indexed_write_and_query(
    disposable_pgvector, dimension
):
    container = disposable_pgvector
    _apply_seed_scripts(container, dimension)
    cast = "vector" if dimension <= 2000 else "halfvec"
    opclass = "vector_cosine_ops" if dimension <= 2000 else "halfvec_cosine_ops"
    result = _psql(
        container,
        f"""
        INSERT INTO public.memory_facts (
            content, embedding, embedding_model, embedding_generation
        ) SELECT
          'one',
          (ARRAY[1.0::real] || array_fill(0.0::real, ARRAY[{dimension - 1}]))::vector({dimension}),
          pgvector_target_model,
          pgvector_target_generation
        FROM public.memory_embedding_schema_state
        UNION ALL SELECT
          'two',
          (array_fill(0.0::real, ARRAY[{dimension - 1}]) || ARRAY[1.0::real])::vector({dimension}),
          pgvector_target_model,
          pgvector_target_generation
        FROM public.memory_embedding_schema_state;
        SELECT phase || ':' || active_dimension || ':' || target_dimension
          FROM public.memory_embedding_schema_state;
        SELECT content FROM public.memory_facts
         WHERE vector_dims(embedding) = {dimension}
           AND embedding_generation = 1
           AND embedding_model = 'ollama/nomic-embed-text'
         ORDER BY embedding::{cast}({dimension}) <=>
                  (ARRAY[1.0::real] || array_fill(0.0::real, ARRAY[{dimension - 1}]))::{cast}({dimension})
         LIMIT 1;
        SET enable_seqscan = off;
        SET plan_cache_mode = force_generic_plan;
        PREPARE atlas_memory_index_plan(text) AS
        SELECT content FROM public.memory_facts
         WHERE embedding IS NOT NULL
           AND vector_dims(embedding) = {dimension}
           AND embedding_generation = 1
           AND embedding_model = $1
         ORDER BY embedding::{cast}({dimension}) <=>
                  (ARRAY[1.0::real] || array_fill(0.0::real, ARRAY[{dimension - 1}]))::{cast}({dimension})
         LIMIT 1;
        EXPLAIN (COSTS OFF)
        EXECUTE atlas_memory_index_plan('ollama/nomic-embed-text');
        DEALLOCATE atlas_memory_index_plan;
        RESET plan_cache_mode;
        RESET enable_seqscan;
        SELECT pg_get_indexdef('public.idx_memory_facts_embedding'::regclass);
        """,
    ).stdout
    assert f"ready:{dimension}:{dimension}" in result
    assert "one" in result
    assert f"{cast}({dimension})" in result
    assert opclass in result
    assert "Index Scan using idx_memory_facts_embedding" in result


def test_real_pgvector_expand_backfill_contract_preserves_existing_768_data(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)
    before = _psql(
        container,
        """
        INSERT INTO public.memory_facts (content, embedding)
        VALUES ('legacy', array_fill(0.125::real, ARRAY[768])::vector(768)),
               ('legacy-null', NULL);
        SELECT embedding::text FROM public.memory_facts WHERE content='legacy';
        """,
    ).stdout.strip().splitlines()[-1]

    _apply_seed_scripts(container, 1536, only_memory=True)
    expanded = _psql(
        container,
        """
        SELECT vector_dims(embedding), phase, active_dimension, target_dimension,
               convalidated
          FROM public.memory_facts, public.memory_embedding_schema_state,
               pg_constraint
         WHERE content='legacy'
           AND conname='memory_facts_embedding_dimension';
        SELECT embedding::text FROM public.memory_facts WHERE content='legacy';
        """,
    ).stdout
    assert "768|backfill|768|1536|f" in expanded
    assert before in expanded

    premature = _psql(
        container,
        "SELECT public.contract_memory_embedding_dimension(1536);",
        check=False,
    )
    assert premature.returncode != 0
    assert "model and generation" in premature.stderr

    premature_identity = _psql(
        container,
        """
        SELECT public.contract_memory_embedding_contract(
            pgvector_target_model, target_dimension, pgvector_target_generation
        ) FROM public.memory_embedding_schema_state;
        """,
        check=False,
    )
    assert premature_identity.returncode != 0
    assert "backfill incomplete" in premature_identity.stderr

    _psql(
        container,
        """
        UPDATE public.memory_facts
           SET embedding=array_fill(0.25::real, ARRAY[1536])::vector(1536),
               embedding_model=s.pgvector_target_model,
               embedding_generation=s.pgvector_target_generation
          FROM public.memory_embedding_schema_state s
         WHERE content IN ('legacy', 'legacy-null');
        SELECT public.contract_memory_embedding_contract(
            pgvector_target_model, target_dimension, pgvector_target_generation
        ) FROM public.memory_embedding_schema_state;
        """,
    )
    contracted = _psql(
        container,
        "SELECT phase, active_dimension, target_dimension FROM public.memory_embedding_schema_state;",
    ).stdout
    assert contracted.strip() == "ready|1536|1536"
    mixed = _psql(
        container,
        "INSERT INTO public.memory_facts(content, embedding) VALUES "
        "('corrupt', array_fill(0.1::real, ARRAY[768])::vector(768));",
        check=False,
    )
    assert mixed.returncode != 0
    assert "memory_facts_embedding_dimension" in mixed.stderr


def test_real_pgvector_rerun_preserves_equivalent_index_and_function_privileges(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 3072)
    before = _psql(
        container,
        "SELECT relfilenode FROM pg_class WHERE oid='public.idx_memory_facts_embedding'::regclass; "
        "SELECT oid || ':' || convalidated FROM pg_constraint "
        "WHERE conname='memory_facts_embedding_dimension';",
    ).stdout.strip()
    _apply_seed_scripts(container, 3072, only_memory=True)
    after = _psql(
        container,
        "SELECT relfilenode FROM pg_class WHERE oid='public.idx_memory_facts_embedding'::regclass; "
        "SELECT oid || ':' || convalidated FROM pg_constraint "
        "WHERE conname='memory_facts_embedding_dimension';",
    ).stdout.strip()
    assert after == before

    privileges = _psql(
        container,
        """
        SELECT has_function_privilege('public',
          'public.contract_memory_embedding_dimension(integer)', 'EXECUTE');
        SELECT has_function_privilege('service_role',
          'public.contract_memory_embedding_dimension(integer)', 'EXECUTE');
        SELECT has_function_privilege('anon',
          'public.contract_memory_embedding_dimension(integer)', 'EXECUTE');
        SELECT has_function_privilege('authenticated',
          'public.contract_memory_embedding_dimension(integer)', 'EXECUTE');
        SELECT has_function_privilege('public',
          'public.contract_memory_embedding_contract(text,integer,bigint)', 'EXECUTE');
        SELECT has_function_privilege('service_role',
          'public.contract_memory_embedding_contract(text,integer,bigint)', 'EXECUTE');
        SELECT has_function_privilege('anon',
          'public.contract_memory_embedding_contract(text,integer,bigint)', 'EXECUTE');
        SELECT has_function_privilege('authenticated',
          'public.contract_memory_embedding_contract(text,integer,bigint)', 'EXECUTE');
        SELECT has_function_privilege('public',
          'public.complete_memory_weaviate_rebuild(bigint)', 'EXECUTE');
        SELECT has_function_privilege('service_role',
          'public.complete_memory_weaviate_rebuild(bigint)', 'EXECUTE');
        SELECT has_function_privilege('anon',
          'public.complete_memory_weaviate_rebuild(bigint)', 'EXECUTE');
        SELECT has_function_privilege('authenticated',
          'public.complete_memory_weaviate_rebuild(bigint)', 'EXECUTE');
        SELECT has_function_privilege('public',
          'public.mark_memory_weaviate_dirty()', 'EXECUTE');
        SELECT has_function_privilege('service_role',
          'public.mark_memory_weaviate_dirty()', 'EXECUTE');
        SELECT has_function_privilege('anon',
          'public.mark_memory_weaviate_dirty()', 'EXECUTE');
        SELECT has_function_privilege('authenticated',
          'public.mark_memory_weaviate_dirty()', 'EXECUTE');
        SELECT has_function_privilege('public',
          'public.ensure_memory_weaviate_identity(text,integer)', 'EXECUTE');
        SELECT has_function_privilege('service_role',
          'public.ensure_memory_weaviate_identity(text,integer)', 'EXECUTE');
        SELECT has_function_privilege('anon',
          'public.ensure_memory_weaviate_identity(text,integer)', 'EXECUTE');
        SELECT has_function_privilege('authenticated',
          'public.ensure_memory_weaviate_identity(text,integer)', 'EXECUTE');
        SELECT has_function_privilege('public',
          'public.complete_memory_weaviate_rebuild(bigint,text,integer)', 'EXECUTE');
        SELECT has_function_privilege('service_role',
          'public.complete_memory_weaviate_rebuild(bigint,text,integer)', 'EXECUTE');
        SELECT has_function_privilege('anon',
          'public.complete_memory_weaviate_rebuild(bigint,text,integer)', 'EXECUTE');
        SELECT has_function_privilege('authenticated',
          'public.complete_memory_weaviate_rebuild(bigint,text,integer)', 'EXECUTE');
        """,
    ).stdout.strip().splitlines()
    assert privileges == [
        "f", "f", "f", "f",
        "f", "t", "f", "f",
        "f", "f", "f", "f",
        "f", "t", "f", "f",
        "f", "t", "f", "f",
        "f", "t", "f", "f",
    ]


def test_real_pgvector_generation_prefix_collision_is_replaced(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)
    before = _psql(
        container,
        """
        DROP INDEX public.idx_memory_facts_embedding;
        CREATE INDEX idx_memory_facts_embedding ON public.memory_facts
        USING hnsw ((embedding::vector(768)) vector_cosine_ops)
        WHERE embedding IS NOT NULL AND vector_dims(embedding) = 768
          AND embedding_generation = 10;
        SELECT relfilenode FROM pg_class
         WHERE oid = 'public.idx_memory_facts_embedding'::regclass;
        """,
    ).stdout.strip().splitlines()[-1]

    _apply_memory_script(container, 768)
    after = _psql(
        container,
        """
        SELECT c.relfilenode, pg_get_expr(i.indpred, i.indrelid, false)
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indexrelid = 'public.idx_memory_facts_embedding'::regclass;
        """,
    ).stdout.strip()
    relfilenode, predicate = after.split("|", 1)
    assert relfilenode != before
    assert "embedding_generation = 1" in predicate
    assert "embedding_generation = 10" not in predicate


def test_real_pgvector_extra_predicate_and_expression_shape_are_replaced(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)
    before = _psql(
        container,
        """
        DROP INDEX public.idx_memory_facts_embedding;
        CREATE INDEX idx_memory_facts_embedding ON public.memory_facts
        USING hnsw ((((embedding::halfvec(768))::vector(768))) vector_cosine_ops)
        WHERE embedding IS NOT NULL AND vector_dims(embedding) = 768
          AND embedding_generation = 1 AND is_active = true;
        SELECT relfilenode FROM pg_class
         WHERE oid = 'public.idx_memory_facts_embedding'::regclass;
        """,
    ).stdout.strip().splitlines()[-1]

    _apply_memory_script(container, 768)
    after = _psql(
        container,
        """
        SELECT c.relfilenode, pg_get_expr(i.indexprs, i.indrelid, false),
               pg_get_expr(i.indpred, i.indrelid, false)
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indexrelid = 'public.idx_memory_facts_embedding'::regclass;
        """,
    ).stdout.strip()
    relfilenode, expression, predicate = after.split("|", 2)
    assert relfilenode != before
    assert "halfvec" not in expression
    assert "is_active" not in predicate


def test_real_pgvector_wrong_am_extra_key_and_invalid_index_are_replaced(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)
    _psql(
        container,
        """
        DROP INDEX public.idx_memory_facts_embedding;
        CREATE INDEX idx_memory_facts_embedding ON public.memory_facts
          USING btree (embedding_generation, id);
        """,
    )
    _apply_memory_script(container, 768)
    corrected = _psql(
        container,
        """
        SELECT am.amname, i.indnkeyatts, i.indisvalid, i.indisready
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
          JOIN pg_am am ON am.oid = c.relam
         WHERE i.indexrelid = 'public.idx_memory_facts_embedding'::regclass;
        """,
    ).stdout.strip()
    assert corrected == "hnsw|1|t|t"

    _psql(
        container,
        """
        DROP INDEX public.idx_memory_facts_embedding;
        CREATE INDEX idx_memory_facts_embedding ON public.memory_facts
        USING hnsw ((embedding::vector(768)) vector_l2_ops)
        WHERE embedding IS NOT NULL AND vector_dims(embedding) = 768
          AND embedding_generation = 1;
        """,
    )
    _apply_memory_script(container, 768)
    corrected_opclass = _psql(
        container,
        """
        SELECT opclass.opcname
          FROM pg_index i
          JOIN pg_opclass opclass ON opclass.oid = i.indclass[0]
         WHERE i.indexrelid = 'public.idx_memory_facts_embedding'::regclass;
        """,
    ).stdout.strip()
    assert corrected_opclass == "vector_cosine_ops"

    _psql(
        container,
        "UPDATE pg_index SET indisvalid = false, indisready = false "
        "WHERE indexrelid = 'public.idx_memory_facts_embedding'::regclass;",
    )
    invalid_node = _psql(
        container,
        "SELECT relfilenode FROM pg_class "
        "WHERE oid = 'public.idx_memory_facts_embedding'::regclass;",
    ).stdout.strip()
    _apply_memory_script(container, 768)
    repaired = _psql(
        container,
        """
        SELECT c.relfilenode, i.indisvalid, i.indisready, i.indislive
          FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indexrelid = 'public.idx_memory_facts_embedding'::regclass;
        """,
    ).stdout.strip().split("|")
    assert repaired[0] != invalid_node
    assert repaired[1:] == ["t", "t", "t"]


def test_real_pgvector_foreign_relation_index_name_collision_fails_closed(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)
    before = _psql(
        container,
        """
        DROP INDEX public.idx_memory_facts_embedding;
        CREATE TABLE public.memory_index_foreign_owner(id bigint);
        CREATE INDEX idx_memory_facts_embedding
          ON public.memory_index_foreign_owner(id);
        SELECT c.relfilenode
          FROM pg_class c
         WHERE c.oid = 'public.idx_memory_facts_embedding'::regclass;
        """,
    ).stdout.strip().splitlines()[-1]

    migration = _apply_memory_script(container, 768, check=False)
    assert migration.returncode != 0
    assert "belongs to public.memory_index_foreign_owner" in migration.stderr
    preserved = _psql(
        container,
        """
        SELECT c.relfilenode, i.indrelid::regclass
          FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indexrelid = 'public.idx_memory_facts_embedding'::regclass;
        """,
    ).stdout.strip()
    assert preserved == f"{before}|memory_index_foreign_owner"


def test_real_pgvector_concurrent_foreign_index_name_collision_fails_closed(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)
    _psql(
        container,
        "DROP INDEX public.idx_memory_facts_embedding;",
    )
    race_key = 7821643
    creator_sql = f"""
        BEGIN;
        CREATE TABLE public.memory_index_concurrent_owner(id bigint);
        CREATE INDEX idx_memory_facts_embedding
          ON public.memory_index_concurrent_owner(id);
        SELECT pg_advisory_lock({race_key});
        SELECT pg_sleep(2);
        COMMIT;
    """
    creator = subprocess.Popen(
        [
            "docker", "exec", "-i", container, "psql", "-X", "-h", "127.0.0.1",
            "-U", seed_harness.DB_USER, "-d", seed_harness.DB_NAME,
            "-v", "ON_ERROR_STOP=1", "-At", "-c", creator_sql,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(50):
            locked = _psql(
                container,
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'advisory' AND granted "
                f"AND objid = {race_key};",
            ).stdout.strip()
            if locked == "1":
                break
            time.sleep(0.1)
        else:
            pytest.fail("concurrent index creator did not reach the race barrier")

        migration = _apply_memory_script(container, 768, check=False)
        creator_stdout, creator_stderr = creator.communicate(timeout=10)
        assert creator.returncode == 0, creator_stderr or creator_stdout
        assert migration.returncode != 0
        preserved = _psql(
            container,
            """
            SELECT i.indrelid::regclass
              FROM pg_index i
             WHERE i.indexrelid =
                   'public.idx_memory_facts_embedding'::regclass;
            """,
        ).stdout.strip()
        assert preserved == "memory_index_concurrent_owner"
    finally:
        if creator.poll() is None:
            creator.kill()
            creator.communicate(timeout=5)


def test_real_schema_state_rls_is_idempotent_and_backend_read_only(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)
    _apply_scoped_roles(container)

    before = _psql(
        container,
        """
        SELECT active_dimension, target_dimension, phase,
               pgvector_target_model, pgvector_target_generation
          FROM public.memory_embedding_schema_state;
        SELECT relfilenode
          FROM pg_class
         WHERE oid = 'public.idx_memory_facts_embedding'::regclass;
        """,
    ).stdout.strip()

    # Repeat the complete ordered seed, including 06-permissions.sql's broad
    # GRANT ON ALL TABLES, before reapplying scoped roles. This proves 14's
    # revokes repair both default privileges and grants reapplied on restart.
    _apply_seed_scripts(container, 768)
    _apply_scoped_roles(container)

    after = _psql(
        container,
        """
        SELECT active_dimension, target_dimension, phase,
               pgvector_target_model, pgvector_target_generation
          FROM public.memory_embedding_schema_state;
        SELECT relfilenode
          FROM pg_class
         WHERE oid = 'public.idx_memory_facts_embedding'::regclass;
        SELECT c.relrowsecurity, c.relforcerowsecurity,
               pg_get_userbyid(c.relowner)
          FROM pg_class c
         WHERE c.oid = 'public.memory_embedding_schema_state'::regclass;
        SELECT policyname, cmd, roles::text
          FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename = 'memory_embedding_schema_state'
         ORDER BY policyname;
        SELECT pg_get_userbyid(p.proowner)
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.proname = 'ensure_memory_weaviate_identity';
        """,
    ).stdout.strip().splitlines()
    assert after[:2] == before.splitlines()
    assert after[2:] == [
        "t|f|supabase_admin",
        "Atlas backend schema-state read|SELECT|{atlas_backend}",
        "supabase_admin",
    ]

    backend = _psql(
        container,
        "SET ROLE atlas_backend; "
        "SELECT count(*) FROM public.memory_embedding_schema_state;",
    )
    assert backend.stdout.strip() == "SET\n1"

    backend_write = _psql(
        container,
        "SET ROLE atlas_backend; "
        "UPDATE public.memory_embedding_schema_state SET phase = phase;",
        check=False,
    )
    assert backend_write.returncode != 0
    assert "permission denied" in backend_write.stderr

    for role in ("anon", "authenticated", "service_role"):
        client_read = _psql(
            container,
            f"SET ROLE {role}; "
            "SELECT count(*) FROM public.memory_embedding_schema_state;",
            check=False,
        )
        assert client_read.returncode != 0
        assert "permission denied" in client_read.stderr

    service_function = _psql(
        container,
        "SET ROLE service_role; "
        "SELECT public.ensure_memory_weaviate_identity('test/embed', 768);",
    )
    assert service_function.stdout.strip().splitlines()[0] == "SET"

    # Configurable role identifiers are quoted with format(%I), including
    # punctuation. Reset the durable marker first to exercise the one-time
    # named-policy upgrade fallback used by installs created before the marker.
    _psql(
        container,
        "ALTER DATABASE postgres RESET atlas.managed_backend_role;",
    )
    custom_backend = "atlas-backend.review"
    _apply_scoped_roles(container, backend_role=custom_backend)
    custom_policy = _psql(
        container,
        """
        SELECT policyname, cmd, roles::text
          FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename = 'memory_embedding_schema_state';
        """,
    ).stdout.strip()
    assert custom_policy == (
        "Atlas backend schema-state read|SELECT|{atlas-backend.review}"
    )
    custom_read = _psql(
        container,
        'SET ROLE "atlas-backend.review"; '
        "SELECT count(*) FROM public.memory_embedding_schema_state;",
    )
    assert custom_read.stdout.strip() == "SET\n1"
    former_backend_read = _psql(
        container,
        "SET ROLE atlas_backend; "
        "SELECT count(*) FROM public.memory_embedding_schema_state;",
        check=False,
    )
    assert former_backend_read.returncode != 0

    former_privileges = _psql(
        container,
        """
        SELECT has_table_privilege('atlas_backend',
          'public.memory_embedding_schema_state', 'SELECT');
        SELECT has_table_privilege('atlas_backend',
          'public.memory_facts', 'SELECT');
        SELECT has_function_privilege('atlas_backend',
          'public.contract_memory_embedding_contract(text,integer,bigint)', 'EXECUTE');
        SELECT has_function_privilege('atlas_backend',
          'public.contract_memory_embedding_dimension(integer)', 'EXECUTE');
        SELECT has_function_privilege('atlas_backend',
          'public.set_memory_weaviate_rebuild_required(boolean)', 'EXECUTE');
        SELECT has_function_privilege('atlas_backend',
          'public.mark_memory_weaviate_dirty()', 'EXECUTE');
        SELECT has_function_privilege('atlas_backend',
          'public.ensure_memory_weaviate_identity(text,integer)', 'EXECUTE');
        SELECT has_function_privilege('atlas_backend',
          'public.complete_memory_weaviate_rebuild(bigint,text,integer)', 'EXECUTE');
        SELECT has_function_privilege('atlas_backend',
          'public.complete_memory_weaviate_rebuild(bigint)', 'EXECUTE');
        """,
    ).stdout.strip().splitlines()
    assert former_privileges == ["f"] * 9
    former_mutation = _psql(
        container,
        "SET ROLE atlas_backend; SELECT public.mark_memory_weaviate_dirty();",
        check=False,
    )
    assert former_mutation.returncode != 0
    assert "permission denied" in former_mutation.stderr

    # Rotate once more so the retired identity itself requires identifier
    # quoting. Only the platform-recorded prior backend may be contracted.
    unmanaged = "unmanaged.backend"
    _psql(
        container,
        f'CREATE ROLE "{unmanaged}"; '
        f'GRANT SELECT ON public.memory_embedding_schema_state TO "{unmanaged}"; '
        f'GRANT EXECUTE ON FUNCTION public.mark_memory_weaviate_dirty() TO "{unmanaged}";',
    )
    next_backend = "atlas-backend.next"
    _apply_scoped_roles(container, backend_role=next_backend)
    quoted_former = _psql(
        container,
        """
        SELECT has_table_privilege('atlas-backend.review',
          'public.memory_embedding_schema_state', 'SELECT');
        SELECT has_function_privilege('atlas-backend.review',
          'public.mark_memory_weaviate_dirty()', 'EXECUTE');
        SELECT has_table_privilege('unmanaged.backend',
          'public.memory_embedding_schema_state', 'SELECT');
        SELECT has_function_privilege('unmanaged.backend',
          'public.mark_memory_weaviate_dirty()', 'EXECUTE');
        """,
    ).stdout.strip().splitlines()
    assert quoted_former == ["f", "f", "t", "t"]
    quoted_mutation = _psql(
        container,
        'SET ROLE "atlas-backend.review"; '
        "SELECT public.mark_memory_weaviate_dirty();",
        check=False,
    )
    assert quoted_mutation.returncode != 0
    unmanaged_mutation = _psql(
        container,
        'SET ROLE "unmanaged.backend"; '
        "SELECT public.mark_memory_weaviate_dirty();",
    )
    assert unmanaged_mutation.returncode == 0

    # A retired Backend identity may be deliberately reassigned to a current
    # read-only role. Retirement must happen before current-role grants so it
    # cannot silently erase the newly resolved reader contract.
    final_backend = "atlas-backend.final"
    _apply_scoped_roles(
        container,
        backend_role=final_backend,
        airflow_reader=next_backend,
    )
    reassigned_reader_privileges = _psql(
        container,
        f"""
        SELECT has_table_privilege('{next_backend}',
          'public.memory_facts', 'SELECT');
        SELECT has_table_privilege('{next_backend}',
          'public.memory_facts', 'INSERT');
        SELECT has_function_privilege('{next_backend}',
          'public.mark_memory_weaviate_dirty()', 'EXECUTE');
        """,
    ).stdout.strip().splitlines()
    assert reassigned_reader_privileges == ["t", "f", "f"]
    reassigned_read = _psql(
        container,
        f'SET ROLE "{next_backend}"; SELECT count(*) FROM public.memory_facts;',
    )
    assert reassigned_read.returncode == 0


def test_real_pgvector_dirty_generation_compare_and_set_is_monotonic(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)

    result = _psql(
        container,
        """
        SELECT public.ensure_memory_weaviate_identity('test/embed', 768);
        SELECT public.complete_memory_weaviate_rebuild(
                   (SELECT weaviate_dirty_generation
                      FROM public.memory_embedding_schema_state),
                   'test/embed', 768);
        SELECT weaviate_rebuild_required
          FROM public.memory_embedding_schema_state;
        SELECT public.set_memory_weaviate_rebuild_required(true) IS NULL;
        SELECT weaviate_dirty_generation
          FROM public.memory_embedding_schema_state;
        SELECT public.set_memory_weaviate_rebuild_required(true) IS NULL;
        SELECT weaviate_dirty_generation
          FROM public.memory_embedding_schema_state;
        SELECT public.complete_memory_weaviate_rebuild(
                   (SELECT weaviate_dirty_generation - 1
                      FROM public.memory_embedding_schema_state),
                   'test/embed', 768);
        SELECT weaviate_rebuild_required
          FROM public.memory_embedding_schema_state;
        SELECT public.complete_memory_weaviate_rebuild(
                   (SELECT weaviate_dirty_generation
                      FROM public.memory_embedding_schema_state),
                   'test/embed', 768);
        SELECT weaviate_dirty_generation, weaviate_rebuild_required,
               weaviate_synced_generation
          FROM public.memory_embedding_schema_state;
        """,
    ).stdout.strip().splitlines()

    initial_generation = int(result[0])
    assert result == [
        str(initial_generation),
        "t",
        "f",
        "f",
        str(initial_generation + 1),
        "f",
        str(initial_generation + 2),
        "f",
        "t",
        "t",
        f"{initial_generation + 2}|f|{initial_generation + 2}",
    ]

    forbidden_clear = _psql(
        container,
        "SELECT public.set_memory_weaviate_rebuild_required(false);",
        check=False,
    )
    assert forbidden_clear.returncode != 0
    assert "only be cleared with generation CAS" in forbidden_clear.stderr

    legacy_cas = _psql(
        container,
        "SELECT public.complete_memory_weaviate_rebuild(1);",
        check=False,
    )
    assert legacy_cas.returncode != 0
    assert "requires generation, model, and dimension" in legacy_cas.stderr


def test_real_pgvector_failback_cas_waits_for_durable_retirement_intent(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)

    result = _psql(
        container,
        """
        SELECT public.ensure_memory_weaviate_identity('test/embed', 768);
        SELECT public.complete_memory_weaviate_rebuild(
            weaviate_dirty_generation, 'test/embed', 768
        ) FROM public.memory_embedding_schema_state;
        INSERT INTO public.memory_facts (
            content, is_active, vector_sync_pending, embedding
        ) VALUES (
            'retired during outage', false, true,
            array_fill(0.1::real, ARRAY[768])::vector(768)
        );
        SELECT public.mark_memory_weaviate_dirty();
        SELECT public.complete_memory_weaviate_rebuild(
                   (SELECT weaviate_dirty_generation
                      FROM public.memory_embedding_schema_state),
                   'test/embed', 768);
        UPDATE public.memory_facts
           SET vector_sync_pending = false
         WHERE content = 'retired during outage';
        SELECT public.complete_memory_weaviate_rebuild(
                   (SELECT weaviate_dirty_generation
                      FROM public.memory_embedding_schema_state),
                   'test/embed', 768);
        """,
    ).stdout.strip().splitlines()

    assert [line for line in result if line in {"f", "t"}] == ["t", "f", "t"]


@pytest.mark.parametrize("target_dimension", [1536, 3072])
def test_clean_dimension_transition_dirties_synced_weaviate_identity(
    disposable_pgvector, target_dimension
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)
    synced = _psql(
        container,
        """
        SELECT public.ensure_memory_weaviate_identity(
            'ollama/nomic-embed-text', 768
        );
        SELECT public.complete_memory_weaviate_rebuild(
            weaviate_dirty_generation, 'ollama/nomic-embed-text', 768
        ) FROM public.memory_embedding_schema_state;
        SELECT weaviate_dirty_generation
          FROM public.memory_embedding_schema_state;
        """,
    ).stdout.strip().splitlines()
    clean_generation = int(synced[-1])

    _apply_seed_scripts(container, target_dimension, only_memory=True)
    transitioned = _psql(
        container,
        """
        SELECT target_dimension, weaviate_rebuild_required,
               weaviate_dirty_generation, weaviate_synced_model,
               weaviate_synced_dimension
          FROM public.memory_embedding_schema_state;
        """,
    ).stdout.strip()

    assert transitioned == (
        f"{target_dimension}|t|{clean_generation + 1}|"
        "ollama/nomic-embed-text|768"
    )


def test_same_dimension_model_change_advances_identity_generation(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768)
    result = _psql(
        container,
        """
        SELECT public.ensure_memory_weaviate_identity('provider-a/embed', 768);
        SELECT public.complete_memory_weaviate_rebuild(
            weaviate_dirty_generation, 'provider-a/embed', 768
        ) FROM public.memory_embedding_schema_state;
        SELECT weaviate_dirty_generation
          FROM public.memory_embedding_schema_state;
        SELECT public.ensure_memory_weaviate_identity('provider-b/embed', 768);
        SELECT weaviate_dirty_generation, weaviate_rebuild_required,
               weaviate_target_model, weaviate_synced_model
          FROM public.memory_embedding_schema_state;
        SELECT public.complete_memory_weaviate_rebuild(
            weaviate_dirty_generation - 1, 'provider-a/embed', 768
        ) FROM public.memory_embedding_schema_state;
        SELECT public.complete_memory_weaviate_rebuild(
            weaviate_dirty_generation, 'provider-b/embed', 768
        ) FROM public.memory_embedding_schema_state;
        """,
    ).stdout.strip().splitlines()

    clean_generation = int(result[2])
    assert result[4] == (
        f"{clean_generation + 1}|t|provider-b/embed|provider-a/embed"
    )
    assert result[-2:] == ["f", "t"]


def test_real_pgvector_same_dimension_model_change_requires_full_reembedding(
    disposable_pgvector,
):
    container = disposable_pgvector
    _apply_seed_scripts(container, 768, model="provider-a/embed")
    _psql(
        container,
        """
        INSERT INTO public.memory_facts (
            content, embedding, embedding_model, embedding_generation
        ) SELECT
            'same-dimension semantic change',
            array_fill(0.125::real, ARRAY[768])::vector(768),
            pgvector_target_model,
            pgvector_target_generation
        FROM public.memory_embedding_schema_state;
        """,
    )

    _apply_seed_scripts(
        container, 768, model="provider-b/embed", only_memory=True
    )
    transitioned = _psql(
        container,
        """
        SELECT phase, pgvector_active_model, pgvector_target_model,
               pgvector_active_generation, pgvector_target_generation,
               embedding_model, embedding_generation
        FROM public.memory_embedding_schema_state, public.memory_facts
        WHERE content = 'same-dimension semantic change';
        """,
    ).stdout.strip()
    assert transitioned == "backfill|provider-a/embed|provider-b/embed|1|2|provider-a/embed|1"

    legacy = _psql(
        container,
        "SELECT public.contract_memory_embedding_dimension(768);",
        check=False,
    )
    assert legacy.returncode != 0
    assert "model and generation" in legacy.stderr

    premature = _psql(
        container,
        "SELECT public.contract_memory_embedding_contract('provider-b/embed', 768, 2);",
        check=False,
    )
    assert premature.returncode != 0
    assert "backfill incomplete" in premature.stderr

    _psql(
        container,
        """
        UPDATE public.memory_facts
           SET embedding = array_fill(0.75::real, ARRAY[768])::vector(768),
               embedding_model = 'provider-b/embed',
               embedding_generation = 2
         WHERE content = 'same-dimension semantic change';
        SELECT public.contract_memory_embedding_contract(
            'provider-b/embed', 768, 2
        );
        """,
    )
    ready = _psql(
        container,
        """
        SELECT phase, pgvector_active_model, pgvector_target_model,
               pgvector_active_generation, pgvector_target_generation
        FROM public.memory_embedding_schema_state;
        SELECT content
        FROM public.memory_facts
        WHERE embedding_model = 'provider-b/embed'
          AND embedding_generation = 2
          AND vector_dims(embedding) = 768;
        """,
    ).stdout.strip().splitlines()
    assert ready == ["ready|provider-b/embed|provider-b/embed|2|2", "same-dimension semantic change"]
