"""raci_activities, raci_assignments, contract_roles, raci_templates, workload_policies, tasks

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-04

ADR-0021: the matrix normalised (one row per filled cell), functions separated
from people (contract_roles), templates as data, workload thresholds as policy.
`tasks` is the ADR-0001 child that gap and workload findings materialise into.
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_ORG = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"


def _tenant(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (organization_id = {_ORG}) WITH CHECK (organization_id = {_ORG})"
    )


def _contract_scope(table: str, nullable: bool = False) -> None:
    cond = "contract_id IN (SELECT id FROM contracts)"
    if nullable:
        cond = f"(contract_id IS NULL OR {cond})"
    op.execute(
        f"CREATE POLICY contract_scope ON {table} AS RESTRICTIVE USING ({cond}) WITH CHECK ({cond})"
    )


TEMPLATES = [
    # key, tiers, forms, name, criticality, assignments
    (
        "sla_followup",
        ["N1", "N2"],
        [],
        "Følge op på leveringsgrad og SLA",
        "hoej",
        {"CM": "A", "BUS": "R", "IT": "C", "LEV": "I"},
    ),
    (
        "penalty",
        ["N1", "N2"],
        [],
        "Beregne og fremsætte bod eller service credit ved leverancesvigt",
        "hoej",
        {"CO": "A", "FIN": "R", "LEGAL": "C", "CM": "C", "LEV": "I"},
    ),
    (
        "invoice_control",
        [],
        [],
        "Fakturakontrol mod prisbilag",
        "mellem",
        {"FIN": "A", "FIN_R": "R"},
    ),
    (
        "renewal",
        ["N1", "N2", "N3"],
        ["rammeaftale", "serviceaftale"],
        "Beslutte forlængelse eller genudbud",
        "hoej",
        {"CO": "A", "PROC": "R", "LEGAL": "C", "CM": "C"},
    ),
    (
        "supplier_meetings",
        [],
        [],
        "Afholde og dokumentere leverandørmøder",
        "mellem",
        {"CM": "A", "CM_R": "R", "LEV": "R", "BUS": "I"},
    ),
    (
        "ai_review",
        [],
        [],
        "Godkende AI-udtræk af forpligtelser og risici",
        "mellem",
        {"CM": "A", "LEGAL": "R", "CO": "I"},
    ),
    (
        "dpa",
        [],
        ["databehandleraftale", "serviceaftale"],
        "Kontrollere databehandleraftale og underdatabehandlere",
        "hoej",
        {"LEGAL": "A", "IT": "R", "CM": "C", "LEV": "I"},
    ),
]


def upgrade() -> None:
    op.execute(
        "CREATE TYPE raci_function AS ENUM ('CM','CO','PROC','LEGAL','FIN','IT','BUS','LEV')"
    )
    op.execute("CREATE TYPE raci_letter AS ENUM ('R','A','C','I')")
    op.execute("CREATE TYPE raci_status AS ENUM ('godkendt','foreslaaet')")
    op.execute("CREATE TYPE task_status AS ENUM ('aaben','igang','lukket')")
    op.execute("CREATE TYPE task_priority AS ENUM ('lav','mellem','hoej')")
    for v in (
        "raci_activity_created",
        "raci_activity_updated",
        "raci_cell_changed",
        "contract_role_assigned",
        "task_created",
        "task_updated",
    ):
        op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{v}'")

    op.execute(
        """
        CREATE TABLE raci_activities (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id      uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            seq              integer NOT NULL,
            name             text NOT NULL,
            criticality      criticality NOT NULL,
            status           raci_status NOT NULL DEFAULT 'godkendt',
            template_key     text,
            origin           origin_kind NOT NULL,
            suggestion_id    uuid,
            created_by       uuid REFERENCES profiles(id) ON DELETE SET NULL,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (contract_id, seq)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE raci_assignments (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id      uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            activity_id      uuid NOT NULL REFERENCES raci_activities(id) ON DELETE CASCADE,
            function         raci_function NOT NULL,
            letter           raci_letter NOT NULL,
            UNIQUE (activity_id, function)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE contract_roles (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id       uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            function          raci_function NOT NULL,
            profile_id        uuid REFERENCES profiles(id) ON DELETE SET NULL,
            supplier_contact  text,
            since             date NOT NULL DEFAULT CURRENT_DATE,
            until             date,
            assigned_by       uuid REFERENCES profiles(id) ON DELETE SET NULL
        )
        """
    )
    # exactly one active person per (contract, function) — ADR-0021 §2
    op.execute(
        "CREATE UNIQUE INDEX contract_roles_active_idx ON contract_roles (contract_id, function) "
        "WHERE until IS NULL"
    )
    op.execute(
        "CREATE INDEX contract_roles_profile_idx ON contract_roles (profile_id) WHERE until IS NULL"
    )
    op.execute(
        """
        CREATE TABLE raci_templates (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid REFERENCES organizations(id) ON DELETE CASCADE,
            key              text NOT NULL,
            tiers            jsonb NOT NULL DEFAULT '[]'::jsonb,
            agreement_forms  jsonb NOT NULL DEFAULT '[]'::jsonb,
            name             text NOT NULL,
            criticality      criticality NOT NULL,
            assignments      jsonb NOT NULL
        )
        """
    )
    # Seed the global templates BEFORE row-level security: FORCE applies to the owner
    # too, and the policy's WITH CHECK refuses NULL organization_id.
    import json

    for key, tiers, forms, name, crit, assignments in TEMPLATES:
        cells = {
            k.replace("_R", ""): v for k, v in assignments.items()
        }  # FIN_R → FIN=R only if no A
        clean = {}
        for k, v in assignments.items():
            fn = k.replace("_R", "")
            if fn in clean and clean[fn] == "A":
                continue
            clean[fn] = v
        op.execute(
            "INSERT INTO raci_templates (organization_id, key, tiers, agreement_forms, name, criticality, assignments) "
            f"VALUES (NULL, '{key}', '{json.dumps(tiers)}'::jsonb, '{json.dumps(forms)}'::jsonb, "
            f"'{name.replace(chr(39), chr(39) * 2)}', '{crit}', '{json.dumps(clean)}'::jsonb)"
        )
        _ = cells

    op.execute(
        """
        CREATE TABLE workload_policies (
            organization_id   uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
            max_weighted      integer NOT NULL DEFAULT 30,
            max_cm_contracts  integer NOT NULL DEFAULT 15,
            tier_weights      jsonb NOT NULL DEFAULT '{"N1": 3, "N2": 2, "N3": 1, "N4": 1}'::jsonb
        )
        """
    )
    op.execute(
        """
        CREATE TABLE tasks (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id      uuid REFERENCES contracts(id) ON DELETE RESTRICT,
            seq              integer NOT NULL,
            title            text NOT NULL,
            description      text,
            responsible_id   uuid REFERENCES profiles(id) ON DELETE SET NULL,
            deadline         date,
            priority         task_priority NOT NULL DEFAULT 'mellem',
            status           task_status NOT NULL DEFAULT 'aaben',
            origin_kind      text,
            origin_ref       text,
            origin           origin_kind NOT NULL,
            suggestion_id    uuid,
            created_by       uuid REFERENCES profiles(id) ON DELETE SET NULL,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            closed_at        timestamptz,
            UNIQUE (organization_id, seq)
        )
        """
    )
    op.execute("CREATE INDEX tasks_contract_idx ON tasks (contract_id, status)")
    op.execute("CREATE INDEX tasks_responsible_idx ON tasks (responsible_id, status)")

    for t in ("raci_activities", "raci_assignments", "contract_roles"):
        _tenant(t)
        _contract_scope(t)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO obliance_app")
        op.execute(f"GRANT SELECT ON {t} TO obliance_worker")
    _tenant("tasks")
    _contract_scope("tasks", nullable=True)
    op.execute("GRANT SELECT, INSERT, UPDATE ON tasks TO obliance_app")
    op.execute("GRANT SELECT ON tasks TO obliance_worker")
    _tenant("workload_policies")
    op.execute("GRANT SELECT, INSERT, UPDATE ON workload_policies TO obliance_app")
    op.execute("GRANT SELECT ON workload_policies TO obliance_worker")
    # templates: global rows (NULL org) are readable by everyone; org rows by the org
    op.execute("ALTER TABLE raci_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE raci_templates FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON raci_templates "
        f"USING (organization_id IS NULL OR organization_id = {_ORG}) "
        f"WITH CHECK (organization_id = {_ORG})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON raci_templates TO obliance_app")
    op.execute("GRANT SELECT ON raci_templates TO obliance_worker")


def downgrade() -> None:
    for t in (
        "tasks",
        "workload_policies",
        "raci_templates",
        "contract_roles",
        "raci_assignments",
        "raci_activities",
    ):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    for e in ("task_priority", "task_status", "raci_status", "raci_letter", "raci_function"):
        op.execute(f"DROP TYPE IF EXISTS {e}")
