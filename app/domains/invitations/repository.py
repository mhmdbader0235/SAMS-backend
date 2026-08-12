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
        """Upsert email-to-tenant mapping in control plane DB."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_tenant_map (email, tenant_id, role, updated_at)
                VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
                ON CONFLICT (email) DO UPDATE
                SET tenant_id = EXCLUDED.tenant_id, role = EXCLUDED.role, updated_at = CURRENT_TIMESTAMP
                """,
                email.strip().lower(),
                tenant_id.strip(),
                role.strip(),
            )
