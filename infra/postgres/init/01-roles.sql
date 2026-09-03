-- Runs ONCE on first init of an empty data volume (docker-entrypoint-initdb.d),
-- connected as the postgres superuser to POSTGRES_DB. Also run by CI against the
-- service container. Idempotent.
--
-- Three roles, none superuser (ADR-0002, ADR-0023 — bidflow 0004: "superusers
-- and BYPASSRLS roles ignore RLS; if the app role is one, isolation silently
-- does nothing"):
--   obliance_migrator  owns the schema; runs Alembic. FORCE RLS applies to it too.
--   obliance_app       the API. SELECT/INSERT/UPDATE/DELETE per migration grants.
--   obliance_worker    agents. SELECT on registers; writes only to ai_suggestions /
--                agent_runs once those exist (ADR-0004, gate G-07).
--
-- Passwords: dev defaults equal the role name. In production rotate them with
--   ALTER ROLE obliance_app PASSWORD '...';  (see infra/README.md) — never commit them.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'obliance_migrator') THEN
    CREATE ROLE obliance_migrator LOGIN PASSWORD 'obliance_migrator' NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'obliance_app') THEN
    CREATE ROLE obliance_app LOGIN PASSWORD 'obliance_app' NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'obliance_worker') THEN
    CREATE ROLE obliance_worker LOGIN PASSWORD 'obliance_worker' NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
  END IF;
END
$$;

-- The application database (created by POSTGRES_DB) is owned by the migrator.
ALTER DATABASE obliance OWNER TO obliance_migrator;
ALTER SCHEMA public OWNER TO obliance_migrator;
GRANT USAGE ON SCHEMA public TO obliance_app, obliance_worker;

-- Test database for the local suite (CI creates its own).
SELECT 'CREATE DATABASE obliance_test OWNER obliance_migrator'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'obliance_test') \gexec
\connect obliance_test
ALTER SCHEMA public OWNER TO obliance_migrator;
GRANT USAGE ON SCHEMA public TO obliance_app, obliance_worker;
CREATE EXTENSION IF NOT EXISTS vector;
