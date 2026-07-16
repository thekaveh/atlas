# Seed Partition (Part A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize `services/supabase/db/scripts/` so each application table's DDL lives in exactly one per-service, owned, guarded SQL file — with zero change to the resulting database schema, proven by a byte-identical `pg_dump`.

**Architecture:** Keep the existing single-folder, alphabetical-execution model (`db-init-runner.sh` runs `find /scripts -name '*.sql' | sort`). Split the mixed `05-public-tables.sql` (+ its satellites `05a`, `07`'s llms trigger, `08`, `12-extend`) into per-service vertical slices numbered `10`–`14`, leaving the core/Supabase scaffolding (`01`–`07`) in place. A dockerized Postgres harness captures a golden schema dump from the *current* scripts first, then guards that the partitioned scripts reproduce it exactly; static lints enforce one-owner-per-file and uniform idempotency guards.

**Tech Stack:** PostgreSQL (`supabase/postgres:17.4.1.016`), `psql`/`pg_dump`/`pg_isready` (run via `docker exec`), Python 3.10+, pytest (`uv run pytest`), the Docker CLI (already a CI dependency; no new Python packages).

## 1. Global Constraints

- **Behavior-preserving:** the partitioned scripts MUST produce a `pg_dump --schema-only` (and a seed-row snapshot) byte-identical to the current scripts. This is the hard gate.
- **Execution order preserved:** scripts run in alphabetical order; core scaffolding (`01`–`07`) runs before app slices (`10`–`14`); within a slice, table → migrations → trigger → seed.
- **Cross-file dependencies that MUST hold:** `public.users` (slice `10`) is FK-referenced by research (`13`) and memory (`14`); the `update_updated_at_column()` function stays in `07-functions.sql` (runs before all slices); the `update_llms_updated_at` trigger moves into the litellm slice (`11`).
- **Uniform guards:** every slice object is idempotent — `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, triggers wrapped in `DO $$ … IF NOT EXISTS pg_trigger … $$` (or `DROP TRIGGER IF EXISTS` first), seeds in `DO $$ … IF NOT EXISTS … $$` or `ON CONFLICT`.
- **Owner banner:** every slice file starts with `-- OWNER: <service> — only this service's objects belong here`.
- **No content rewrites:** move SQL statements verbatim (use `git mv` for whole-file renames). Do not fold `ALTER … ADD COLUMN` migrations back into `CREATE` statements — the migrations protect legacy volumes and keep the dump identical.
- **DB identity for tests:** image `supabase/postgres:17.4.1.016`, user `supabase_admin`, database `postgres` (mirrors `.env.example` `SUPABASE_DB_IMAGE`/`SUPABASE_DB_USER`/`SUPABASE_DB_NAME`).
- **Branch:** `model-sot-decoupling`. Part B and Part C are separate, later plans.

---

## 2. File Structure

**Create:**
- `bootstrapper/tests/seed_harness.py` — Docker Postgres helper: boot image, apply a scripts dir in sorted order, return normalized `pg_dump` + seed-row snapshot; `__main__` regenerates golden fixtures.
- `bootstrapper/tests/test_seed_scripts_equivalence.py` — docker-gated: golden-equivalence, sorted-run-succeeds (order), and double-run idempotency.
- `bootstrapper/tests/test_seed_partition_layout.py` — static (no DB): one-owner-per-file, owner banner, guard-clause, and completeness lints.
- `bootstrapper/tests/fixtures/seed_schema_golden.sql` — committed normalized golden schema dump.
- `bootstrapper/tests/fixtures/seed_rows_golden.txt` — committed golden seed-row snapshot.
- `services/supabase/db/scripts/10-users.sql` — `public.users` (split from `05`).
- `services/supabase/db/scripts/11-litellm.sql` — `public.llms` + its trigger + its migrations (split from `05`/`05a`/`07`).
- `services/supabase/db/scripts/12-comfyui.sql` — comfyui tables/indexes/seeds/extend (split from `05`/`08`/`12-extend`).
- `services/supabase/db/scripts/13-backend-research.sql` — research tables (renamed from `09`).
- `services/supabase/db/scripts/14-backend-memory.sql` — memory tables + migrations (renamed from `10`, with `10a` appended).

**Modify:**
- `services/supabase/db/scripts/07-functions.sql` — remove the `update_llms_updated_at` trigger block (keep `health()`, `update_updated_at_column()`, replication slot).
- `services/supabase/README.md` — update the script-list section if present.

**Delete:**
- `services/supabase/db/scripts/05-public-tables.sql`, `05a-public-tables-migrations.sql`, `08-seed-data.sql`, `09-research-tables.sql`, `10-langmem-tables.sql`, `10a-langmem-migrations.sql`, `12-extend-comfyui-models.sql`.

---

## 3. Task 1: Dockerized characterization harness + golden snapshot

Locks current behavior before any change. The harness runs the current scripts, captures a normalized schema dump + seed rows, commits them as goldens, and asserts the live run matches. Also proves the current set runs in sorted order and is idempotent.

**Files:**
- Create: `bootstrapper/tests/seed_harness.py`
- Create: `bootstrapper/tests/test_seed_scripts_equivalence.py`
- Create: `bootstrapper/tests/fixtures/seed_schema_golden.sql`
- Create: `bootstrapper/tests/fixtures/seed_rows_golden.txt`

**Interfaces:**
- Produces: `seed_harness.docker_available() -> bool`; `seed_harness.run_scripts_and_dump(scripts_dir: pathlib.Path, *, run_twice: bool = False) -> tuple[str, str]` returning `(normalized_schema_dump, seed_rows)`; module constants `SCRIPTS_DIR`, `SCHEMA_GOLDEN`, `ROWS_GOLDEN`, `DB_IMAGE`.

- [ ] **Step 1: Write the harness helper**

Create `bootstrapper/tests/seed_harness.py`:

```python
"""Docker-backed harness for the Supabase seed scripts.

Boots the pinned supabase/postgres image, applies every *.sql in a scripts
directory in sorted order (mimicking services/supabase/db/scripts/
db-init-runner.sh), and returns a normalized pg_dump + a seed-row snapshot.

No new Python deps: shells out to the Docker CLI + the psql/pg_dump that ship
inside the image. `python -m tests.seed_harness` (run from bootstrapper/)
regenerates the committed golden fixtures.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "services" / "supabase" / "db" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_GOLDEN = FIXTURES / "seed_schema_golden.sql"
ROWS_GOLDEN = FIXTURES / "seed_rows_golden.txt"

# Mirrors .env.example SUPABASE_DB_IMAGE / SUPABASE_DB_USER / SUPABASE_DB_NAME.
DB_IMAGE = "supabase/postgres:17.4.1.016"
DB_USER = "supabase_admin"
DB_NAME = "postgres"

# Deterministic seed data lives only in comfyui_workflows (from the former
# 08-seed-data.sql). Snapshot the columns the seed sets, ordered stably.
SEED_QUERY = (
    "SELECT name, description, category, active "
    "FROM public.comfyui_workflows ORDER BY name;"
)


def docker_available() -> bool:
    return shutil.which("docker") is not None


def _normalize(dump: str) -> str:
    """Drop pg_dump's comment lines (version banners etc.) and collapse blank
    lines, so the comparison is structural and image-patch-stable."""
    out: list[str] = []
    for line in dump.splitlines():
        if line.startswith("--"):
            continue
        if line.strip() == "" and (not out or out[-1] == ""):
            continue
        out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"


def run_scripts_and_dump(
    scripts_dir: Path, *, run_twice: bool = False
) -> tuple[str, str]:
    """Return (normalized_schema_dump, seed_rows). Raises CalledProcessError if
    any script exits non-zero (psql -v ON_ERROR_STOP=1)."""
    name = f"atlas-seedtest-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "-e", f"POSTGRES_USER={DB_USER}",
            "-e", "POSTGRES_PASSWORD=postgres",
            "-e", f"POSTGRES_DB={DB_NAME}",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            DB_IMAGE,
        ],
        check=True, capture_output=True,
    )
    try:
        for _ in range(180):
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", DB_USER, "-d", DB_NAME, "-q"]
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("postgres did not become ready in 180s")

        sql_files = sorted(scripts_dir.glob("*.sql"))
        passes = 2 if run_twice else 1
        for _ in range(passes):
            for sql in sql_files:
                subprocess.run(
                    ["docker", "exec", "-i", name, "psql", "-v", "ON_ERROR_STOP=1",
                     "-U", DB_USER, "-d", DB_NAME, "-f", "-"],
                    stdin=sql.open("rb"), check=True, capture_output=True,
                )

        schema = subprocess.run(
            ["docker", "exec", name, "pg_dump", "--schema-only",
             "-U", DB_USER, "-d", DB_NAME],
            check=True, capture_output=True, text=True,
        ).stdout
        rows = subprocess.run(
            ["docker", "exec", name, "psql", "-A", "-t",
             "-U", DB_USER, "-d", DB_NAME, "-c", SEED_QUERY],
            check=True, capture_output=True, text=True,
        ).stdout
        return _normalize(schema), rows.strip() + "\n"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


if __name__ == "__main__":
    if not docker_available():
        raise SystemExit("docker not on PATH — cannot regenerate goldens")
    schema, rows = run_scripts_and_dump(SCRIPTS_DIR)
    FIXTURES.mkdir(exist_ok=True)
    SCHEMA_GOLDEN.write_text(schema, encoding="utf-8")
    ROWS_GOLDEN.write_text(rows, encoding="utf-8")
    print(f"wrote {SCHEMA_GOLDEN} ({len(schema)} bytes)")
    print(f"wrote {ROWS_GOLDEN} ({len(rows)} bytes)")
```

- [ ] **Step 2: Generate the golden fixtures from the CURRENT scripts**

Run (from the repo root; first run pulls the ~2 GB image):

```bash
cd bootstrapper && uv run python -m tests.seed_harness
```

Expected: prints `wrote …/seed_schema_golden.sql (… bytes)` and `wrote …/seed_rows_golden.txt (… bytes)`. Open `bootstrapper/tests/fixtures/seed_rows_golden.txt` and confirm it contains the two workflow rows `Basic Text to Image|…|basic|t` and `SDXL Text to Image|…|basic|t`.

- [ ] **Step 3: Write the equivalence/order/idempotency tests**

Create `bootstrapper/tests/test_seed_scripts_equivalence.py`:

```python
"""Docker-gated guards that the seed scripts produce a stable schema.

These lock current behavior (Task 1) and must stay green across the Part A
partition (Task 3): same golden, regardless of how the *.sql files are split.
"""
from __future__ import annotations

import pytest

from tests import seed_harness

pytestmark = pytest.mark.skipif(
    not seed_harness.docker_available(), reason="docker not on PATH"
)


def test_schema_matches_golden():
    schema, _ = seed_harness.run_scripts_and_dump(seed_harness.SCRIPTS_DIR)
    assert schema == seed_harness.SCHEMA_GOLDEN.read_text(encoding="utf-8")


def test_seed_rows_match_golden():
    _, rows = seed_harness.run_scripts_and_dump(seed_harness.SCRIPTS_DIR)
    assert rows == seed_harness.ROWS_GOLDEN.read_text(encoding="utf-8")


def test_sorted_run_succeeds():
    # A non-zero exit (e.g. an FK reference to a not-yet-created table) raises
    # CalledProcessError inside the harness; reaching here means every script
    # ran cleanly in alphabetical order.
    schema, _ = seed_harness.run_scripts_and_dump(seed_harness.SCRIPTS_DIR)
    assert "CREATE TABLE" in schema


def test_double_run_is_idempotent():
    once, _ = seed_harness.run_scripts_and_dump(seed_harness.SCRIPTS_DIR)
    twice, _ = seed_harness.run_scripts_and_dump(
        seed_harness.SCRIPTS_DIR, run_twice=True
    )
    assert twice == once
```

- [ ] **Step 4: Run the tests — expect PASS on the current scripts**

```bash
cd bootstrapper && uv run pytest tests/test_seed_scripts_equivalence.py -v
```

Expected: 4 passed (or 4 skipped if docker is unavailable on this machine — that is acceptable locally; CI has docker).

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/tests/seed_harness.py \
        bootstrapper/tests/test_seed_scripts_equivalence.py \
        bootstrapper/tests/fixtures/seed_schema_golden.sql \
        bootstrapper/tests/fixtures/seed_rows_golden.txt
git commit -m "test(supabase): characterization harness + golden for seed scripts"
```

---

## 4. Task 2: Static layout lints (failing-first)

Encode the target per-service layout as static checks. They FAIL on the current mixed files (proving they detect the problem) and become the definition of "done" for Task 3.

**Files:**
- Create: `bootstrapper/tests/test_seed_partition_layout.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing consumed downstream (pure assertions over the scripts dir).

- [ ] **Step 1: Write the layout lints**

Create `bootstrapper/tests/test_seed_partition_layout.py`:

```python
"""Static (no-DB) lints for the per-service seed partition (Part A).

Enforces: each app table's CREATE lives in exactly one owned slice; every
slice carries an OWNER banner; every slice object is idempotently guarded;
the full set of app tables is present across the slices.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "services" / "supabase" / "db" / "scripts"

# Expected owning slice for each app table.
EXPECTED_OWNER = {
    "users": "10-users.sql",
    "llms": "11-litellm.sql",
    "comfyui_models": "12-comfyui.sql",
    "comfyui_workflows": "12-comfyui.sql",
    "comfyui_generations": "12-comfyui.sql",
    "research_sessions": "13-backend-research.sql",
    "research_results": "13-backend-research.sql",
    "research_sources": "13-backend-research.sql",
    "research_logs": "13-backend-research.sql",
    "memory_facts": "14-backend-memory.sql",
    "memory_sessions": "14-backend-memory.sql",
    "memory_consolidation_log": "14-backend-memory.sql",
}
SLICE_FILES = sorted({v for v in EXPECTED_OWNER.values()})

_CREATE_TABLE = re.compile(
    r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+public\.(\w+)", re.IGNORECASE
)


def _read(name: str) -> str:
    return (SCRIPTS_DIR / name).read_text(encoding="utf-8")


def test_each_app_table_created_in_exactly_one_slice():
    location: dict[str, list[str]] = {}
    for sql in SCRIPTS_DIR.glob("*.sql"):
        for table in _CREATE_TABLE.findall(sql.read_text(encoding="utf-8")):
            location.setdefault(table, []).append(sql.name)
    for table, owner in EXPECTED_OWNER.items():
        assert location.get(table) == [owner], (
            f"public.{table} should be created only in {owner}, "
            f"found in {location.get(table)}"
        )


def test_every_slice_has_owner_banner():
    for name in SLICE_FILES:
        first = _read(name).splitlines()[0:3]
        assert any(line.startswith("-- OWNER:") for line in first), (
            f"{name} missing '-- OWNER:' banner in its first lines"
        )


def test_slice_tables_are_guarded():
    bad = re.compile(r"CREATE TABLE\s+public\.", re.IGNORECASE)  # without IF NOT EXISTS
    for name in SLICE_FILES:
        text = _read(name)
        assert not bad.search(text), (
            f"{name} has a CREATE TABLE without IF NOT EXISTS"
        )
        for m in re.finditer(r"ADD COLUMN\s+(?!IF NOT EXISTS)", text, re.IGNORECASE):
            raise AssertionError(f"{name} has an ADD COLUMN without IF NOT EXISTS")
        for m in re.finditer(r"CREATE INDEX\s+(?!IF NOT EXISTS)", text, re.IGNORECASE):
            raise AssertionError(f"{name} has a CREATE INDEX without IF NOT EXISTS")


def test_all_expected_tables_present():
    found = set()
    for name in SLICE_FILES:
        found.update(_CREATE_TABLE.findall(_read(name)))
    missing = set(EXPECTED_OWNER) - found
    assert not missing, f"app tables missing from slices: {sorted(missing)}"


def test_old_mixed_files_are_gone():
    for stale in ("05-public-tables.sql", "05a-public-tables-migrations.sql",
                  "08-seed-data.sql", "09-research-tables.sql",
                  "10-langmem-tables.sql", "10a-langmem-migrations.sql",
                  "12-extend-comfyui-models.sql"):
        assert not (SCRIPTS_DIR / stale).exists(), f"{stale} should be removed"
```

- [ ] **Step 2: Run the lints — expect FAIL on the current layout**

```bash
cd bootstrapper && uv run pytest tests/test_seed_partition_layout.py -v
```

Expected: FAIL — e.g. `test_each_app_table_created_in_exactly_one_slice` reports `public.users should be created only in 10-users.sql, found in ['05-public-tables.sql']`, and `test_old_mixed_files_are_gone` fails. This confirms the lints detect the un-partitioned state.

- [ ] **Step 3: Commit**

```bash
git add bootstrapper/tests/test_seed_partition_layout.py
git commit -m "test(supabase): failing layout lints for per-service seed partition"
```

---

## 5. Task 3: Perform the partition

Split the mixed files into per-service slices. After this task, Task 2's lints pass and Task 1's golden/order/idempotency tests stay green.

**Files:**
- Create: `services/supabase/db/scripts/10-users.sql`, `11-litellm.sql`, `12-comfyui.sql`
- Rename: `09-research-tables.sql` → `13-backend-research.sql`; `10-langmem-tables.sql` → `14-backend-memory.sql`
- Modify: `14-backend-memory.sql` (append `10a` body), `07-functions.sql` (drop llms trigger)
- Delete: `05-public-tables.sql`, `05a-public-tables-migrations.sql`, `08-seed-data.sql`, `10a-langmem-migrations.sql`, `12-extend-comfyui-models.sql`

**Interfaces:**
- Consumes: the golden + lints from Tasks 1–2.
- Produces: the final script set `01,02,03,03b,04,06,07,10,11,12,13,14`.

- [ ] **Step 1: Create `10-users.sql`**

```sql
-- 10-users.sql
-- OWNER: backend/auth — public.users. FK-referenced by the research (13) and
-- memory (14) slices, so this MUST sort before them. Only this service's
-- objects belong here. Moved verbatim from the former 05-public-tables.sql.

CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

- [ ] **Step 2: Create `11-litellm.sql`**

```sql
-- 11-litellm.sql
-- OWNER: litellm — public.llms catalog, its idempotent migrations, and its
-- updated_at trigger. Only this service's objects belong here.
-- Assembled verbatim from the former 05-public-tables.sql (llms CREATE),
-- 05a-public-tables-migrations.sql (constraint/column/type migrations), and
-- 07-functions.sql (the update_llms_updated_at trigger). The shared
-- update_updated_at_column() function stays in 07-functions.sql, which sorts
-- before this file (07 < 11).

CREATE TABLE IF NOT EXISTS public.llms (
  id bigint generated by default as identity not null,
  active boolean not null default false,
  vision integer not null default 0,
  content integer not null default 0,
  structured_content integer not null default 0,
  embeddings integer not null default 0,
  provider character varying not null,
  name character varying not null,
  description text,
  size_gb numeric,
  context_window integer,
  api_key text,
  api_endpoint text,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  constraint llms_pkey primary key (id),
  constraint llms_id_key unique (id),
  constraint llms_name_key unique (name)
);

-- Idempotent migrations (formerly 05a-public-tables-migrations.sql).
-- Safe to re-run; no-ops on fresh installs.
ALTER TABLE public.llms DROP CONSTRAINT IF EXISTS llms_name_key;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'llms_provider_name_key'
      AND conrelid = 'public.llms'::regclass
  ) THEN
    ALTER TABLE public.llms
      ADD CONSTRAINT llms_provider_name_key UNIQUE (provider, name);
  END IF;
END $$;

ALTER TABLE public.llms
    ADD COLUMN IF NOT EXISTS description text,
    ADD COLUMN IF NOT EXISTS size_gb numeric,
    ADD COLUMN IF NOT EXISTS context_window integer,
    ADD COLUMN IF NOT EXISTS api_key text,
    ADD COLUMN IF NOT EXISTS api_endpoint text;

DO $$
DECLARE
    col text;
BEGIN
    FOREACH col IN ARRAY ARRAY['vision', 'content', 'structured_content', 'embeddings']
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'llms'
               AND column_name = col
               AND data_type = 'boolean'
        ) THEN
            EXECUTE format('ALTER TABLE public.llms ALTER COLUMN %I DROP DEFAULT', col);
            EXECUTE format('ALTER TABLE public.llms ALTER COLUMN %I TYPE integer USING (CASE WHEN %I THEN 1 ELSE 0 END)', col, col);
            EXECUTE format('ALTER TABLE public.llms ALTER COLUMN %I SET DEFAULT 0', col);
            EXECUTE format('ALTER TABLE public.llms ALTER COLUMN %I SET NOT NULL', col);
            RAISE NOTICE 'public.llms.% converted boolean → integer (legacy volume)', col;
        END IF;
    END LOOP;
END $$;

-- updated_at trigger (formerly in 07-functions.sql). The trigger function
-- update_updated_at_column() is defined in 07-functions.sql (sorts first).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'update_llms_updated_at'
    ) THEN
        CREATE TRIGGER update_llms_updated_at
            BEFORE UPDATE ON public.llms
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
```

- [ ] **Step 3: Create `12-comfyui.sql`**

```sql
-- 12-comfyui.sql
-- OWNER: comfyui — comfyui_models / comfyui_workflows / comfyui_generations,
-- their indexes, the catalog-metadata extension columns, and the default
-- workflow seeds. Only this service's objects belong here.
-- Assembled verbatim from the former 05-public-tables.sql (comfyui tables +
-- indexes), 12-extend-comfyui-models.sql (extension columns + source index),
-- and 08-seed-data.sql (default workflow seeds). Tables are created before the
-- ALTER/seed blocks that depend on them.

CREATE TABLE IF NOT EXISTS public.comfyui_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL, -- 'checkpoint', 'vae', 'lora', 'controlnet', 'upscaler', 'embeddings'
    filename VARCHAR(255) NOT NULL,
    download_url TEXT NOT NULL,
    file_size_gb DECIMAL(5,2),
    description TEXT,
    active BOOLEAN DEFAULT true,
    essential BOOLEAN DEFAULT false, -- Models that should be downloaded by default
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_comfyui_model_name UNIQUE (name, type)
);

CREATE INDEX IF NOT EXISTS idx_comfyui_models_active ON public.comfyui_models(active);
CREATE INDEX IF NOT EXISTS idx_comfyui_models_essential ON public.comfyui_models(essential);
CREATE INDEX IF NOT EXISTS idx_comfyui_models_type ON public.comfyui_models(type);

CREATE TABLE IF NOT EXISTS public.comfyui_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    workflow_data JSONB NOT NULL,
    category VARCHAR(100) DEFAULT 'custom',
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_comfyui_workflow_name UNIQUE (name)
);

CREATE INDEX IF NOT EXISTS idx_comfyui_workflows_active ON public.comfyui_workflows(active);
CREATE INDEX IF NOT EXISTS idx_comfyui_workflows_category ON public.comfyui_workflows(category);

CREATE TABLE IF NOT EXISTS public.comfyui_generations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt TEXT NOT NULL,
    negative_prompt TEXT,
    workflow_id UUID REFERENCES public.comfyui_workflows(id),
    image_url TEXT,
    image_path TEXT,
    parameters JSONB, -- Store generation parameters like seed, steps, cfg, etc.
    status VARCHAR(50) DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_comfyui_generations_status ON public.comfyui_generations(status);
CREATE INDEX IF NOT EXISTS idx_comfyui_generations_created_at ON public.comfyui_generations(created_at DESC);

-- Catalog-metadata extension columns (formerly 12-extend-comfyui-models.sql).
DO $$ BEGIN
  ALTER TABLE public.comfyui_models
    ADD COLUMN IF NOT EXISTS family TEXT,
    ADD COLUMN IF NOT EXISTS sha256 TEXT,
    ADD COLUMN IF NOT EXISTS target_dir TEXT,
    ADD COLUMN IF NOT EXISTS min_vram_gb DECIMAL(5,2),
    ADD COLUMN IF NOT EXISTS cpu_supported BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS requires_custom_node JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS popularity INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source TEXT;
END $$;

CREATE INDEX IF NOT EXISTS idx_comfyui_models_source ON public.comfyui_models(source);

-- Default workflow seeds (formerly 08-seed-data.sql).
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM public.comfyui_workflows WHERE name = 'Basic Text to Image') THEN
    INSERT INTO public.comfyui_workflows (name, description, workflow_data, category, active) VALUES
      ('Basic Text to Image', 'Simple text-to-image workflow using SD1.5',
       '{"nodes": [{"id": 1, "type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"}}, {"id": 2, "type": "CLIPTextEncode", "inputs": {"text": "a beautiful landscape", "clip": [1, 1]}}, {"id": 3, "type": "CLIPTextEncode", "inputs": {"text": "bad quality, blurry", "clip": [1, 1]}}, {"id": 4, "type": "KSampler", "inputs": {"seed": 42, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "model": [1, 0], "positive": [2, 0], "negative": [3, 0]}}, {"id": 5, "type": "VAEDecode", "inputs": {"samples": [4, 0], "vae": [1, 2]}}, {"id": 6, "type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": [5, 0]}}]}',
       'basic', true);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM public.comfyui_workflows WHERE name = 'SDXL Text to Image') THEN
    INSERT INTO public.comfyui_workflows (name, description, workflow_data, category, active) VALUES
      ('SDXL Text to Image', 'High-quality text-to-image workflow using SDXL',
       '{"nodes": [{"id": 1, "type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}}, {"id": 2, "type": "CLIPTextEncode", "inputs": {"text": "a beautiful landscape", "clip": [1, 1]}}, {"id": 3, "type": "CLIPTextEncode", "inputs": {"text": "bad quality, blurry", "clip": [1, 1]}}, {"id": 4, "type": "KSampler", "inputs": {"seed": 42, "steps": 25, "cfg": 7.5, "sampler_name": "dpmpp_2m", "scheduler": "karras", "model": [1, 0], "positive": [2, 0], "negative": [3, 0]}}, {"id": 5, "type": "VAEDecode", "inputs": {"samples": [4, 0], "vae": [1, 2]}}, {"id": 6, "type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI_SDXL", "images": [5, 0]}}]}',
       'basic', true);
  END IF;
END $$;
```

- [ ] **Step 4: Rename research and memory slices (verbatim, via git mv)**

```bash
cd /Users/kaveh/repos/atlas
git mv services/supabase/db/scripts/09-research-tables.sql services/supabase/db/scripts/13-backend-research.sql
git mv services/supabase/db/scripts/10-langmem-tables.sql services/supabase/db/scripts/14-backend-memory.sql
```

- [ ] **Step 5: Add OWNER banners to the renamed slices**

In `13-backend-research.sql`, replace the first two comment lines:

```sql
-- 13-backend-research.sql
-- OWNER: backend/local-deep-researcher — research_* tables. user_id FKs
-- reference public.users (slice 10, sorts first). Only this service's objects
-- belong here. Renamed verbatim from the former 09-research-tables.sql.
```

In `14-backend-memory.sql`, replace the first two comment lines:

```sql
-- 14-backend-memory.sql
-- OWNER: backend — memory_* tables + their idempotent migrations. user_id FKs
-- reference public.users (slice 10, sorts first). Only this service's objects
-- belong here. Assembled from the former 10-langmem-tables.sql and
-- 10a-langmem-migrations.sql (appended below).
```

- [ ] **Step 6: Append the memory migrations into `14-backend-memory.sql`, then delete `10a`**

Append the body of `10a-langmem-migrations.sql` (its `DO $$ … END $$;` block, with a section comment) to the end of `14-backend-memory.sql`:

```sql

-- ── Migrations (formerly 10a-langmem-migrations.sql) ───────────────────────
-- Idempotent: converts legacy VARCHAR(255) user_id → UUID and re-points the FK
-- at public.users(id). Safe to re-run; no-op on fresh installs.
DO $$
DECLARE
    tbl text;
    legacy_fk text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'memory_facts',
        'memory_sessions',
        'memory_consolidation_log'
    ]
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = tbl
               AND column_name = 'user_id'
               AND data_type = 'character varying'
        ) THEN
            CONTINUE;
        END IF;

        SELECT conname
          INTO legacy_fk
          FROM pg_constraint
         WHERE conrelid = ('public.' || tbl)::regclass
           AND contype = 'f'
           AND array_length(conkey, 1) = 1
           AND (SELECT attname FROM pg_attribute
                 WHERE attrelid = conrelid
                   AND attnum  = conkey[1]) = 'user_id';
        IF legacy_fk IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', tbl, legacy_fk);
            RAISE NOTICE 'public.%: dropped legacy user_id FK %', tbl, legacy_fk;
        END IF;

        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN user_id TYPE uuid USING user_id::uuid',
            tbl
        );

        EXECUTE format(
            'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE',
            tbl, tbl || '_user_id_fkey'
        );

        RAISE NOTICE 'public.%: user_id migrated VARCHAR(255) → UUID, FK → public.users(id)', tbl;
    END LOOP;
END $$;
```

Then remove the now-merged file:

```bash
git rm services/supabase/db/scripts/10a-langmem-migrations.sql
```

- [ ] **Step 7: Remove the llms trigger from `07-functions.sql`**

In `services/supabase/db/scripts/07-functions.sql`, delete this block (it moved to `11-litellm.sql` in Step 2):

```sql
-- Create updated_at trigger for llms table
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger 
        WHERE tgname = 'update_llms_updated_at'
    ) THEN
        CREATE TRIGGER update_llms_updated_at 
            BEFORE UPDATE ON public.llms 
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
```

Leave everything else in `07` (`health()`, its grant, `update_updated_at_column()`, the replication-slot blocks) untouched.

- [ ] **Step 8: Delete the remaining mixed source files**

```bash
git rm services/supabase/db/scripts/05-public-tables.sql \
       services/supabase/db/scripts/05a-public-tables-migrations.sql \
       services/supabase/db/scripts/08-seed-data.sql \
       services/supabase/db/scripts/12-extend-comfyui-models.sql
```

- [ ] **Step 9: Run the layout lints — expect PASS**

```bash
cd bootstrapper && uv run pytest tests/test_seed_partition_layout.py -v
```

Expected: all lint tests pass (one owner per table, banners present, guards present, stale files gone).

- [ ] **Step 10: Run the equivalence/order/idempotency tests — expect PASS (golden unchanged)**

```bash
cd bootstrapper && uv run pytest tests/test_seed_scripts_equivalence.py -v
```

Expected: 4 passed. The partitioned scripts reproduce the byte-identical golden schema + seed rows captured in Task 1, run cleanly in sorted order, and remain idempotent.

If `test_schema_matches_golden` fails on GRANT lines (table privileges differing because a table now sorts after `06-permissions`), reconcile by adding the table's explicit grants to its slice to match the golden — e.g. append to the affected slice:

```sql
GRANT SELECT ON public.<table> TO anon;
GRANT ALL ON public.<table> TO authenticated, service_role;
```

Re-run until the dump matches the golden exactly. (Expected outcome is a clean match without this, because `06`'s `ALTER DEFAULT PRIVILEGES` already covers tables created later by `supabase_admin`; this step is the documented contingency.)

- [ ] **Step 11: Commit**

```bash
cd /Users/kaveh/repos/atlas
git add services/supabase/db/scripts/
git commit -m "refactor(supabase): partition seed scripts into per-service slices

Split the mixed 05-public-tables.sql (+ 05a/07-trigger/08/12-extend) into
per-service owned slices 10-users, 11-litellm, 12-comfyui; rename research
(09->13) and memory (10/10a->14). Schema-identical (golden dump unchanged);
ordering preserved (users before research/memory; trigger fn in 07)."
```

---

## 6. Task 4: Docs + drift + full suite

**Files:**
- Modify: `services/supabase/README.md` (if it enumerates the scripts)

- [ ] **Step 1: Check for stale references to the old filenames**

```bash
cd /Users/kaveh/repos/atlas
grep -rn "05-public-tables\|05a-public-tables\|08-seed-data\|09-research-tables\|10-langmem\|10a-langmem\|12-extend-comfyui" \
  services/ bootstrapper/ docs/ --include=*.md --include=*.py --include=*.sh | grep -v docs/superpowers/
```

Expected: only matches inside the new slice files' own header comments (which intentionally cite their origins). Update any README/doc that lists the script set to reflect `10-users`, `11-litellm`, `12-comfyui`, `13-backend-research`, `14-backend-memory`.

- [ ] **Step 2: Run the docs-drift gate + audit scripts**

```bash
cd /Users/kaveh/repos/atlas
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check
python scripts/check-docs-drift.py
```

Expected: exit 0 (no drift). If `regen --check` reports drift in `services/supabase`, run `PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen supabase` and re-commit the regenerated files.

- [ ] **Step 3: Run the full bootstrapper suite**

```bash
cd bootstrapper && uv run pytest -q
```

Expected: all pass (or the docker-gated seed tests skip if docker is absent locally). No previously-passing test regresses.

- [ ] **Step 4: Commit**

```bash
cd /Users/kaveh/repos/atlas
git add -A
git commit -m "docs(supabase): refresh script-list references after seed partition"
```

---

## 7. Self-Review

**Spec coverage (Part A sections):**
- §5.1 layout (two tiers, per-service slices) → Task 3 (`10`–`14`) + Task 2 lints.
- §5.2 guards → Task 2 `test_slice_tables_are_guarded` + verbatim-preserved `DO $$ … IF NOT EXISTS` / `ON CONFLICT` blocks in Task 3.
- §5.3 ordering invariant → Task 1 `test_sorted_run_succeeds` + Task 3 numbering (`users` 10 < research 13 / memory 14; trigger fn in `07`).
- §9.1 execution tests (order/equivalence/idempotency) → Task 1.
- §9.1 static lints (ownership/guard/completeness) → Task 2.
- §5.4 sequencing (partition all incl. llms/comfyui) → Task 3 slices `11`/`12`.
- §9.4 CI mapping (docs drift) → Task 4.

**Placeholder scan:** none — every SQL slice and test file is shown in full; the one "contingency" (explicit grants) is a documented conditional with exact code, not a TODO.

**Type/name consistency:** `seed_harness.run_scripts_and_dump`, `docker_available`, `SCRIPTS_DIR`, `SCHEMA_GOLDEN`, `ROWS_GOLDEN` are defined in Task 1 and used identically in Task 1's tests; the layout lints in Task 2 use the same `EXPECTED_OWNER` slice filenames that Task 3 creates (`10-users.sql`, `11-litellm.sql`, `12-comfyui.sql`, `13-backend-research.sql`, `14-backend-memory.sql`).

**Out of scope (later plans):** Part B (`public.llms` → YAML SoT, consumer repointing, `llm-catalog-init` removal) and Part C (ComfyUI) are separate plans per the spec.
