-- Obliance — one-time setup for a NATIVE PostgreSQL (no Docker), e.g. the local
-- PostgreSQL 18 on Windows. Run ONCE as the postgres superuser:
--
--   psql -U postgres -h localhost -f infra/postgres/native-setup.sql
--
-- Creates the same three non-superuser roles as the Docker init (01-roles.sql) —
-- RLS is silently void for superusers and BYPASSRLS roles (ADR-0002, bidflow 0004),
-- so the app must never connect as postgres. Idempotent: safe to re-run.
--
-- Dev passwords equal the role names; they never leave this machine.

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'obliance_migrator') THEN
    CREATE ROLE obliance_migrator LOGIN PASSWORD 'obliance_migrator' NOSUPERUSER NOBYPASSRLS NOCREATEROLE CREATEDB;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'obliance_app') THEN
    CREATE ROLE obliance_app LOGIN PASSWORD 'obliance_app' NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'obliance_worker') THEN
    CREATE ROLE obliance_worker LOGIN PASSWORD 'obliance_worker' NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
  END IF;
END
$$;

-- Databases: app + test, both owned by the migrator (schema owner, runs Alembic).
SELECT 'CREATE DATABASE obliance OWNER obliance_migrator'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'obliance') \gexec
SELECT 'CREATE DATABASE obliance_test OWNER obliance_migrator'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'obliance_test') \gexec

-- Per-database: schema owner, USAGE for the two runtime roles, extensions.
-- pgvector (ADR-0002) is created only if the extension is installed on this
-- server; the first migration does not need it yet.
\connect obliance
ALTER SCHEMA public OWNER TO obliance_migrator;
GRANT USAGE ON SCHEMA public TO obliance_app, obliance_worker;
SELECT 'CREATE EXTENSION IF NOT EXISTS vector'
WHERE EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') \gexec

\connect obliance_test
ALTER SCHEMA public OWNER TO obliance_migrator;
GRANT USAGE ON SCHEMA public TO obliance_app, obliance_worker;
SELECT 'CREATE EXTENSION IF NOT EXISTS vector'
WHERE EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') \gexec

\echo 'obliance: roles + databases ready (obliance, obliance_test)'
