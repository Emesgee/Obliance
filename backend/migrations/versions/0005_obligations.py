"""obligations + citations

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-04

`obligations` is the first register table an agent proposes rows for (ADR-0004
`create`), and `citations` is ADR-0005 §1's structured source. Both are contract
children (ADR-0001): tenant_isolation + RESTRICTIVE contract_scope.

Rights: the app role writes both (the approving human's act); the worker role
reads only — an agent that tries to INSERT an obligation fails in Postgres.
Nothing deletes: obligations are closed (status), citations get successors.
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_ORG = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"


def _policies(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (organization_id = {_ORG}) WITH CHECK (organization_id = {_ORG})"
    )
    op.execute(
        f"CREATE POLICY contract_scope ON {table} AS RESTRICTIVE "
        f"USING (contract_id IN (SELECT id FROM contracts)) "
        f"WITH CHECK (contract_id IN (SELECT id FROM contracts))"
    )


def upgrade() -> None:
    op.execute("CREATE TYPE obligation_party AS ENUM ('kunde','leverandoer','begge')")
    op.execute(
        "CREATE TYPE obligation_frequency AS ENUM ('engang','loebende','maanedlig',"
        "'kvartalsvis','halvaarlig','aarlig','ved_haendelse')"
    )
    op.execute("CREATE TYPE criticality AS ENUM ('lav','mellem','hoej','kritisk')")
    op.execute("CREATE TYPE obligation_status AS ENUM ('aaben','opfyldt','lukket')")
    op.execute("CREATE TYPE origin_kind AS ENUM ('human','ai')")
    op.execute("CREATE TYPE citation_kind AS ENUM ('document','record')")
    op.execute("CREATE TYPE successor_status AS ENUM ('uaendret','flyttet','ikke_fundet')")
    for v in (
        "obligation_created",
        "obligation_updated",
        "obligation_status_changed",
        "citations_reresolved",
    ):
        op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{v}'")

    op.execute(
        """
        CREATE TABLE obligations (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id      uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            seq              integer NOT NULL,
            title            text NOT NULL,
            description      text,
            party            obligation_party NOT NULL,
            responsible_id   uuid REFERENCES profiles(id) ON DELETE SET NULL,
            frequency        obligation_frequency NOT NULL,
            deadline         date,
            criticality      criticality NOT NULL,
            status           obligation_status NOT NULL DEFAULT 'aaben',
            consequence      text,
            note             text,
            origin           origin_kind NOT NULL,
            suggestion_id    uuid,
            created_by       uuid REFERENCES profiles(id) ON DELETE SET NULL,
            approved_by      uuid REFERENCES profiles(id) ON DELETE SET NULL,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            fulfilled_at     timestamptz,
            UNIQUE (contract_id, seq)
        )
        """
    )
    op.execute("CREATE INDEX obligations_contract_idx ON obligations (contract_id, status)")
    op.execute("CREATE INDEX obligations_deadline_idx ON obligations (organization_id, deadline)")
    _policies("obligations")
    op.execute("GRANT SELECT, INSERT, UPDATE ON obligations TO obliance_app")
    op.execute("GRANT SELECT ON obligations TO obliance_worker")

    op.execute(
        """
        CREATE TABLE citations (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id          uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            subject_kind         text NOT NULL,
            subject_id           uuid NOT NULL,
            kind                 citation_kind NOT NULL,
            document_id          uuid REFERENCES contract_documents(id) ON DELETE RESTRICT,
            document_version_id  uuid REFERENCES document_versions(id) ON DELETE RESTRICT,
            page_pdf             integer,
            page_printed         text,
            clause_ref           text,
            quote                text,
            quote_hash           text,
            verified             boolean NOT NULL DEFAULT false,
            record_kind          text,
            record_id            uuid,
            label                text NOT NULL DEFAULT '',
            successor_status     successor_status,
            successor_id         uuid REFERENCES citations(id) ON DELETE SET NULL,
            created_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX citations_subject_idx ON citations (subject_kind, subject_id)")
    op.execute("CREATE INDEX citations_version_idx ON citations (document_version_id)")
    _policies("citations")
    op.execute("GRANT SELECT, INSERT, UPDATE ON citations TO obliance_app")
    op.execute("GRANT SELECT ON citations TO obliance_worker")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS citations CASCADE")
    op.execute("DROP TABLE IF EXISTS obligations CASCADE")
    for e in (
        "successor_status",
        "citation_kind",
        "origin_kind",
        "obligation_status",
        "criticality",
        "obligation_frequency",
        "obligation_party",
    ):
        op.execute(f"DROP TYPE IF EXISTS {e}")
    # enum values cannot be removed from audit_action; they stay (harmless)
