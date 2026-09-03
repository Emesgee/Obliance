"""foundation: identity tables, contracts aggregate, RLS on two levels, role grants

Revision ID: 0001
Revises:
Create Date: 2026-09-03

Implements:
  ADR-0001  contracts as the aggregate root (phase + status, stable reference,
            structured governance, DKK-only money, budgets as a side table)
  ADR-0002  RLS level 1 (tenant_isolation, FORCE) on every table carrying
            organization_id; level 2 (contract_visibility, RESTRICTIVE) on
            contracts; children inherit via a RESTRICTIVE contract_scope policy
  ADR-0003  member_role enum (one role per membership)
  ADR-0023  grants for the non-superuser roles obliance_app / obliance_worker

Why the level-2 policies are RESTRICTIVE: Postgres ORs permissive policies
together, so a permissive visibility policy would be bypassed by the permissive
tenant policy. RESTRICTIVE policies are ANDed on top — that is the only way to
narrow what tenant_isolation already allows.

Why contract_access has NO contract_scope policy: contract_visibility's EXISTS
reads contract_access; a policy on contract_access that reads contracts would
recurse ("infinite recursion detected in policy"). contract_access is org-scoped
only, which is acceptable — it reveals who has access, not what the contract says.

Why INSERT of a fortrolig contract needs owner/manager set (or system context):
INSERT ... RETURNING evaluates SELECT policies on the returned row. A creator who
is neither owner nor manager and has no contract_access row would get a policy
violation on RETURNING. Services set owner_id/manager_id at creation, or run in
system context (ADR-0002 §Systemkontekst).
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# A custom GUC reverts to '' (empty string, NOT NULL) once it has been set in a
# session, so NULLIF(..., '') is required — otherwise ''::uuid errors (bidflow 0004).
_ORG = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"
_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_ROLE = "current_setting('app.current_role', true)"
_SYSTEM = "COALESCE(current_setting('app.current_user_id', true), '') = ''"

# Tables that carry organization_id and therefore get tenant_isolation + FORCE.
# Identity tables (organizations, profiles, organization_members) are excluded on
# purpose — they are read before a tenant context exists (bidflow 0004).
TENANT_TABLES = ["contracts", "contract_budgets", "contract_access"]

# Children of the contract aggregate: inherit visibility from contracts.
# contract_access is deliberately NOT here (recursion — see module docstring).
CONTRACT_CHILDREN = ["contract_budgets"]


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (organization_id = {_ORG}) WITH CHECK (organization_id = {_ORG})"
    )


def _child_policy(table: str) -> None:
    # RESTRICTIVE: ANDed with tenant_isolation. The subquery on contracts is itself
    # RLS-filtered for the same role, so children inherit level 2 for free.
    op.execute(
        f"CREATE POLICY contract_scope ON {table} AS RESTRICTIVE "
        f"USING (contract_id IN (SELECT id FROM contracts)) "
        f"WITH CHECK (contract_id IN (SELECT id FROM contracts))"
    )


def upgrade() -> None:
    # ---- enums -------------------------------------------------------------
    op.execute(
        "CREATE TYPE member_role AS ENUM ("
        "'systemadministrator','contract_manager','contract_owner','procurement_manager',"
        "'legal_compliance','finance_controller','business_user','auditor')"
    )
    op.execute(
        "CREATE TYPE contract_phase AS ENUM ("
        "'forberedelse','udbud','evaluering','kontrahering','aktiv_drift','genudbud_exit')"
    )
    op.execute(
        "CREATE TYPE contract_status AS ENUM ('kladde','aktiv','udloebet','opsagt','arkiveret')"
    )
    op.execute(
        "CREATE TYPE agreement_form AS ENUM ("
        "'serviceaftale','rammeaftale','leveringsaftale','databehandleraftale','andet')"
    )
    op.execute("CREATE TYPE contract_tier AS ENUM ('N1','N2','N3','N4')")
    op.execute("CREATE TYPE confidentiality AS ENUM ('intern','fortrolig')")
    op.execute("CREATE TYPE risk_level AS ENUM ('lav','mellem','hoej')")

    # ---- identity (no RLS) -------------------------------------------------
    op.execute(
        """
        CREATE TABLE organizations (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name        text NOT NULL,
            slug        text NOT NULL UNIQUE,
            created_at  timestamptz NOT NULL DEFAULT now(),
            deleted_at  timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE profiles (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email           text NOT NULL,
            name            text NOT NULL,
            title           text,
            deactivated_at  timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX profiles_email_lower_idx ON profiles (lower(email))")
    op.execute(
        """
        CREATE TABLE organization_members (
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            profile_id      uuid NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
            role            member_role NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (organization_id, profile_id)
        )
        """
    )

    # ---- contracts aggregate root (ADR-0001) --------------------------------
    op.execute(
        """
        CREATE TABLE contracts (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            reference             text NOT NULL,
            contract_number       text,
            name                  text NOT NULL,
            description           text,
            agreement_form        agreement_form,
            category              text,
            department            text,
            phase                 contract_phase NOT NULL DEFAULT 'forberedelse',
            status                contract_status NOT NULL DEFAULT 'kladde',
            tier                  contract_tier,
            confidentiality       confidentiality NOT NULL DEFAULT 'intern',
            risk_level            risk_level,
            governance_meetings   jsonb NOT NULL DEFAULT '[]'::jsonb,
            governance_note       text,
            owner_id              uuid REFERENCES profiles(id) ON DELETE SET NULL,
            manager_id            uuid REFERENCES profiles(id) ON DELETE SET NULL,
            start_date            date,
            end_date              date,
            notice_period         interval,
            last_termination_date date,
            options               jsonb NOT NULL DEFAULT '[]'::jsonb,
            price_regulation      text,
            price_regulation_date date,
            total_value           numeric(14,2),
            annual_value          numeric(14,2),
            created_by            uuid REFERENCES profiles(id) ON DELETE SET NULL,
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now(),
            UNIQUE (organization_id, reference)
        )
        """
    )
    op.execute("CREATE INDEX contracts_org_phase_idx ON contracts (organization_id, phase)")
    op.execute("CREATE INDEX contracts_org_status_idx ON contracts (organization_id, status)")

    op.execute(
        """
        CREATE TABLE contract_budgets (
            contract_id     uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            year            integer NOT NULL,
            budget          numeric(14,2) NOT NULL,
            PRIMARY KEY (contract_id, year)
        )
        """
    )

    # ---- level 2: access list for fortrolig contracts (ADR-0002) -------------
    op.execute(
        """
        CREATE TABLE contract_access (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id     uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            profile_id      uuid NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
            granted_by      uuid REFERENCES profiles(id) ON DELETE SET NULL,
            granted_at      timestamptz NOT NULL DEFAULT now(),
            reason          text,
            revoked_at      timestamptz
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX contract_access_active_idx "
        "ON contract_access (contract_id, profile_id) WHERE revoked_at IS NULL"
    )
    op.execute("CREATE INDEX contract_access_contract_idx ON contract_access (contract_id)")

    # ---- RLS level 1: every table with organization_id ------------------------
    for t in TENANT_TABLES:
        _tenant_policy(t)

    # ---- RLS level 2: confidentiality on contracts (RESTRICTIVE, SELECT) -----
    op.execute(
        f"""
        CREATE POLICY contract_visibility ON contracts AS RESTRICTIVE FOR SELECT USING (
            confidentiality = 'intern'
            OR {_SYSTEM}
            OR {_ROLE} = 'auditor'
            OR contracts.owner_id = {_USER}
            OR contracts.manager_id = {_USER}
            OR EXISTS (
                SELECT 1 FROM contract_access a
                WHERE a.contract_id = contracts.id
                  AND a.profile_id = {_USER}
                  AND a.revoked_at IS NULL
            )
        )
        """
    )
    for t in CONTRACT_CHILDREN:
        _child_policy(t)

    # ---- grants (ADR-0023): app role reads/writes; worker reads registers -----
    op.execute("GRANT USAGE ON SCHEMA public TO obliance_app, obliance_worker")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON organizations, profiles, organization_members, "
        "contracts, contract_budgets, contract_access TO obliance_app"
    )
    op.execute(
        "GRANT SELECT ON organizations, profiles, organization_members, "
        "contracts, contract_budgets, contract_access TO obliance_worker"
    )


def downgrade() -> None:
    for t in [
        "contract_access",
        "contract_budgets",
        "contracts",
        "organization_members",
        "profiles",
        "organizations",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    for e in [
        "risk_level",
        "confidentiality",
        "contract_tier",
        "agreement_form",
        "contract_status",
        "contract_phase",
        "member_role",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {e}")
