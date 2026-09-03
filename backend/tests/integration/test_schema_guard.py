"""Gate G-13 — schema guard (ADR-0002, ADR-0023 §5).

Reads the catalog after migrations and fails if any table that carries customer
data is missing its RLS policy. This is the test that catches bidflow 0074's
class of bug (a table added without RLS) at the migration, not at the leak.

Rules:
  * every table with an organization_id column (except the identity allowlist)
    has RLS enabled, FORCEd, and a PERMISSIVE policy named tenant_isolation
  * every table with a contract_id column (except contracts itself and the
    documented recursion exception contract_access) has a RESTRICTIVE policy
    named contract_scope
  * contracts has a RESTRICTIVE SELECT policy named contract_visibility
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

IDENTITY_TABLES = {"organizations", "organization_members", "profiles"}
CONTRACT_SCOPE_EXCEPTIONS = {"contracts", "contract_access"}


def _tables_with_column(engine: Engine, column: str) -> set[str]:
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema='public' AND column_name=:col"
            ),
            {"col": column},
        )
        return {r[0] for r in rows}


def _rls_flags(engine: Engine, table: str) -> tuple[bool, bool]:
    with engine.connect() as c:
        row = c.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname=:t AND relnamespace='public'::regnamespace"
            ),
            {"t": table},
        ).one()
        return bool(row[0]), bool(row[1])


def _policies(engine: Engine, table: str) -> dict[str, tuple[str, str]]:
    """name -> (permissive|restrictive, cmd)"""
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT policyname, lower(permissive), cmd FROM pg_policies "
                "WHERE schemaname='public' AND tablename=:t"
            ),
            {"t": table},
        )
        return {r[0]: (r[1], r[2]) for r in rows}


def test_every_tenant_table_has_forced_tenant_isolation(migrator_engine: Engine):
    tables = _tables_with_column(migrator_engine, "organization_id") - IDENTITY_TABLES
    assert tables, "no tenant tables found — did migrations run?"
    problems = []
    for t in sorted(tables):
        enabled, forced = _rls_flags(migrator_engine, t)
        pols = _policies(migrator_engine, t)
        if not enabled:
            problems.append(f"{t}: RLS not enabled")
        if not forced:
            problems.append(f"{t}: RLS not FORCEd (owner would bypass it)")
        if pols.get("tenant_isolation", ("", ""))[0] != "permissive":
            problems.append(f"{t}: missing permissive policy tenant_isolation")
    assert not problems, "\n".join(problems)


def test_every_contract_child_has_restrictive_contract_scope(migrator_engine: Engine):
    tables = _tables_with_column(migrator_engine, "contract_id") - CONTRACT_SCOPE_EXCEPTIONS
    problems = []
    for t in sorted(tables):
        pols = _policies(migrator_engine, t)
        kind = pols.get("contract_scope", ("", ""))[0]
        if kind != "restrictive":
            problems.append(
                f"{t}: missing RESTRICTIVE policy contract_scope (found: {kind or 'none'})"
            )
    assert not problems, "\n".join(problems)


def test_contracts_has_restrictive_visibility_policy(migrator_engine: Engine):
    pols = _policies(migrator_engine, "contracts")
    assert pols.get("contract_visibility") == ("restrictive", "SELECT"), pols


def test_identity_tables_have_no_rls(migrator_engine: Engine):
    """Documented exception (bidflow 0004): identity is read before a tenant exists."""
    for t in IDENTITY_TABLES:
        enabled, _ = _rls_flags(migrator_engine, t)
        assert not enabled, f"{t} unexpectedly has RLS — auth would break"


def test_app_role_is_not_superuser_and_cannot_bypass_rls(app_engine: Engine):
    """If this fails, every other RLS test is meaningless."""
    with app_engine.connect() as c:
        row = c.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert row == (False, False), f"app role must be non-superuser without BYPASSRLS, got {row}"
