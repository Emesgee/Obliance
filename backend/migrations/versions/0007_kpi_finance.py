"""kpis, kpi_measurements, penalty_terms, sla_breaches, financial_claims

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-04

ADR-0019 (targets as data, measurements as facts, status derived) and ADR-0013
(clause parameters as data, claims computed in code with their basis stored).
All five are contract children (ADR-0001): tenant_isolation + contract_scope.
The app role writes (the human's act, or the deterministic chain a human's
measurement approval triggers); the worker role reads.
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_ORG = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"
TABLES = ["kpis", "kpi_measurements", "penalty_terms", "sla_breaches", "financial_claims"]


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
    op.execute("CREATE TYPE kpi_unit AS ENUM ('pct','antal','timer','dkk','score')")
    op.execute("CREATE TYPE target_operator AS ENUM ('gte','lte','eq','between')")
    op.execute("CREATE TYPE kpi_period AS ENUM ('maaned','kvartal','halvaar','aar')")
    op.execute(
        "CREATE TYPE measurement_source AS ENUM ('manual','import','document','integration')"
    )
    op.execute(
        "CREATE TYPE term_type AS ENUM ('service_credit_pct_of_fee','service_credit_tiered',"
        "'delivery_penalty_per_week','fixed_penalty_per_breach')"
    )
    op.execute(
        "CREATE TYPE penalty_basis AS ENUM ('maanedligt_driftsvederlag','aarligt_vederlag',"
        "'vaerdi_ikke_leverede_ordrelinjer','maanedens_omsaetning','fast_beloeb')"
    )
    op.execute(
        "CREATE TYPE penalty_time_unit AS ENUM ('maaned','paabegyndt_uge','dag','haendelse')"
    )
    op.execute("CREATE TYPE term_status AS ENUM ('aktiv','kraever_godkendelse')")
    op.execute("CREATE TYPE claim_type AS ENUM ('service_credit','bod','prisafvigelse')")
    op.execute(
        "CREATE TYPE claim_status AS ENUM ('beregnet','afventer_2_signatur','godkendt','fremsat',"
        "'modregnet','betalt','afvist_af_leverandoer','frafaldet')"
    )
    for v in ("kpi", "penalty_term", "kpi_measurement"):
        op.execute(f"ALTER TYPE suggestion_subject ADD VALUE IF NOT EXISTS '{v}'")
    for v in (
        "kpi_created",
        "kpi_updated",
        "measurement_recorded",
        "measurement_superseded",
        "sla_breach_recorded",
        "penalty_term_created",
        "penalty_term_updated",
        "claim_calculated",
        "claim_approved",
        "claim_submitted",
        "claim_status_changed",
    ):
        op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{v}'")

    op.execute(
        """
        CREATE TABLE penalty_terms (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id          uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            seq                  integer NOT NULL,
            name                 text NOT NULL,
            term_type            term_type NOT NULL,
            trigger_description  text,
            applies_to           text,
            rate                 numeric(9,6),
            tiers                jsonb,
            basis                penalty_basis NOT NULL,
            basis_amount         numeric(14,2),
            time_unit            penalty_time_unit NOT NULL,
            cap_rate             numeric(9,6),
            cap_basis            penalty_basis,
            cap_amount           numeric(14,2),
            document_version_id  uuid REFERENCES document_versions(id) ON DELETE SET NULL,
            status               term_status NOT NULL DEFAULT 'aktiv',
            origin               origin_kind NOT NULL,
            suggestion_id        uuid,
            created_by           uuid REFERENCES profiles(id) ON DELETE SET NULL,
            approved_by          uuid REFERENCES profiles(id) ON DELETE SET NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            UNIQUE (contract_id, seq)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE kpis (
            id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id             uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id                 uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            seq                         integer NOT NULL,
            name                        text NOT NULL,
            unit                        kpi_unit NOT NULL,
            target_operator             target_operator NOT NULL,
            target_value                numeric(14,4) NOT NULL,
            target_value_high           numeric(14,4),
            period                      kpi_period NOT NULL,
            warn_band                   numeric(14,4) NOT NULL,
            penalty_term_id             uuid REFERENCES penalty_terms(id) ON DELETE SET NULL,
            measurement_obligation_id   uuid REFERENCES obligations(id) ON DELETE SET NULL,
            active                      boolean NOT NULL DEFAULT true,
            origin                      origin_kind NOT NULL,
            suggestion_id               uuid,
            created_by                  uuid REFERENCES profiles(id) ON DELETE SET NULL,
            approved_by                 uuid REFERENCES profiles(id) ON DELETE SET NULL,
            created_at                  timestamptz NOT NULL DEFAULT now(),
            updated_at                  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (contract_id, seq)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE kpi_measurements (
            id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id             uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id                 uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            kpi_id                      uuid NOT NULL REFERENCES kpis(id) ON DELETE RESTRICT,
            period_start                date NOT NULL,
            period_end                  date NOT NULL,
            value                       numeric(14,4) NOT NULL,
            source_kind                 measurement_source NOT NULL,
            entered_by                  uuid REFERENCES profiles(id) ON DELETE SET NULL,
            approved_by                 uuid REFERENCES profiles(id) ON DELETE SET NULL,
            approved_at                 timestamptz,
            note                        text,
            suggestion_id               uuid,
            supersedes_measurement_id   uuid REFERENCES kpi_measurements(id) ON DELETE SET NULL,
            superseded_by_id            uuid REFERENCES kpi_measurements(id) ON DELETE SET NULL,
            created_at                  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Exactly one live measurement per (kpi, period) — ADR-0019 §2.
    op.execute(
        "CREATE UNIQUE INDEX kpi_measurements_live_idx ON kpi_measurements (kpi_id, period_start) "
        "WHERE superseded_by_id IS NULL"
    )
    op.execute(
        """
        CREATE TABLE sla_breaches (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id      uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            kpi_id           uuid NOT NULL REFERENCES kpis(id) ON DELETE RESTRICT,
            measurement_id   uuid NOT NULL REFERENCES kpi_measurements(id) ON DELETE RESTRICT,
            period_start     date NOT NULL,
            period_end       date NOT NULL,
            target_value     numeric(14,4) NOT NULL,
            actual_value     numeric(14,4) NOT NULL,
            penalty_term_id  uuid REFERENCES penalty_terms(id) ON DELETE SET NULL,
            claim_id         uuid,
            note             text,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE financial_claims (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id          uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            seq                  integer NOT NULL,
            claim_type           claim_type NOT NULL,
            period_start         date,
            period_end           date,
            penalty_term_id      uuid REFERENCES penalty_terms(id) ON DELETE SET NULL,
            breach_id            uuid REFERENCES sla_breaches(id) ON DELETE SET NULL,
            inputs               jsonb NOT NULL,
            formula_version      text NOT NULL,
            basis_text           text NOT NULL,
            amount_uncapped      numeric(14,2) NOT NULL,
            amount               numeric(14,2) NOT NULL,
            cap_applied          boolean NOT NULL DEFAULT false,
            currency             text NOT NULL DEFAULT 'DKK',
            status               claim_status NOT NULL DEFAULT 'beregnet',
            created_by           uuid REFERENCES profiles(id) ON DELETE SET NULL,
            approved_by          uuid REFERENCES profiles(id) ON DELETE SET NULL,
            approved_at          timestamptz,
            second_approved_by   uuid REFERENCES profiles(id) ON DELETE SET NULL,
            second_approved_at   timestamptz,
            submitted_by         uuid REFERENCES profiles(id) ON DELETE SET NULL,
            submitted_at         timestamptz,
            decision_comment     text,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            UNIQUE (contract_id, seq)
        )
        """
    )
    op.execute("CREATE INDEX kpis_contract_idx ON kpis (contract_id)")
    op.execute(
        "CREATE INDEX kpi_measurements_kpi_idx ON kpi_measurements (kpi_id, period_start DESC)"
    )
    op.execute(
        "CREATE INDEX sla_breaches_contract_idx ON sla_breaches (contract_id, period_start DESC)"
    )
    op.execute(
        "CREATE INDEX financial_claims_contract_idx ON financial_claims (contract_id, status)"
    )
    for t in TABLES:
        _policies(t)
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {t} TO obliance_app")
        op.execute(f"GRANT SELECT ON {t} TO obliance_worker")


def downgrade() -> None:
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    for e in (
        "claim_status",
        "claim_type",
        "term_status",
        "penalty_time_unit",
        "penalty_basis",
        "term_type",
        "measurement_source",
        "kpi_period",
        "target_operator",
        "kpi_unit",
    ):
        op.execute(f"DROP TYPE IF EXISTS {e}")
