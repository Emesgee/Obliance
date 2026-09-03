"""documents: logical documents, immutable versions, page text, clause index

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-03

ADR-0006 (versioning: one gaeldende per document, versions never deleted by the
app role), ADR-0005 (pages + clauses as the substrate for citations), ADR-0002
(all four tables are contract children: tenant_isolation + RESTRICTIVE
contract_scope, so a fortrolig contract's documents are invisible without access).

Immutability is a GRANT, not a convention: obliance_app gets no DELETE on
document_versions and no UPDATE on contract_documents' file columns other than
status/ingest bookkeeping — retention (ADR-0012) runs as its own role.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_ORG = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"

TABLES = ["contract_documents", "document_versions", "document_pages", "document_clauses"]


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
    op.execute(
        "CREATE TYPE doc_type AS ENUM ('hovedkontrakt','bilag','prisbilag','databehandleraftale',"
        "'tillaeg','rapport','korrespondance','andet')"
    )
    op.execute("CREATE TYPE version_status AS ENUM ('kladde','gaeldende','historisk')")
    op.execute("CREATE TYPE ingest_status AS ENUM ('afventer','koerer','ok','fejlet')")

    op.execute(
        """
        CREATE TABLE contract_documents (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id         uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            doc_type            doc_type NOT NULL,
            title               text NOT NULL,
            current_version_id  uuid,
            amends_document_id  uuid REFERENCES contract_documents(id) ON DELETE RESTRICT,
            created_by          uuid REFERENCES profiles(id) ON DELETE SET NULL,
            created_at          timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX contract_documents_contract_idx ON contract_documents (contract_id)")

    op.execute(
        """
        CREATE TABLE document_versions (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id        uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            document_id        uuid NOT NULL REFERENCES contract_documents(id) ON DELETE RESTRICT,
            version_no         integer NOT NULL,
            storage_key        text NOT NULL,
            sha256             text NOT NULL,
            size_bytes         integer NOT NULL,
            mime               text NOT NULL,
            original_filename  text NOT NULL,
            uploaded_by        uuid REFERENCES profiles(id) ON DELETE SET NULL,
            uploaded_at        timestamptz NOT NULL DEFAULT now(),
            status             version_status NOT NULL DEFAULT 'kladde',
            made_current_by    uuid REFERENCES profiles(id) ON DELETE SET NULL,
            made_current_at    timestamptz,
            pdf_storage_key    text,
            ingest_status      ingest_status NOT NULL DEFAULT 'afventer',
            ingest_error       text,
            ocr_applied        boolean NOT NULL DEFAULT false,
            page_count         integer,
            effective_note     text,
            UNIQUE (document_id, version_no),
            UNIQUE (document_id, sha256)
        )
        """
    )
    # Exactly one gaeldende per document (ADR-0006 §1).
    op.execute(
        "CREATE UNIQUE INDEX document_versions_one_current_idx "
        "ON document_versions (document_id) WHERE status = 'gaeldende'"
    )
    op.execute("CREATE INDEX document_versions_contract_idx ON document_versions (contract_id)")
    op.execute(
        "ALTER TABLE contract_documents ADD CONSTRAINT contract_documents_current_fk "
        "FOREIGN KEY (current_version_id) REFERENCES document_versions(id) ON DELETE SET NULL"
    )

    op.execute(
        """
        CREATE TABLE document_pages (
            version_id       uuid NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id      uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            page_pdf         integer NOT NULL,
            page_printed     text,
            text             text NOT NULL,
            PRIMARY KEY (version_id, page_pdf)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE document_clauses (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            version_id       uuid NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id      uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            clause_ref       text NOT NULL,
            heading          text NOT NULL,
            page_pdf         integer NOT NULL,
            char_start       integer NOT NULL,
            char_end         integer NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX document_clauses_version_idx ON document_clauses (version_id, page_pdf)"
    )

    for t in TABLES:
        _policies(t)

    # ---- grants: versions are immutable for the app; pages/clauses are rebuilt on
    # re-ingest (delete+insert); worker may write ingest results only. ----------------
    op.execute("GRANT SELECT, INSERT, UPDATE ON contract_documents TO obliance_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON document_versions TO obliance_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON document_pages, document_clauses TO obliance_app")
    op.execute("GRANT SELECT ON contract_documents TO obliance_worker")
    op.execute("GRANT SELECT, UPDATE ON document_versions TO obliance_worker")
    op.execute(
        "GRANT SELECT, INSERT, DELETE ON document_pages, document_clauses TO obliance_worker"
    )


def downgrade() -> None:
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    for e in ["ingest_status", "version_status", "doc_type"]:
        op.execute(f"DROP TYPE IF EXISTS {e}")
