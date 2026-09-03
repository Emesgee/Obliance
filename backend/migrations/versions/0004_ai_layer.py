"""ai layer: audit_log, agent_runs, ai_suggestions, usage_events, agent_settings

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-04

Four tables, four ADRs, one rule: the model writes proposals, humans write the
register (ADR-0004). Rights are the enforcement, not conventions:

  audit_log       ADR-0011  append-only: app/worker get INSERT + SELECT, never UPDATE/DELETE
  agent_runs      ADR-0010  the run trail (drift), separate from the audit log
  ai_suggestions  ADR-0004  the ONLY table an agent may write proposals to
  usage_events    ADR-0014  one row per operation, price frozen at write time
  agent_settings  ADR-0010  on/off per (org, agent)

The worker role gets nothing new on contracts & co. — it already has SELECT only,
so a "direct write to the register" fails in Postgres, not in code review.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
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
    # ---- enums ------------------------------------------------------------------------
    op.execute("CREATE TYPE actor_type AS ENUM ('human','agent','system')")
    op.execute(
        "CREATE TYPE audit_action AS ENUM ("
        "'login','login_failed',"
        "'contract_created','contract_updated','contract_status_changed',"
        "'document_uploaded','document_version_made_current',"
        "'ai_suggestion_created','ai_suggestion_approved','ai_suggestion_rejected',"
        "'ai_suggestion_expired','ai_query','agent_run_completed','agent_run_failed')"
    )
    op.execute("CREATE TYPE suggestion_kind AS ENUM ('create','update')")
    op.execute(
        "CREATE TYPE suggestion_subject AS ENUM ('obligation','risk','raci_entry',"
        "'invoice_finding','contract_intake','sla_breach','task')"
    )
    op.execute(
        "CREATE TYPE suggestion_status AS ENUM ('foreslaaet','afventer_2_signatur',"
        "'godkendt','afvist','foraeldet')"
    )
    op.execute("CREATE TYPE confidence AS ENUM ('hoej','mellem','lav')")
    op.execute("CREATE TYPE agent_run_status AS ENUM ('koerer','ok','fejlet','sprunget_over')")
    op.execute("CREATE TYPE agent_trigger AS ENUM ('schedule','event','manual')")

    # ---- audit_log (ADR-0011) --------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE audit_log (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            occurred_at      timestamptz NOT NULL DEFAULT now(),
            actor_type       actor_type NOT NULL,
            actor_id         uuid,
            actor_label      text NOT NULL,
            actor_role       text,
            action           audit_action NOT NULL,
            object_kind      text NOT NULL,
            object_id        uuid,
            object_label     text NOT NULL DEFAULT '',
            contract_id      uuid REFERENCES contracts(id) ON DELETE RESTRICT,
            details          jsonb NOT NULL DEFAULT '{}'::jsonb,
            request_id       text,
            agent_run_id     uuid,
            prev_hash        text,
            row_hash         text NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX audit_log_org_time_idx ON audit_log (organization_id, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX audit_log_contract_time_idx ON audit_log (contract_id, occurred_at DESC)"
    )
    _tenant("audit_log")
    _contract_scope("audit_log", nullable=True)
    # Append-only is a GRANT (ADR-0011 §1): no UPDATE, no DELETE for app or worker.
    op.execute("GRANT SELECT, INSERT ON audit_log TO obliance_app, obliance_worker")

    # ---- agent_runs (ADR-0010 §3) ----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE agent_runs (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            agent_key            text NOT NULL,
            contract_id          uuid REFERENCES contracts(id) ON DELETE RESTRICT,
            trigger              agent_trigger NOT NULL,
            trigger_ref          text,
            triggered_by         uuid REFERENCES profiles(id) ON DELETE SET NULL,
            status               agent_run_status NOT NULL DEFAULT 'koerer',
            started_at           timestamptz NOT NULL DEFAULT now(),
            finished_at          timestamptz,
            duration_ms          integer,
            contracts_scanned    integer NOT NULL DEFAULT 0,
            suggestions_created  integer NOT NULL DEFAULT 0,
            suggestions_updated  integer NOT NULL DEFAULT 0,
            model                text,
            task                 text,
            input_tokens         integer,
            output_tokens        integer,
            cost_dkk             numeric(12,4),
            batch_id             text,
            error                text,
            error_context        jsonb
        )
        """
    )
    op.execute(
        "CREATE INDEX agent_runs_org_agent_idx ON agent_runs (organization_id, agent_key, started_at DESC)"
    )
    op.execute("CREATE INDEX agent_runs_contract_idx ON agent_runs (contract_id, started_at DESC)")
    _tenant("agent_runs")
    _contract_scope("agent_runs", nullable=True)
    op.execute("GRANT SELECT, INSERT, UPDATE ON agent_runs TO obliance_app, obliance_worker")

    # ---- ai_suggestions (ADR-0004 §1) ------------------------------------------------------
    op.execute(
        """
        CREATE TABLE ai_suggestions (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            contract_id       uuid NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
            agent_key         text NOT NULL,
            agent_run_id      uuid REFERENCES agent_runs(id) ON DELETE SET NULL,
            kind              suggestion_kind NOT NULL,
            subject_kind      suggestion_subject NOT NULL,
            subject_id        uuid,
            payload           jsonb NOT NULL,
            confidence        confidence NOT NULL,
            rationale         text NOT NULL DEFAULT '',
            citations         jsonb NOT NULL DEFAULT '[]'::jsonb,
            amount_dkk        numeric(14,2),
            fingerprint       text NOT NULL,
            status            suggestion_status NOT NULL DEFAULT 'foreslaaet',
            decided_by        uuid REFERENCES profiles(id) ON DELETE SET NULL,
            decided_at        timestamptz,
            decision_comment  text,
            materialized_id   uuid,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # ADR-0004 §4: one OPEN suggestion per fingerprint — a rerun updates, never duplicates.
    op.execute(
        "CREATE UNIQUE INDEX ai_suggestions_open_fingerprint_idx ON ai_suggestions (fingerprint) "
        "WHERE status IN ('foreslaaet','afventer_2_signatur')"
    )
    op.execute("CREATE INDEX ai_suggestions_contract_idx ON ai_suggestions (contract_id, status)")
    op.execute("CREATE INDEX ai_suggestions_fingerprint_idx ON ai_suggestions (fingerprint)")
    _tenant("ai_suggestions")
    _contract_scope("ai_suggestions", nullable=False)
    op.execute("GRANT SELECT, INSERT, UPDATE ON ai_suggestions TO obliance_app, obliance_worker")

    # ---- usage_events (ADR-0014 §1) --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE usage_events (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            occurred_at        timestamptz NOT NULL DEFAULT now(),
            task               text NOT NULL,
            actor_type         actor_type NOT NULL,
            user_id            uuid REFERENCES profiles(id) ON DELETE SET NULL,
            contract_id        uuid REFERENCES contracts(id) ON DELETE RESTRICT,
            agent_run_id       uuid REFERENCES agent_runs(id) ON DELETE SET NULL,
            model              text NOT NULL,
            backend            text NOT NULL,
            input_tokens       integer NOT NULL DEFAULT 0,
            output_tokens      integer NOT NULL DEFAULT 0,
            cache_read_tokens  integer NOT NULL DEFAULT 0,
            cache_write_tokens integer NOT NULL DEFAULT 0,
            batch              boolean NOT NULL DEFAULT false,
            inference_geo      text,
            cost_usd           numeric(12,6),
            cost_dkk           numeric(12,4),
            dkk_per_usd        numeric(8,4)
        )
        """
    )
    op.execute(
        "CREATE INDEX usage_events_org_time_idx ON usage_events (organization_id, occurred_at DESC)"
    )
    _tenant("usage_events")
    _contract_scope("usage_events", nullable=True)
    op.execute("GRANT SELECT, INSERT ON usage_events TO obliance_app, obliance_worker")

    # ---- agent_settings (ADR-0010 §2) ------------------------------------------------------
    op.execute(
        """
        CREATE TABLE agent_settings (
            organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
            agent_key        text NOT NULL,
            enabled          boolean NOT NULL DEFAULT true,
            schedule_override text,
            paused_by        uuid REFERENCES profiles(id) ON DELETE SET NULL,
            paused_at        timestamptz,
            paused_reason    text,
            PRIMARY KEY (organization_id, agent_key)
        )
        """
    )
    _tenant("agent_settings")
    op.execute("GRANT SELECT, INSERT, UPDATE ON agent_settings TO obliance_app")
    op.execute("GRANT SELECT ON agent_settings TO obliance_worker")


def downgrade() -> None:
    for t in ["agent_settings", "usage_events", "ai_suggestions", "agent_runs", "audit_log"]:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    for e in [
        "agent_trigger",
        "agent_run_status",
        "confidence",
        "suggestion_status",
        "suggestion_subject",
        "suggestion_kind",
        "audit_action",
        "actor_type",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {e}")
