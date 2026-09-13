"""auth_selection_challenges: the school-selection step between password and session

When a password verifies against more than one candidate school
(AuthService.login_user's match-all path), the response cannot be a plain
token -- there is more than one school it could name. It also cannot be a
second stateless JWT: intercepted, a signed token stays redeemable for its
whole expiry window, letting one interception open a session at every school
it names. Making the intermediate artifact a server-side row gets single-use
for free (an atomic `UPDATE ... RETURNING` on `consumed_at`) and makes it
revocable besides.

The token itself is only ever stored hashed -- for the few minutes it lives,
it is momentarily equivalent to a password, and the database is not a place
to keep one of those in the clear.

Revision ID: cp_0006
Revises: cp_0005
Create Date: 2026-09-08
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cp_0006"
down_revision = "cp_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_selection_challenges (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            token_hash      BYTEA       NOT NULL UNIQUE,
            identity_id     UUID        NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
            -- ONLY the schools whose own password hash verified the submitted
            -- password. Never the caller's full membership set -- that is
            -- exactly the first-match-wins escalation this table exists to
            -- close (a password compromised at School A must not redeem a
            -- session at School B, whose hash it never matched).
            matched_tenants TEXT[]      NOT NULL,
            consumed_at     TIMESTAMPTZ,
            expires_at      TIMESTAMPTZ NOT NULL,
            created_ip      INET,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_asc_identity ON auth_selection_challenges (identity_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth_selection_challenges;")
