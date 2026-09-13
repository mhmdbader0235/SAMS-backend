"""refresh_tokens: rotation so the access-token lifetime can actually be short

Reading role/status live from user_tenant_map (dependencies.py) closes most
of the token-lifetime exposure -- a revoked or demoted membership stops
working on the next request regardless of what the token still claims. It
does nothing for an exfiltrated token's remaining validity window, and today
that window is JWT_EXPIRATION_MINUTES=1440 (24h) with no way to shorten it
without forcing a password prompt every time it expires.

A refresh token (opaque, stored hashed, same reasoning as
auth_selection_challenges) lets the access token itself drop to a short
lifetime while a session stays alive. Rotation means each refresh both mints
a new refresh token AND revokes the one just used (`revoked_at` +
`replaced_by`); presenting an already-revoked token is a reuse signal --
AuthService treats it as theft and revokes the whole family
(every token sharing that ancestry), not just the one presented.

Revision ID: cp_0008
Revises: cp_0007
Create Date: 2026-09-08
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cp_0008"
down_revision = "cp_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            token_hash   BYTEA       NOT NULL UNIQUE,
            identity_id  UUID        NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
            -- '' for a super_admin token (mirrors CurrentUser.tenant_id's own
            -- "" convention for a super_admin with no home tenant), a real
            -- tenant_id for everyone else.
            tenant_id    VARCHAR(50) NOT NULL DEFAULT '',
            role         TEXT        NOT NULL,
            -- The first token in a rotation chain points to itself; every
            -- token minted by a refresh points back at its predecessor's
            -- family root, so revoking a family is one indexed update, not a
            -- recursive walk.
            family_id    UUID        NOT NULL,
            revoked_at   TIMESTAMPTZ,
            replaced_by  UUID        REFERENCES refresh_tokens(id),
            expires_at   TIMESTAMPTZ NOT NULL,
            created_ip   INET,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_identity ON refresh_tokens (identity_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_family ON refresh_tokens (family_id) "
        "WHERE revoked_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_tokens;")
