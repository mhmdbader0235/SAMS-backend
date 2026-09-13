"""access_requests: a membership a person asks for, before a school grants it

A request holds the ask (requested role, note) and the reviewer's decision
without a half-real row sitting in `user_tenant_map` -- mirrors the shape of
the existing manager-decision flow (approve, or reject with a required
reason). Approving writes a real `user_tenant_map` row; rejecting or
withdrawing never does.

Revision ID: cp_0005
Revises: cp_0004
Create Date: 2026-09-08
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cp_0005"
down_revision = "cp_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS access_requests (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            identity_id     UUID        NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
            tenant_id       VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            requested_role  TEXT        NOT NULL,
            note            TEXT,
            status          TEXT        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
            reject_reason   TEXT,
            decided_by      TEXT,
            decided_at      TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # At most one open (pending) request per person per school -- asking
    # twice while the first ask is still pending re-uses it rather than
    # piling up duplicates in the school's queue.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ar_one_open ON access_requests (identity_id, tenant_id) "
        "WHERE status = 'pending';"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ar_queue ON access_requests (tenant_id, status, created_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS access_requests;")
