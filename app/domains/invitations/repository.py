"""
Invitation Repository Layer.

Handles raw SQL database queries against PostgreSQL via asyncpg for user_invitations audit logging
and control plane user-tenant mappings. Contains zero business logic or HTTP/Keycloak code.
"""

import asyncpg


class InvitationRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create_invitation_record(
        self,
        email: str,
        tenant_id: str,
        role: str,
        inviter_id: str | None = None,
    ) -> dict:
        """Insert a new pending invitation audit log into user_invitations."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO user_invitations (email, tenant_id, role, inviter_id, status)
                VALUES ($1, $2, $3, $4, 'pending')
                RETURNING id, email, tenant_id, role, inviter_id, status, created_at
                """,
                email.strip().lower(),
                tenant_id.strip(),
                role.strip(),
                inviter_id,
            )
            return dict(row)

    async def get_invitation_by_email(
        self,
        email: str,
        tenant_id: str,
    ) -> dict | None:
        """Query pending invitation for a specific email and tenant."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, email, tenant_id, role, inviter_id, status, created_at
                FROM user_invitations
                WHERE email = $1 AND tenant_id = $2 AND status = 'pending'
                """,
                email.strip().lower(),
                tenant_id.strip(),
            )
            return dict(row) if row else None

    async def upsert_user_tenant_map(
        self,
        email: str,
        tenant_id: str,
        role: str,
    ) -> None:
        """Upsert this user's role within one tenant, in the control plane DB.

        Conflicts on (email, tenant_id) -- inviting someone to a second school
        adds a membership instead of moving them out of the first. Mirrors
        ControlPlaneRepository.upsert_user_tenant_map; see alembic cp_0002.

        cp_0004 made `identity_id` NOT NULL on this table, so an invitee never
        seen before needs an `identities` row resolved (or created) before the
        INSERT below -- otherwise a brand-new invitation fails NOT NULL."""
        async with self.pool.acquire() as conn:
            identity_id = await conn.fetchval(
                """
                INSERT INTO identities (email)
                VALUES ($1)
                ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
                RETURNING id
                """,
                email.strip().lower(),
            )
            await conn.execute(
                """
                INSERT INTO user_tenant_map (identity_id, email, tenant_id, role, updated_at)
                VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
                ON CONFLICT (email, tenant_id) DO UPDATE
                SET role = EXCLUDED.role, updated_at = CURRENT_TIMESTAMP
                """,
                identity_id,
                email.strip().lower(),
                tenant_id.strip(),
                role.strip(),
            )
