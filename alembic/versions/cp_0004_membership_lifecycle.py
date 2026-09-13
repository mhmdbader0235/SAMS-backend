"""user_tenant_map: promote from a resolution cache to the membership edge

Adds a real lifecycle (`status`) and a per-membership role set (`roles`) on
top of the single `role` column, re-keys the table onto the immutable
`identity_id` from cp_0003, and folds `parent_tenant_links` into the same
edge -- it modelled the identical concept (a person belonging to a school) a
second time, only for parents.

`role` is left in place as a denormalised convenience for the legacy
resolution paths in `dependencies.py` / `AuthService`; nothing new may key on
it after this migration -- `roles` (plural) is the membership's real role set,
and `status` is the only thing revocation/suspension needs to change.

Revision ID: cp_0004
Revises: cp_0003
Create Date: 2026-09-08
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cp_0004"
down_revision = "cp_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE user_tenant_map
            ADD COLUMN IF NOT EXISTS identity_id     UUID REFERENCES identities(id) ON DELETE CASCADE,
            ADD COLUMN IF NOT EXISTS status          TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('invited', 'pending_approval', 'active', 'suspended', 'revoked')),
            ADD COLUMN IF NOT EXISTS roles           TEXT[] NOT NULL DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS invited_by      TEXT,
            ADD COLUMN IF NOT EXISTS decided_by      TEXT,
            ADD COLUMN IF NOT EXISTS decided_at      TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS decline_reason  TEXT,
            ADD COLUMN IF NOT EXISTS created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ADD COLUMN IF NOT EXISTS last_active_at  TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS is_default      BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )

    # Backfill the owner and the per-membership role set. Guarded by
    # `identity_id IS NULL` so a re-run only touches rows a previous partial
    # run missed, never overwriting a roles[] a later step may have grown.
    op.execute(
        """
        UPDATE user_tenant_map m
        SET    identity_id = i.id,
               roles       = CASE WHEN m.roles = '{}' THEN ARRAY[m.role] ELSE m.roles END
        FROM   identities i
        WHERE  i.email = m.email
        AND    m.identity_id IS NULL;
        """
    )

    # Every row must have resolved an identity by now -- cp_0003 backfilled
    # identities from this exact table's emails, so a NULL here means cp_0003
    # was skipped or a row was inserted between the two migrations. Fail
    # loudly rather than silently making identity_id nullable in the PK below.
    op.execute(
        """
        DO $$
        DECLARE missing INTEGER;
        BEGIN
            SELECT COUNT(*) INTO missing FROM user_tenant_map WHERE identity_id IS NULL;
            IF missing > 0 THEN
                RAISE EXCEPTION
                    'cp_0004 aborted: % user_tenant_map row(s) have no matching identity. '
                    'Re-run cp_0003 (or check for rows inserted between cp_0003 and cp_0004).',
                    missing;
            END IF;
        END $$;
        """
    )

    op.execute("ALTER TABLE user_tenant_map ALTER COLUMN identity_id SET NOT NULL;")

    # Re-point the PK from (email, tenant_id) [cp_0002] to (identity_id, tenant_id).
    # Guarded the same way cp_0002 guards its own PK swap, so a re-run is a no-op.
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
                AND    (
                    SELECT array_agg(a.attname::text ORDER BY a.attname::text)
                    FROM   unnest(c.conkey) AS k(attnum)
                    JOIN   pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                ) = ARRAY['email', 'tenant_id']
            ) THEN
                ALTER TABLE user_tenant_map DROP CONSTRAINT user_tenant_map_pkey;
                ALTER TABLE user_tenant_map ADD CONSTRAINT user_tenant_map_pkey
                    PRIMARY KEY (identity_id, tenant_id);
            END IF;
        END $$;
        """
    )

    # (email, tenant_id) stays a real unique index -- the legacy
    # email-keyed resolution paths (AuthService, dependencies.py) are not
    # rewritten in this migration, and this is what keeps their existing
    # queries and upsert_user_tenant_map's ON CONFLICT (email, tenant_id)
    # working unchanged.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_utm_email_tenant ON user_tenant_map (email, tenant_id);"
    )
    # The per-request membership check (dependencies.py) is a PK probe on
    # (identity_id, tenant_id); the admin access-requests/membership queue
    # reads (tenant_id, status).
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_utm_tenant_status ON user_tenant_map (tenant_id, status);"
    )
    # Only one school may be the caller's default.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_utm_one_default ON user_tenant_map (identity_id) "
        "WHERE is_default;"
    )

    # Fold parent_tenant_links into the same edge -- it is the same concept
    # (a person belonging to a school) modelled a second time, only for
    # parents. Left in place and kept in sync for one release rather than
    # dropped outright: AuthService.login_user's parent branch still reads it
    # directly, and rewriting that branch to stop doing so is out of scope
    # for this migration.
    op.execute(
        """
        INSERT INTO user_tenant_map (identity_id, email, tenant_id, role, roles, status)
        SELECT i.id, p.email, l.tenant_id, 'parent', ARRAY['parent'], 'active'
        FROM   parent_tenant_links l
        JOIN   parents p    ON p.id = l.parent_id
        JOIN   identities i ON i.email = p.email
        ON CONFLICT (identity_id, tenant_id) DO NOTHING;
        """
    )


def downgrade() -> None:
    # LOSSY, same tradeoff cp_0002's downgrade documents: status/roles/
    # invitation and default-school state are not representable in the old
    # shape and are discarded. The rows folded in from parent_tenant_links
    # are left in place (parent_tenant_links itself is untouched by this
    # migration and still holds the authoritative parent link), which
    # produces harmless duplicate-looking membership rows rather than data
    # loss -- removing exactly the folded-in subset without a marker column
    # is not safely reconstructable, so this does not attempt it.
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
                AND    (
                    SELECT array_agg(a.attname::text ORDER BY a.attname::text)
                    FROM   unnest(c.conkey) AS k(attnum)
                    JOIN   pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                ) = ARRAY['identity_id', 'tenant_id']
            ) THEN
                ALTER TABLE user_tenant_map DROP CONSTRAINT user_tenant_map_pkey;
                ALTER TABLE user_tenant_map ADD CONSTRAINT user_tenant_map_pkey
                    PRIMARY KEY (email, tenant_id);
            END IF;
        END $$;
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_utm_one_default;")
    op.execute("DROP INDEX IF EXISTS idx_utm_tenant_status;")
    op.execute("DROP INDEX IF EXISTS idx_utm_email_tenant;")
    op.execute(
        """
        ALTER TABLE user_tenant_map
            DROP COLUMN IF EXISTS identity_id,
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS roles,
            DROP COLUMN IF EXISTS invited_by,
            DROP COLUMN IF EXISTS decided_by,
            DROP COLUMN IF EXISTS decided_at,
            DROP COLUMN IF EXISTS decline_reason,
            DROP COLUMN IF EXISTS created_at,
            DROP COLUMN IF EXISTS last_active_at,
            DROP COLUMN IF EXISTS is_default;
        """
    )
