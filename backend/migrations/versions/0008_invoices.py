"""suppliers, price_terms, invoice_sources, invoices, invoice_lines, import_errors

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-04

ADR-0018: inbound only, one model for several sources, fingerprint idempotence,
three-step matching, lines for the price check (ADR-0013 price_deviation).
Suppliers are the minimal subset of ADR-0020 the feed needs (CVR per org) and
are never created by an import. The app role writes; the worker reads.
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
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


def _contract_scope(table: str, nullable: bool) -> None:
    cond = "contract_id IN (SELECT id FROM contracts)"
    if nullable:
        cond = f"(contract_id IS NULL OR {cond})"
    op.execute(
        f"CREATE POLICY contract_scope ON {table} AS RESTRICTIVE USING ({cond}) WITH CHECK ({cond})"
    )


def upgrade() -> None:
    op.execute("CREATE TYPE source_kind AS ENUM ('file_import','sftp_drop','api_pull')")
    op.execute(
        "CREATE TYPE invoice_status AS ENUM ('modtaget','matchet','kontrolleret','godkendt',"
        "'afvist','erstattet')"
    )
    op.execute("CREATE TYPE matched_by AS ENUM ('reference','rule','suggestion','manual')")
    op.execute("CREATE TYPE control_result AS ENUM ('bestaaet','afvigelse','ingen_prisgrundlag')")
    for v in ("invoice_match", "price_term"):
        op.execute(f"ALTER TYPE suggestion_subject ADD VALUE IF NOT EXISTS '{v}'")
    for v in (
        "supplier_created",
        "price_term_created",
        "invoices_imported",
        "invoice_matched",
        "invoice_checked",
        "invoice_approved",
        "invoice_rejected",
    ):
        op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{v}'")

    op.execute(
        """
        CREATE TABLE suppliers (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            cvr              text NOT NULL,
            name             text NOT NULL,
            country          text NOT NULL DEFAULT 'DK',
            created_by       uuid REFERENCES profiles(id) ON DELETE SET NULL,
            created_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (organization_id, cvr)
        )
        """
    )
    _tenant("suppliers")
    op.execute("GRANT SELECT, INSERT, UPDATE ON suppliers TO obliance_app")
    op.execute("GRANT SELECT ON suppliers TO obliance_worker")

    op.execute(
        "ALTER TABLE contracts ADD COLUMN supplier_id uuid REFERENCES suppliers(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX contracts_supplier_idx ON contracts (supplier_id)")

    op.execute(
        """
        CREATE TABLE price_terms (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id        uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            product_ref        text,
            description        text NOT NULL,
            unit               text,
            agreed_unit_price  numeric(14,4) NOT NULL,
            valid_from         date,
            valid_to           date,
            origin             origin_kind NOT NULL,
            suggestion_id      uuid,
            created_by         uuid REFERENCES profiles(id) ON DELETE SET NULL,
            created_at         timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX price_terms_contract_idx ON price_terms (contract_id)")
    _tenant("price_terms")
    _contract_scope("price_terms", nullable=False)
    op.execute("GRANT SELECT, INSERT, UPDATE ON price_terms TO obliance_app")
    op.execute("GRANT SELECT ON price_terms TO obliance_worker")

    op.execute(
        """
        CREATE TABLE invoice_sources (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            kind              source_kind NOT NULL,
            name              text NOT NULL,
            config            jsonb NOT NULL DEFAULT '{}'::jsonb,
            enabled           boolean NOT NULL DEFAULT true,
            last_sync_at      timestamptz,
            last_sync_status  text,
            created_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    _tenant("invoice_sources")
    op.execute("GRANT SELECT, INSERT, UPDATE ON invoice_sources TO obliance_app, obliance_worker")

    op.execute(
        """
        CREATE TABLE invoices (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id           uuid REFERENCES contracts(id) ON DELETE RESTRICT,
            source_id             uuid REFERENCES invoice_sources(id) ON DELETE SET NULL,
            supplier_id           uuid NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
            invoice_number        text NOT NULL,
            invoice_date          date NOT NULL,
            due_date              date,
            currency              text NOT NULL DEFAULT 'DKK',
            total_amount          numeric(14,2) NOT NULL,
            external_ref          text,
            contract_reference    text,
            fingerprint           text NOT NULL,
            status                invoice_status NOT NULL DEFAULT 'modtaget',
            matched_by            matched_by,
            control_result        control_result,
            control_note          text,
            supersedes_invoice_id uuid REFERENCES invoices(id) ON DELETE SET NULL,
            raw_payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
            first_seen_at         timestamptz NOT NULL DEFAULT now(),
            last_seen_at          timestamptz NOT NULL DEFAULT now(),
            decided_by            uuid REFERENCES profiles(id) ON DELETE SET NULL,
            decided_at            timestamptz,
            decision_comment      text,
            UNIQUE (organization_id, fingerprint)
        )
        """
    )
    op.execute("CREATE INDEX invoices_contract_idx ON invoices (contract_id, status)")
    op.execute("CREATE INDEX invoices_supplier_no_idx ON invoices (supplier_id, invoice_number)")
    _tenant("invoices")
    _contract_scope("invoices", nullable=True)
    op.execute("GRANT SELECT, INSERT, UPDATE ON invoices TO obliance_app, obliance_worker")

    op.execute(
        """
        CREATE TABLE invoice_lines (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            invoice_id       uuid NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            contract_id      uuid REFERENCES contracts(id) ON DELETE RESTRICT,
            line_no          integer NOT NULL,
            description      text NOT NULL,
            quantity         numeric(14,4) NOT NULL,
            unit             text,
            unit_price       numeric(14,4) NOT NULL,
            line_total       numeric(14,2) NOT NULL,
            period_from      date,
            period_to        date,
            product_ref      text
        )
        """
    )
    op.execute("CREATE INDEX invoice_lines_invoice_idx ON invoice_lines (invoice_id, line_no)")
    _tenant("invoice_lines")
    _contract_scope("invoice_lines", nullable=True)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON invoice_lines TO obliance_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON invoice_lines TO obliance_worker")

    op.execute(
        """
        CREATE TABLE import_errors (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            source_id        uuid REFERENCES invoice_sources(id) ON DELETE SET NULL,
            file_name        text NOT NULL,
            row_no           integer NOT NULL,
            reason           text NOT NULL,
            raw              jsonb NOT NULL DEFAULT '{}'::jsonb,
            resolved_at      timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    _tenant("import_errors")
    op.execute("GRANT SELECT, INSERT, UPDATE ON import_errors TO obliance_app, obliance_worker")


def downgrade() -> None:
    for t in ("import_errors", "invoice_lines", "invoices", "invoice_sources", "price_terms"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("ALTER TABLE contracts DROP COLUMN IF EXISTS supplier_id")
    op.execute("DROP TABLE IF EXISTS suppliers CASCADE")
    for e in ("control_result", "matched_by", "invoice_status", "source_kind"):
        op.execute(f"DROP TYPE IF EXISTS {e}")
