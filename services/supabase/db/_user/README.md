# User Supabase Migrations

This directory is the downstream-owned Supabase SQL slot for local projects,
submodule consumers, and forks that need to layer database objects on top of
Atlas without editing Atlas-owned files in `../scripts/`.

`supabase-db-init` mounts this directory at `/user-scripts` and runs `*.sql`
files from it after all Atlas-owned scripts have completed successfully.
Execution is deterministic lexical order, so prefix files with numbers such as
`10-my-schema.sql` and `20-seed-reference-data.sql`.

Write SQL to be idempotent. Prefer `CREATE SCHEMA IF NOT EXISTS`, `CREATE TABLE
IF NOT EXISTS`, guarded `ALTER TABLE`, and conflict-safe seed statements. A
failing user SQL file stops `supabase-db-init`, which also prevents downstream
services that depend on `supabase-db-init` from starting against a partially
prepared database.

This upstream directory ignores local SQL files by default. Downstream projects
that intentionally version their own migrations can either force-add files here
or mount their own replacement directory in their consumer compose layer.
