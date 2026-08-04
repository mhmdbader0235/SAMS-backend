"""ControlPlaneRepository — database queries for the control plane DB."""

from datetime import datetime
from uuid import UUID

import asyncpg



class ControlPlaneRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    # =========================================================================
    # Parents
    # =========================================================================
    async def create_parent(self, email: str, password_hash: str) -> UUID:
        """Insert a new parent and return their UUID."""
        return await self.pool.fetchval(
            """
            INSERT INTO parents (email, password_hash)
            VALUES ($1, $2)
            RETURNING id
            """,
            email,
            password_hash,
        )

    async def get_parent_by_email(self, email: str) -> dict | None:
        """Fetch a parent record by email, or None if not found."""
        row = await self.pool.fetchrow(
            "SELECT id, email, password_hash, created_at, phone, address FROM parents WHERE email = $1",
            email,
        )
        return dict(row) if row else None

    async def get_parent_by_id(self, parent_id: UUID) -> dict | None:
        """Fetch a parent record by UUID, or None if not found."""
        row = await self.pool.fetchrow(
            "SELECT id, email, created_at FROM parents WHERE id = $1",
            parent_id,
        )
        return dict(row) if row else None

    # =========================================================================
    # Super Admins
    # =========================================================================
    async def create_super_admin(self, email: str, password_hash: str) -> UUID:
        """Insert a new super admin and return their UUID."""
        return await self.pool.fetchval(
            """
            INSERT INTO super_admins (email, password_hash)
            VALUES ($1, $2)
            RETURNING id
            """,
            email,
            password_hash,
        )

    async def get_super_admin_by_email(self, email: str) -> dict | None:
        """Fetch a super admin record by email, or None if not found."""
        row = await self.pool.fetchrow(
            "SELECT id, email, password_hash, created_at FROM super_admins WHERE email = $1",
            email,
        )
        return dict(row) if row else None

    # =========================================================================
    # Tenants
    # =========================================================================
    async def get_all_tenants(self) -> list[dict]:
        """Fetch all registered tenants."""
        rows = await self.pool.fetch(
            "SELECT tenant_id, name, db_host, db_port, db_user, db_name, created_at FROM tenants"
        )
        return [dict(row) for row in rows]

    async def create_tenant(
        self,
        tenant_id: str,
        name: str,
        db_host: str = "127.0.0.1",
        db_port: int = 5433,
        db_user: str = "admin",
        db_password: str = "secure_local_password",
        db_name: str = "user_service_db",
    ) -> str:
        """Insert a new tenant record into control plane."""
        await self.pool.execute(
            """
            INSERT INTO tenants (tenant_id, name, db_host, db_port, db_user, db_password, db_name)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (tenant_id) DO UPDATE SET name = EXCLUDED.name
            """,
            tenant_id,
            name,
            db_host,
            db_port,
            db_user,
            db_password,
            db_name,
        )
        return tenant_id



    # =========================================================================
    # Parent-Child Links
    # =========================================================================
    async def create_parent_child_link(
        self, parent_id: UUID, tenant_id: str, student_id: UUID
    ) -> UUID:
        """Link a parent to a student in a specific tenant database."""
        return await self.pool.fetchval(
            """
            INSERT INTO parent_child_links (parent_id, tenant_id, student_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (parent_id, tenant_id, student_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id
            RETURNING id
            """,
            parent_id,
            tenant_id,
            student_id,
        )

    async def get_links_for_parent(self, parent_id: UUID) -> list[dict]:
        """Retrieve all parent-child links for a given parent ID."""
        rows = await self.pool.fetch(
            """
            SELECT id, parent_id, tenant_id, student_id, created_at
            FROM parent_child_links
            WHERE parent_id = $1
            """,
            parent_id,
        )
        return [dict(row) for row in rows]

    async def delete_parent_child_link(
        self, parent_id: UUID, tenant_id: str, student_id: UUID
    ) -> bool:
        """Remove a parent-child link."""
        result = await self.pool.execute(
            """
            DELETE FROM parent_child_links
            WHERE parent_id = $1 AND tenant_id = $2 AND student_id = $3
            """,
            parent_id,
            tenant_id,
            student_id,
        )
        return result == "DELETE 1"

    # =========================================================================
    # Parent-Tenant Links
    # =========================================================================
    async def add_parent_tenant_link(self, parent_id: UUID, tenant_id: str) -> None:
        """Associate a parent with a tenant (school) registration."""
        await self.pool.execute(
            """
            INSERT INTO parent_tenant_links (parent_id, tenant_id)
            VALUES ($1, $2)
            ON CONFLICT (parent_id, tenant_id) DO NOTHING
            """,
            parent_id,
            tenant_id,
        )

    async def check_parent_tenant_link(self, parent_id: UUID, tenant_id: str) -> bool:
        """Check if a parent is registered with a tenant."""
        row = await self.pool.fetchval(
            """
            SELECT 1 FROM parent_tenant_links WHERE parent_id = $1 AND tenant_id = $2
            """,
            parent_id,
            tenant_id,
        )
        return row is not None

    async def get_parent_email_for_student(self, student_id: UUID, tenant_id: str) -> str | None:
        """Retrieve the parent's email for a given student in a tenant."""
        return await self.pool.fetchval(
            """
            SELECT p.email 
            FROM parent_child_links pcl
            JOIN parents p ON pcl.parent_id = p.id
            WHERE pcl.student_id = $1 AND pcl.tenant_id = $2
            """,
            student_id,
            tenant_id,
        )

    async def get_parent_profile(self, parent_id: UUID) -> dict | None:
        """Retrieve parent profile metadata."""
        row = await self.pool.fetchrow(
            "SELECT email, phone, address FROM parents WHERE id = $1",
            parent_id,
        )
        return dict(row) if row else None

    async def update_parent_profile(self, parent_id: UUID, phone: str | None, address: str | None) -> None:
        """Update parent profile metadata."""
        await self.pool.execute(
            "UPDATE parents SET phone = $1, address = $2 WHERE id = $3",
            phone,
            address,
            parent_id,
        )

    # =========================================================================
    # Invitations
    # =========================================================================
    async def create_invitation(
        self,
        code: str,
        tenant_id: str,
        role: str,
        target_email: str | None,
        max_uses: int,
        expires_at: datetime,
        created_by: UUID | None = None,
    ) -> dict:
        """Create a new invitation record."""
        row = await self.pool.fetchrow(
            """
            INSERT INTO invitations (code, tenant_id, role, target_email, max_uses, expires_at, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, code, tenant_id, role, target_email, max_uses, uses_count, expires_at, is_active, created_at
            """,
            code,
            tenant_id,
            role,
            target_email,
            max_uses,
            expires_at,
            created_by,
        )
        return dict(row)

    async def get_invitation_by_code(self, code: str) -> dict | None:
        """Fetch invitation metadata by invite code."""
        row = await self.pool.fetchrow(
            """
            SELECT id, code, tenant_id, role, target_email, max_uses, uses_count, expires_at, is_active, created_at
            FROM invitations
            WHERE code = $1
            """,
            code,
        )
        return dict(row) if row else None

    async def increment_invitation_uses(self, code: str) -> None:
        """Increment uses_count and deactivate if max_uses reached."""
        await self.pool.execute(
            """
            UPDATE invitations
            SET uses_count = uses_count + 1,
                is_active = CASE WHEN uses_count + 1 >= max_uses THEN FALSE ELSE is_active END
            WHERE code = $1
            """,
            code,
        )

    # =========================================================================
    # User-Tenant Mapping (Cross-realm tenant resolution for Keycloak users)
    # =========================================================================
    async def get_tenant_for_email(self, email: str) -> dict | None:
        """Look up which tenant and role a user belongs to by email.
        Used when Keycloak token does not carry a tenant_id claim."""
        row = await self.pool.fetchrow(
            "SELECT tenant_id, role FROM user_tenant_map WHERE email = $1",
            email.strip().lower(),
        )
        return dict(row) if row else None

    async def upsert_user_tenant_map(self, email: str, tenant_id: str, role: str) -> None:
        """Register or update a user's email → tenant_id + role mapping.
        Called at registration and login so Keycloak users can be resolved later."""
        await self.pool.execute(
            """
            INSERT INTO user_tenant_map (email, tenant_id, role, updated_at)
            VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
            ON CONFLICT (email) DO UPDATE
                SET tenant_id  = EXCLUDED.tenant_id,
                    role       = EXCLUDED.role,
                    updated_at = CURRENT_TIMESTAMP
            """,
            email.strip().lower(),
            tenant_id,
            role,
        )
