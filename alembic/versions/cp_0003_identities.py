"""identities: one immutable owner per email, gated on human-reviewed collisions

Every membership, access request, and selection challenge this feature adds
needs a stable owner that survives an email change -- keying straight on
`email` would mean re-keying all of them in a later, riskier migration. This
table exists now, before the membership lifecycle work in cp_0004, purely to
give every future row an immutable `identity_id`.

`identities` holds no `password_hash` yet -- credential unification across
`public.parents` / `public.super_admins` / each tenant's own `users` table is
a separate, riskier migration for later. This table's only job is identity.

The backfill assumes one email is one person, which is not always true: a
shared family mailbox, a departmental address, or a reissued account can put
two different humans behind the same address in two different stores. Since
that would silently pool one person's memberships onto a different person's
identity -- unrecoverable once cp_0004 re-keys onto it -- this migration
refuses to run past any email that appears in more than one of
{parents, super_admins, user_tenant_map} without a recorded human decision in
`identity_merge_decisions`. Run `scripts/check_identity_clashes.py` first;
it prints exactly the collisions this gate will otherwise abort on.

Revision ID: cp_0003
Revises: cp_0002
Create Date: 2026-09-08
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cp_0003"
down_revision = "cp_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    # The ledger a human decision gets written into. Created before the gate
    # below so the gate has somewhere to check against on a first run.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS identity_merge_decisions (
            email       CITEXT      PRIMARY KEY,
            decision    TEXT        NOT NULL CHECK (decision IN ('same_person', 'distinct_people')),
            -- Only meaningful for 'distinct_people': which store keeps this
            -- address. The other side must be re-addressed by hand (in its own
            -- table) before this migration is re-run -- this migration does not
            -- and must not guess which human keeps a shared address.
            keeps_email TEXT,
            decided_by  TEXT        NOT NULL,
            note        TEXT,
            decided_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # The gate. Aborts ONLY on a collision nobody has ruled on yet -- a
    # 'same_person' decision lets the merge below proceed for that email, a
    # 'distinct_people' decision is a signal that the operator already
    # re-addressed one side, so by the time this runs again the collision
    # query should no longer find it. This is deliberately not a bare
    # RAISE EXCEPTION with no way to record a decision and proceed -- that
    # was tried, and it deadlocks on exactly the dual-role person (a teacher
    # who is genuinely also a parent elsewhere) this feature exists to serve.
    op.execute(
        """
        DO $$
        DECLARE undecided TEXT;
        BEGIN
            SELECT string_agg(email, ', ') INTO undecided
            FROM (
                SELECT email FROM (
                    SELECT email, 'parent' AS store FROM parents
                    UNION
                    SELECT email, 'super_admin' AS store FROM super_admins
                    UNION
                    -- Excludes role='parent' rows: that is a parent's own
                    -- mirror written on every login, the same identity as
                    -- their `parents` row -- not a second account. See
                    -- scripts/check_identity_clashes.py, which this query
                    -- must stay identical to.
                    SELECT DISTINCT email, 'tenant_member' AS store FROM user_tenant_map
                        WHERE role != 'parent'
                ) by_store
                GROUP BY email
                HAVING COUNT(DISTINCT store) > 1
            ) collisions
            WHERE NOT EXISTS (
                SELECT 1 FROM identity_merge_decisions d WHERE d.email = collisions.email
            );
            IF undecided IS NOT NULL THEN
                RAISE EXCEPTION
                    'cp_0003 aborted: unreviewed email collision(s) across parents/'
                    'super_admins/user_tenant_map: %. Run '
                    'scripts/check_identity_clashes.py, record a decision for each '
                    'in identity_merge_decisions, then re-run this migration.',
                    undecided;
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS identities (
            id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            email            CITEXT      UNIQUE NOT NULL,
            keycloak_user_id TEXT        UNIQUE,
            display_name     TEXT,
            phone            TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # One identity per distinct email across all three of today's account
    # stores. Safe to re-run: ON CONFLICT (email) DO NOTHING.
    op.execute(
        """
        INSERT INTO identities (email)
        SELECT email FROM user_tenant_map
        UNION SELECT email FROM parents
        UNION SELECT email FROM super_admins
        ON CONFLICT (email) DO NOTHING;
        """
    )


def downgrade() -> None:
    # identities has no dependents yet at this revision (cp_0004 is what
    # re-keys user_tenant_map onto it) -- safe to drop outright here.
    op.execute("DROP TABLE IF EXISTS identities;")
    op.execute("DROP TABLE IF EXISTS identity_merge_decisions;")
