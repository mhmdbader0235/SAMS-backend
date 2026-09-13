"""tenants: mirror school_code/display_name/brand_color from each tenant's profile

`school_code` already exists on the tenant-schema `school_profile` table and
is permanently immutable after activation, but a code-lookup or a switcher
needs a school's name and colour without opening that tenant's own schema --
control-plane code must never join across tenant schemas. Mirroring these
three fields onto `public.tenants` lets a lookup stay control-plane-only.

Schema only in this migration. Backfilling real values, and keeping them in
sync going forward, is `SchoolService`'s job (wired in the same change that
adds the write path) -- a NULL `school_code` here just means that tenant's
mirror has not been synced yet, which is harmless until something reads it.

Revision ID: cp_0007
Revises: cp_0006
Create Date: 2026-09-08
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cp_0007"
down_revision = "cp_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS school_code   CITEXT UNIQUE,
            ADD COLUMN IF NOT EXISTS display_name  TEXT,
            ADD COLUMN IF NOT EXISTS brand_color   TEXT;
        """
    )
    # `name` (the tenant_id-derived label set at provisioning time, e.g.
    # "Tenant A") is not the school's real name -- seed it as a starting
    # point so display_name is never blank, but SchoolService's sync
    # overwrites this the next time that tenant's profile is read or saved.
    op.execute("UPDATE tenants SET display_name = name WHERE display_name IS NULL;")


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenants
            DROP COLUMN IF EXISTS school_code,
            DROP COLUMN IF EXISTS display_name,
            DROP COLUMN IF EXISTS brand_color;
        """
    )
