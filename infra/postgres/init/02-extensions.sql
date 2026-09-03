-- Runs against POSTGRES_DB (obliance) on first init; CI runs it against
-- the test database explicitly. pgvector for the document index (ADR-0002);
-- pg_stat_statements for the admin CLI's DB health view (bidflow ADR-0070).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
