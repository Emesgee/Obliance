"""risks

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-04

The mockup's Risiko (`titel, kategori, sandsynlighed 1–5, konsekvens 1–5, status,
ansvarlig, deadline, afvaergelse, kilde`) as an ADR-0001 child. Score and level
are derived (sandsynlighed × konsekvens), not stored. Same rights as
obligations: the app writes (the approving human), the worker reads.
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_ORG = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        "CREATE TYPE risk_category AS ENUM ('operationel','gdpr','kommerciel','udbudsretlig',"
        "'compliance','juridisk','leverandoer','andet')"
    )
    op.execute("CREATE TYPE risk_status AS ENUM ('aaben','under_haandtering','lukket')")
    for v in ("risk_created", "risk_updated", "risk_status_changed"):
        op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{v}'")

    op.execute(
        """
        CREATE TABLE risks (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id      uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            seq              integer NOT NULL,
            title            text NOT NULL,
            description      text,
            category         risk_category NOT NULL,
            probability      integer NOT NULL CHECK (probability BETWEEN 1 AND 5),
            consequence      integer NOT NULL CHECK (consequence BETWEEN 1 AND 5),
            status           risk_status NOT NULL DEFAULT 'aaben',
            responsible_id   uuid REFERENCES profiles(id) ON DELETE SET NULL,
            deadline         date,
            mitigation       text,
            note             text,
            origin           origin_kind NOT NULL,
            suggestion_id    uuid,
            created_by       uuid REFERENCES profiles(id) ON DELETE SET NULL,
            approved_by      uuid REFERENCES profiles(id) ON DELETE SET NULL,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            closed_at        timestamptz,
            UNIQUE (contract_id, seq)
        )
        """
    )
    op.execute("CREATE INDEX risks_contract_idx ON risks (contract_id, status)")
    op.execute("ALTER TABLE risks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE risks FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON risks "
        f"USING (organization_id = {_ORG}) WITH CHECK (organization_id = {_ORG})"
    )
    op.execute(
        "CREATE POLICY contract_scope ON risks AS RESTRICTIVE "
        "USING (contract_id IN (SELECT id FROM contracts)) "
        "WITH CHECK (contract_id IN (SELECT id FROM contracts))"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON risks TO obliance_app")
    op.execute("GRANT SELECT ON risks TO obliance_worker")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS risks CASCADE")
    op.execute("DROP TYPE IF EXISTS risk_status")
    op.execute("DROP TYPE IF EXISTS risk_category")
