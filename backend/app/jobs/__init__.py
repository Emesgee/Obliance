"""RQ jobs: ingest, agent runs, retention (ADR-0010, ADR-0012). Jobs run in
system tenant context (app.core.rls.tenant(..., system=True)) — the only place
that is allowed."""
