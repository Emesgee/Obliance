"""auth: password hash + login timestamps on profiles, role_permissions matrix

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-03

ADR-0024 (auth increment 1) and ADR-0003 (RBAC as data). The matrix rows are
seeded from app.core.access.MATRIX so code and table cannot drift; a test
asserts equality on every run.
"""

from __future__ import annotations

from alembic import op

from app.core.access import seed_rows

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE profiles ADD COLUMN password_hash text")
    op.execute("ALTER TABLE profiles ADD COLUMN email_verified_at timestamptz")
    op.execute("ALTER TABLE profiles ADD COLUMN last_login_at timestamptz")

    op.execute(
        """
        CREATE TABLE role_permissions (
            role        member_role NOT NULL,
            permission  text NOT NULL,
            PRIMARY KEY (role, permission)
        )
        """
    )
    values = ", ".join(f"('{r}', '{p}')" for r, p in seed_rows())
    op.execute(f"INSERT INTO role_permissions (role, permission) VALUES {values}")

    # Read-only reference data for both runtime roles; only migrations change it.
    op.execute("GRANT SELECT ON role_permissions TO obliance_app, obliance_worker")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS role_permissions")
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS last_login_at")
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS email_verified_at")
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS password_hash")
