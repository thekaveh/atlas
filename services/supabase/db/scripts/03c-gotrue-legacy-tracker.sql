-- 03c-gotrue-legacy-tracker.sql
-- Owner: supabase-auth (GoTrue). Issue #1825.
--
-- GoTrue's tracker is an unqualified schema_migrations, so the connecting role's
-- search_path picks the table. Pre-#991 volumes: GoTrue connected as supabase_admin
-- (search_path "$user", public, ...), so the history is in public.schema_migrations.
-- Since #991 it connects as supabase_auth_admin (search_path auth), so it reads
-- auth.schema_migrations, which on those volumes is shorter. GoTrue then re-runs
-- applied migrations and crash-loops on 20250731150234_add_oauth_clients_table.
--
-- Fix: when public.schema_migrations holds GoTrue history, copy into
-- auth.schema_migrations each GoTrue v2.191.0 version that public holds. The
-- VALUES list is the image's embedded migration set, tracked in
-- services/supabase/db/gotrue-v2.191.0-migrations.txt. The test
-- bootstrapper/tests/test_supabase_db_init_runner.py checks both against the image.
-- Versions are compared as numbers: GoTrue's "00" is stored as 0 in public.
--
-- The four versions 03b seeds into public are not legacy history: on a fresh
-- volume, GoTrue as supabase_auth_admin applies them. Without the gate below, a
-- fresh volume would copy them and skip them, changing the auth schema. Realtime
-- rows share the table; only the versions in the list below are copied.
-- Idempotent. Runs before supabase-auth starts (compose depends_on).
WITH gotrue(version) AS (VALUES
  ('00'), ('20210710035447'), ('20210722035447'), ('20210730183235'), ('20210909172000'), ('20210927181326'), ('20211122151130'), ('20211124214934'),
  ('20211202183645'), ('20220114185221'), ('20220114185340'), ('20220224000811'), ('20220323170000'), ('20220429102000'), ('20220531120530'), ('20220614074223'),
  ('20220811173540'), ('20221003041349'), ('20221003041400'), ('20221011041400'), ('20221020193600'), ('20221021073300'), ('20221021082433'), ('20221027105023'),
  ('20221114143122'), ('20221114143410'), ('20221125140132'), ('20221208132122'), ('20221215195500'), ('20221215195800'), ('20221215195900'), ('20230116124310'),
  ('20230116124412'), ('20230131181311'), ('20230322519590'), ('20230402418590'), ('20230411005111'), ('20230508135423'), ('20230523124323'), ('20230818113222'),
  ('20230914180801'), ('20231027141322'), ('20231114161723'), ('20231117164230'), ('20240115144230'), ('20240214120130'), ('20240306115329'), ('20240314092811'),
  ('20240427152123'), ('20240612123726'), ('20240729123726'), ('20240802193726'), ('20240806073726'), ('20241009103726'), ('20250717082212'), ('20250731150234'),
  ('20250804100000'), ('20250901200500'), ('20250903112500'), ('20250904133000'), ('20250925093508'), ('20251007112900'), ('20251104100000'), ('20251111201300'),
  ('20251201000000'), ('20260115000000'), ('20260121000000'), ('20260219120000'), ('20260302000000')
),
seeded(version) AS (VALUES ('20221208132122'), ('20221215195500'), ('20221215195800'), ('20221215195900')),
legacy AS (
  SELECT 1
  FROM gotrue g JOIN public.schema_migrations p ON p.version::numeric = g.version::numeric
  WHERE g.version NOT IN (SELECT version FROM seeded)
)
INSERT INTO auth.schema_migrations (version)
SELECT g.version
FROM gotrue g JOIN public.schema_migrations p ON p.version::numeric = g.version::numeric
WHERE EXISTS (SELECT 1 FROM legacy)
ON CONFLICT (version) DO NOTHING;
