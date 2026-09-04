"""scheduler (ADR-0010): audit action for pausing/resuming an agent, and the index
the scheduler, the cursor lookup and the alert sweep read agent_runs by.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Closed enum (ADR-0011 §3): a new action is a migration, on purpose.
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'agent_settings_changed'")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_runs_org_agent_started "
        "ON agent_runs (organization_id, agent_key, started_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_org_agent_started")
    # enum values are not removable without a type rebuild; left in place
