"""audit log — immutable, tenant-scoped audit trail

Introduces `audit_log` per ADR 0006 (module roadmap Wave A4). Replaces
`_log_audit()` in `app/domains/tenant/service.py`, which only ever printed
to stdout — no table, no persistence, no query surface, despite
`.resourses/AGENTS.md` §5.4 requiring an immutable audit log for sensitive
tables (health records, permission changes, deletions, money state changes).

Deliberately no PII in this table: `actor_email_hmac` (not the raw email)
survives user deletion while still being queryable by email, and
`changed_fields` stores field *names* only, never values. `metadata` is
validated at the application layer (AuditService.record) against a
per-action allowlist of keys before it ever reaches this table -- the
schema alone cannot enforce that, so don't trust the column type as the
safety mechanism.

Immutability is enforced at the application layer for now (no repository
method issues UPDATE/DELETE against this table, proven by
tests/integration/test_audit_immutable.py) -- a real `REVOKE UPDATE, DELETE`
needs a non-superuser app role, which this stack does not have yet (see
ADR 0006's Consequences section). No trigger is used: this codebase has none
anywhere (ADR 0003), and quietly introducing the first one here would be a
bigger decision than it looks.

Run once per tenant schema — see alembic/apply_all_tenants.py, or directly:
    alembic -x target=tenant -x schema=<tenant_id> upgrade tenant@head

Revision ID: tenant_0005
Revises: tenant_0004
Create Date: 2026-09-06
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "tenant_0005"
down_revision = "tenant_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id BIGSERIAL PRIMARY KEY,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            correlation_id UUID NULL,
            actor_user_id BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
            actor_email_hmac TEXT NULL,
            actor_role TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('allow','deny','error')),
            changed_fields TEXT[] NULL,
            metadata JSONB NOT NULL DEFAULT '{}',
            retention_until DATE NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_occurred ON audit_log (occurred_at DESC);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_entity "
        "ON audit_log (entity_type, entity_id, occurred_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log (actor_user_id, occurred_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_log;")
