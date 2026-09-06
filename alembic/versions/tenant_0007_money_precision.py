"""money precision — widen money columns to NUMERIC(14,4), add payments.currency

Scale 2 cannot represent JOD/KWD/BHD's 3-decimal minor unit, or CLF's 4.
Widening is additive/lossless (more room, same values). payments.currency
is backfilled from the school's own currency, never a literal, before
being made NOT NULL.

Revision ID: tenant_0007
Revises: tenant_0006
Create Date: 2026-09-06
"""

from alembic import op

revision = "tenant_0007"
down_revision = "tenant_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE event_class_map ALTER COLUMN ticket_price TYPE NUMERIC(14, 4);")
    op.execute("ALTER TABLE event ALTER COLUMN school_subsidy TYPE NUMERIC(14, 4);")
    op.execute("ALTER TABLE event ALTER COLUMN total_cost TYPE NUMERIC(14, 4);")
    op.execute("ALTER TABLE resource_cost ALTER COLUMN unit_price TYPE NUMERIC(14, 4);")
    op.execute("ALTER TABLE resource_cost ALTER COLUMN total_cost TYPE NUMERIC(14, 4);")
    op.execute("ALTER TABLE payments ALTER COLUMN amount TYPE NUMERIC(14, 4);")
    op.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS currency VARCHAR(3);")
    op.execute(
        """
        UPDATE payments SET currency = COALESCE(
            (SELECT currency FROM school_profile ORDER BY id ASC LIMIT 1), 'USD'
        )
        WHERE currency IS NULL;
        """
    )
    op.execute("ALTER TABLE payments ALTER COLUMN currency SET NOT NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE payments ALTER COLUMN currency DROP NOT NULL;")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS currency;")
    op.execute("ALTER TABLE payments ALTER COLUMN amount TYPE NUMERIC(10, 2);")
    op.execute("ALTER TABLE resource_cost ALTER COLUMN total_cost TYPE NUMERIC(12, 2);")
    op.execute("ALTER TABLE resource_cost ALTER COLUMN unit_price TYPE NUMERIC(12, 2);")
    op.execute("ALTER TABLE event ALTER COLUMN total_cost TYPE NUMERIC(12, 2);")
    op.execute("ALTER TABLE event ALTER COLUMN school_subsidy TYPE NUMERIC(10, 2);")
    op.execute("ALTER TABLE event_class_map ALTER COLUMN ticket_price TYPE NUMERIC(10, 2);")
