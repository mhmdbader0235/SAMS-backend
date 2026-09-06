"""locale preferences — per-user language/timezone + profile backfill

Adds per-user locale override columns and backfills school_profile's
timezone/default_language for any tenant that predates the onboarding
wizard collecting them. Additive and backfill-only; no destructive change.

Revision ID: tenant_0006
Revises: tenant_0005
Create Date: 2026-09-06
"""

from alembic import op

revision = "tenant_0006"
down_revision = "tenant_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language TEXT DEFAULT NULL;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_timezone TEXT DEFAULT NULL;")
    op.execute("UPDATE school_profile SET timezone = 'UTC' WHERE timezone IS NULL;")
    op.execute("UPDATE school_profile SET default_language = 'en' WHERE default_language IS NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS preferred_language;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS preferred_timezone;")
