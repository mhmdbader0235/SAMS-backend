"""user_tenant_map: one membership per (email, tenant) instead of one per email

`user_tenant_map` was keyed on `email` alone, which made "this person belongs to
this school with this role" a single global fact: one email could map to exactly
one tenant, platform-wide. `upsert_user_tenant_map` was correspondingly written
as `ON CONFLICT (email) DO UPDATE SET tenant_id = EXCLUDED.tenant_id`, so
registering the same person against a second school silently *moved* them out of
the first.

That is the structural blocker for Keycloak-Organizations-backed tenancy, where a
user's org memberships are a set: a parent with children at two schools, or a
teacher who also parents a pupil at another school, has to be representable as two
rows. Widening the key to `(email, tenant_id)` also moves `role` onto the
membership edge — the same person can be a teacher at one school and a parent at
another, which a single global `role` could never express.

`parent_tenant_links` (PK `(parent_id, tenant_id)`) already models exactly this
shape for parents only; this generalises it to every user.

Revision ID: cp_0002
Revises: cp_0001
Create Date: 2026-08-31
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cp_0002"
down_revision = "cp_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No dedup needed on the way up: the old PRIMARY KEY (email) guaranteed at
    # most one row per email, so every existing row is already unique under the
    # wider key. Guarded so a re-run is a no-op rather than a duplicate-key error.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM   pg_constraint c
                JOIN   pg_class t ON t.oid = c.conrelid
                JOIN   pg_namespace n ON n.oid = t.relnamespace
                WHERE  c.conname  = 'user_tenant_map_pkey'
                AND    t.relname  = 'user_tenant_map'
                AND    n.nspname  = current_schema()
                AND    c.contype  = 'p'
                AND    (
                    -- attname is `name`, not `text`; without the cast this
                    -- comparison is "operator does not exist: name[] = text[]".
                    SELECT array_agg(a.attname::text ORDER BY a.attname::text)
                    FROM   unnest(c.conkey) AS k(attnum)
                    JOIN   pg_attribute a
                      ON   a.attrelid = c.conrelid AND a.attnum = k.attnum
                ) = ARRAY['email']
            ) THEN
                ALTER TABLE user_tenant_map DROP CONSTRAINT user_tenant_map_pkey;
                ALTER TABLE user_tenant_map ADD CONSTRAINT user_tenant_map_pkey
                    PRIMARY KEY (email, tenant_id);
            END IF;
        END $$;
        """
    )

    # Resolving "which schools does this email belong to" is now the hot path in
    # tenant resolution, and it reads by email alone. The composite PK's index is
    # usable for that (email is its leading column), so no separate index is added.


def downgrade() -> None:
    # LOSSY. Narrowing back to PRIMARY KEY (email) cannot keep more than one
    # membership per person, so any user who genuinely belongs to several schools
    # loses all but their most recently updated membership. There is no way to
    # avoid that while restoring the old key; it is recorded here rather than
    # hidden because the rows are not recoverable afterwards.
    op.execute(
        """
        DELETE FROM user_tenant_map u
        WHERE  EXISTS (
            SELECT 1 FROM user_tenant_map keep
            WHERE  keep.email = u.email
            AND    (keep.updated_at, keep.tenant_id) > (u.updated_at, u.tenant_id)
        );
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM   pg_constraint c
                JOIN   pg_class t ON t.oid = c.conrelid
                JOIN   pg_namespace n ON n.oid = t.relnamespace
                WHERE  c.conname = 'user_tenant_map_pkey'
                AND    t.relname = 'user_tenant_map'
                AND    n.nspname = current_schema()
                AND    c.contype = 'p'
                AND    array_length(c.conkey, 1) = 2
            ) THEN
                ALTER TABLE user_tenant_map DROP CONSTRAINT user_tenant_map_pkey;
                ALTER TABLE user_tenant_map ADD CONSTRAINT user_tenant_map_pkey
                    PRIMARY KEY (email);
            END IF;
        END $$;
        """
    )
