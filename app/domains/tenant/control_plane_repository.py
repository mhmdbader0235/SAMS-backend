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

    async def update_parent_password(self, parent_id: UUID, password_hash: str) -> None:
        """Overwrite a parent's password hash — the record login_user's parent
        branch actually verifies against, so this is the store a parent's
        self-service password change must write to."""
        await self.pool.execute(
            "UPDATE parents SET password_hash = $1 WHERE id = $2",
            password_hash,
            parent_id,
        )

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

    async def update_super_admin_password(self, super_admin_id: UUID, password_hash: str) -> None:
        """Overwrite a super admin's password hash."""
        await self.pool.execute(
            "UPDATE super_admins SET password_hash = $1 WHERE id = $2",
            password_hash,
            super_admin_id,
        )

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

    async def sync_tenant_mirror(
        self,
        tenant_id: str,
        display_name: str | None,
        school_code: str | None,
        brand_color: str | None,
    ) -> None:
        """Mirror a tenant's school_profile identity fields onto this row
        (alembic cp_0007) so a switcher or cross-tenant lookup never has to
        open that tenant's own schema. COALESCE keeps a field not yet filled
        in (mid-onboarding) from blanking out whatever the mirror already
        has, rather than reflecting every half-finished edit."""
        await self.pool.execute(
            """
            UPDATE tenants
            SET display_name = COALESCE($2, display_name),
                school_code  = COALESCE($3, school_code),
                brand_color  = COALESCE($4, brand_color)
            WHERE tenant_id = $1
            """,
            tenant_id,
            display_name,
            school_code,
            brand_color,
        )

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

    async def get_tenants_for_parent(self, parent_id: UUID) -> list[str]:
        """Every tenant this parent is linked to.

        Parents are control-plane records that legitimately span schools (a parent
        with a child at two schools), so this returns a list. It exists so login can
        resolve a parent's tenant from their existing links instead of falling back
        to a default -- a parent whose tenant could not be resolved used to be
        auto-linked into whatever tenant that default named, which both granted
        access to the wrong school and created a parent row inside it."""
        rows = await self.pool.fetch(
            """
            SELECT tenant_id
            FROM   parent_tenant_links
            WHERE  parent_id = $1
            ORDER BY created_at DESC, tenant_id ASC
            """,
            parent_id,
        )
        return [r["tenant_id"] for r in rows]

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

    async def update_parent_profile(
        self, parent_id: UUID, phone: str | None, address: str | None
    ) -> None:
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
            WHERE UPPER(code) = UPPER($1) OR UPPER(target_email) = UPPER($1) OR id::text = $1
            """,
            code.strip(),
        )
        return dict(row) if row else None

    async def increment_invitation_uses(self, code: str) -> None:
        """Increment uses_count and deactivate if max_uses reached."""
        await self.pool.execute(
            """
            UPDATE invitations
            SET uses_count = uses_count + 1,
                is_active = CASE WHEN uses_count + 1 >= max_uses THEN FALSE ELSE is_active END
            WHERE UPPER(code) = UPPER($1) OR UPPER(target_email) = UPPER($1) OR id::text = $1
            """,
            code.strip(),
        )

    # =========================================================================
    # User-Tenant Mapping (Cross-realm tenant resolution for Keycloak users)
    # =========================================================================
    async def get_tenants_for_email(self, email: str) -> list[dict]:
        """Every (tenant_id, role) membership this email holds.

        This is the honest shape of the data now that user_tenant_map is keyed on
        (email, tenant_id) -- a person can be a teacher at one school and a parent
        at another. Ordered newest-first so callers that must collapse to one row
        do it deterministically."""
        rows = await self.pool.fetch(
            """
            SELECT tenant_id, role
            FROM   user_tenant_map
            WHERE  email = $1
            ORDER BY updated_at DESC, tenant_id ASC
            """,
            email.strip().lower(),
        )
        return [dict(r) for r in rows]

    async def get_tenant_for_email(self, email: str) -> dict | None:
        """The single most-recently-updated membership for this email.

        Kept for callers that still assume one-tenant-per-user. It is now a lossy
        view: a user with memberships at several schools has the others silently
        dropped here, so anything making an access decision should use
        get_tenants_for_email and let the caller choose explicitly.

        The ORDER BY is load-bearing rather than cosmetic -- with the composite key
        this query can match several rows, and the previous unordered fetchrow
        would have returned an arbitrary one, so which school a multi-school user
        landed in could change between two identical requests."""
        rows = await self.get_tenants_for_email(email)
        return rows[0] if rows else None

    async def upsert_user_tenant_map(self, email: str, tenant_id: str, role: str) -> None:
        """Register or update this user's role *within one tenant*.

        Conflicts on (email, tenant_id), so registering the same person against a
        second school ADDS a membership rather than moving them. The old key was
        (email) alone with `SET tenant_id = EXCLUDED.tenant_id`, which silently
        relocated the user out of every school but the newest -- see alembic
        cp_0002. Only `role` is updated on conflict now; re-pointing someone at a
        different tenant is an add plus an explicit remove_user_tenant_map, not a
        side effect of writing their role.

        cp_0004 made `identity_id` NOT NULL on this table, so a brand-new row
        (this email's first membership anywhere) must resolve one via
        get_or_create_identity before the INSERT -- omitting it here fails
        closed with a NOT NULL violation on every first-time SSO login."""
        identity = await self.get_or_create_identity(email)
        await self.pool.execute(
            """
            INSERT INTO user_tenant_map (identity_id, email, tenant_id, role, updated_at)
            VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
            ON CONFLICT (email, tenant_id) DO UPDATE
                SET role       = EXCLUDED.role,
                    updated_at = CURRENT_TIMESTAMP
            """,
            identity["id"],
            email.strip().lower(),
            tenant_id,
            role,
        )
        try:
            from app.core.dependencies import invalidate_membership_cache

            invalidate_membership_cache(email)
        except Exception:
            pass

    async def remove_super_admin(self, email: str) -> bool:
        """Revoke super_admin authority at the control-plane level.

        Deleting a user's tenant-local `users` row (TenantService.delete_tenant_user)
        does NOT touch this table -- `public.super_admins` is what
        AuthService.login_user and get_current_user's super_admin check consult
        FIRST, before any tenant table is ever read. Without this, clicking
        "Delete" on a super_admin in Manage Permissions removes their tenant-
        local mirror row but leaves them able to log in as super_admin exactly
        as before -- the delete looks like it worked and does not.

        Returns True iff a row was actually removed.
        """
        result = await self.pool.execute(
            "DELETE FROM super_admins WHERE UPPER(email) = UPPER($1)",
            email.strip(),
        )
        # asyncpg command tags look like "DELETE 1" / "DELETE 0"
        return result.endswith(" 1")

    async def remove_user_tenant_map(self, email: str, tenant_id: str) -> None:
        """Remove an email -> tenant mapping, scoped to the specific tenant it
        pointed at, so deleting a user doesn't clobber a mapping that was
        re-pointed elsewhere in the meantime."""
        await self.pool.execute(
            "DELETE FROM user_tenant_map WHERE email = $1 AND tenant_id = $2",
            email.strip().lower(),
            tenant_id,
        )

    # =========================================================================
    # Identities (alembic cp_0003+) — the immutable owner every membership,
    # access request, and selection challenge below is keyed on.
    # =========================================================================
    async def get_identity_by_id(self, identity_id) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT id, email, keycloak_user_id, display_name, phone, created_at "
            "FROM identities WHERE id = $1",
            identity_id,
        )
        return dict(row) if row else None

    async def get_identity_by_email(self, email: str) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT id, email, keycloak_user_id, display_name, phone, created_at "
            "FROM identities WHERE email = $1",
            email.strip().lower(),
        )
        return dict(row) if row else None

    async def get_or_create_identity(self, email: str) -> dict:
        """Fetch this email's identity, creating one if this is its first
        appearance anywhere. Every write path that can originate a brand-new
        email (an admin inviting an unknown address, a parent link recorded
        for an email never seen before) needs this rather than assuming
        cp_0003's backfill already covered it."""
        row = await self.pool.fetchrow(
            """
            INSERT INTO identities (email)
            VALUES ($1)
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING id, email, keycloak_user_id, display_name, phone, created_at
            """,
            email.strip().lower(),
        )
        return dict(row)

    # =========================================================================
    # Memberships (user_tenant_map, promoted to a real lifecycle by cp_0004)
    # =========================================================================
    async def get_memberships_for_identity(self, identity_id) -> list[dict]:
        """Every membership this identity holds, any status — the switcher's
        one read (active + invited + suspended, so a pending invitation and a
        suspended school both surface in the same list)."""
        rows = await self.pool.fetch(
            """
            SELECT m.identity_id, m.email, m.tenant_id, m.role, m.roles, m.status,
                   m.invited_by, m.decided_by, m.decided_at, m.decline_reason,
                   m.created_at, m.last_active_at, m.is_default,
                   t.name AS tenant_name, t.display_name, t.brand_color
            FROM   user_tenant_map m
            JOIN   tenants t ON t.tenant_id = m.tenant_id
            WHERE  m.identity_id = $1
            ORDER BY m.is_default DESC, m.last_active_at DESC NULLS LAST, t.tenant_id
            """,
            identity_id,
        )
        return [dict(r) for r in rows]

    async def get_membership(self, identity_id, tenant_id: str) -> dict | None:
        """The per-request authority probe: one primary-key lookup, uncached
        (see core/dependencies.py) — status and roles come from here, never
        from the token, so revocation and demotion take effect on the very
        next request."""
        row = await self.pool.fetchrow(
            "SELECT identity_id, email, tenant_id, role, roles, status, is_default "
            "FROM user_tenant_map WHERE identity_id = $1 AND tenant_id = $2",
            identity_id,
            tenant_id,
        )
        return dict(row) if row else None

    async def create_invited_membership(
        self, identity_id, email: str, tenant_id: str, role: str, invited_by: str
    ) -> None:
        """The admin-initiated grant path (§04 'School invites person'): the
        receiving school decides, so this writes 'invited' directly rather
        than going through access_requests, which is for the reverse
        direction (a person asking a school)."""
        await self.pool.execute(
            """
            INSERT INTO user_tenant_map (identity_id, email, tenant_id, role, roles, status, invited_by)
            VALUES ($1, $2, $3, $4, ARRAY[$4], 'invited', $5)
            ON CONFLICT (identity_id, tenant_id) DO UPDATE
                SET status      = 'invited',
                    role        = EXCLUDED.role,
                    roles       = EXCLUDED.roles,
                    invited_by  = EXCLUDED.invited_by
                WHERE user_tenant_map.status IN ('revoked')
            """,
            identity_id,
            email.strip().lower(),
            tenant_id,
            role,
            invited_by,
        )

    async def accept_membership(self, identity_id, tenant_id: str) -> bool:
        """invited -> active. Only the invitee's own identity may do this —
        enforced by the caller passing their own identity_id, not by anything
        in this query."""
        result = await self.pool.execute(
            "UPDATE user_tenant_map SET status = 'active' "
            "WHERE identity_id = $1 AND tenant_id = $2 AND status = 'invited'",
            identity_id,
            tenant_id,
        )
        return result.endswith(" 1")

    async def decline_membership(self, identity_id, tenant_id: str) -> bool:
        """Declining leaves no trace of a school the person declined — the
        edge is deleted, not marked, so it can be re-offered cleanly later."""
        result = await self.pool.execute(
            "DELETE FROM user_tenant_map WHERE identity_id = $1 AND tenant_id = $2 "
            "AND status = 'invited'",
            identity_id,
            tenant_id,
        )
        return result.endswith(" 1")

    async def set_default_membership(self, identity_id, tenant_id: str) -> bool:
        """'Always start me here'. The unique partial index on (identity_id)
        WHERE is_default guarantees only one school can hold this at a time —
        clearing every other row first is what makes the following INSERT-like
        UPDATE safe under that constraint."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE user_tenant_map SET is_default = FALSE WHERE identity_id = $1",
                identity_id,
            )
            result = await conn.execute(
                "UPDATE user_tenant_map SET is_default = TRUE "
                "WHERE identity_id = $1 AND tenant_id = $2 AND status = 'active'",
                identity_id,
                tenant_id,
            )
        return result.endswith(" 1")

    async def touch_membership_last_active(self, identity_id, tenant_id: str) -> None:
        await self.pool.execute(
            "UPDATE user_tenant_map SET last_active_at = CURRENT_TIMESTAMP "
            "WHERE identity_id = $1 AND tenant_id = $2",
            identity_id,
            tenant_id,
        )

    async def suspend_membership(self, email: str, tenant_id: str) -> bool:
        """Reversible — see restore_membership. Does not touch the
        tenant-local profile row (teachers/students/etc keep their data)."""
        result = await self.pool.execute(
            "UPDATE user_tenant_map SET status = 'suspended' "
            "WHERE email = $1 AND tenant_id = $2 AND status = 'active'",
            email.strip().lower(),
            tenant_id,
        )
        return result.endswith(" 1")

    async def restore_membership(self, email: str, tenant_id: str) -> bool:
        result = await self.pool.execute(
            "UPDATE user_tenant_map SET status = 'active' "
            "WHERE email = $1 AND tenant_id = $2 AND status = 'suspended'",
            email.strip().lower(),
            tenant_id,
        )
        return result.endswith(" 1")

    async def revoke_membership(self, email: str, tenant_id: str) -> bool:
        """Terminal — re-granting means a fresh invitation, not a restore."""
        result = await self.pool.execute(
            "UPDATE user_tenant_map SET status = 'revoked' "
            "WHERE email = $1 AND tenant_id = $2 AND status != 'revoked'",
            email.strip().lower(),
            tenant_id,
        )
        return result.endswith(" 1")

    # =========================================================================
    # Access requests (alembic cp_0005) — a person asking a school, never a
    # membership by itself. Approving writes a real membership; this table
    # never does.
    # =========================================================================
    async def create_access_request(
        self, identity_id, tenant_id: str, requested_role: str, note: str | None
    ) -> dict:
        row = await self.pool.fetchrow(
            """
            INSERT INTO access_requests (identity_id, tenant_id, requested_role, note)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (identity_id, tenant_id) WHERE status = 'pending' DO UPDATE
                SET requested_role = EXCLUDED.requested_role,
                    note           = EXCLUDED.note
            RETURNING id, identity_id, tenant_id, requested_role, note, status, created_at
            """,
            identity_id,
            tenant_id,
            requested_role,
            note,
        )
        return dict(row)

    async def get_access_requests_for_tenant(
        self, tenant_id: str, status: str = "pending"
    ) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT ar.id, ar.identity_id, ar.tenant_id, ar.requested_role, ar.note, ar.status,
                   ar.reject_reason, ar.decided_by, ar.decided_at, ar.created_at,
                   i.email, i.display_name
            FROM   access_requests ar
            JOIN   identities i ON i.id = ar.identity_id
            WHERE  ar.tenant_id = $1 AND ar.status = $2
            ORDER BY ar.created_at DESC
            """,
            tenant_id,
            status,
        )
        return [dict(r) for r in rows]

    async def get_access_request(self, request_id) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT ar.id, ar.identity_id, ar.tenant_id, ar.requested_role, ar.note, ar.status,
                   i.email
            FROM   access_requests ar
            JOIN   identities i ON i.id = ar.identity_id
            WHERE  ar.id = $1
            """,
            request_id,
        )
        return dict(row) if row else None

    async def decide_access_request(
        self, request_id, status: str, decided_by: str, reject_reason: str | None = None
    ) -> bool:
        result = await self.pool.execute(
            """
            UPDATE access_requests
            SET    status = $2, decided_by = $3, decided_at = CURRENT_TIMESTAMP, reject_reason = $4
            WHERE  id = $1 AND status = 'pending'
            """,
            request_id,
            status,
            decided_by,
            reject_reason,
        )
        return result.endswith(" 1")

    # =========================================================================
    # Selection challenges (alembic cp_0006) — single-use, server-side, the
    # step between "password verified against several schools" and "session
    # issued for one of them". See AuthService.login_user / login_redeem.
    # =========================================================================
    async def create_selection_challenge(
        self,
        token_hash: bytes,
        identity_id,
        matched_tenants: list[str],
        expires_at,
        created_ip: str | None,
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO auth_selection_challenges
                (token_hash, identity_id, matched_tenants, expires_at, created_ip)
            VALUES ($1, $2, $3, $4, $5)
            """,
            token_hash,
            identity_id,
            matched_tenants,
            expires_at,
            created_ip,
        )

    async def redeem_selection_challenge(self, token_hash: bytes) -> dict | None:
        """Atomic UPDATE ... RETURNING: a replay (already consumed) or an
        expired row matches zero rows and returns None — there is no
        read-then-write race between two concurrent redemption attempts."""
        row = await self.pool.fetchrow(
            """
            UPDATE auth_selection_challenges
            SET    consumed_at = CURRENT_TIMESTAMP
            WHERE  token_hash = $1 AND consumed_at IS NULL AND expires_at > CURRENT_TIMESTAMP
            RETURNING identity_id, matched_tenants
            """,
            token_hash,
        )
        return dict(row) if row else None

    # =========================================================================
    # Refresh tokens (alembic cp_0008) — rotation with reuse detection.
    # =========================================================================
    async def create_refresh_token(
        self,
        token_hash: bytes,
        identity_id,
        tenant_id: str,
        role: str,
        family_id,
        expires_at,
        created_ip: str | None,
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO refresh_tokens
                (token_hash, identity_id, tenant_id, role, family_id, expires_at, created_ip)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            token_hash,
            identity_id,
            tenant_id,
            role,
            family_id,
            expires_at,
            created_ip,
        )

    async def get_refresh_token(self, token_hash: bytes) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT id, identity_id, tenant_id, role, family_id, revoked_at, expires_at "
            "FROM refresh_tokens WHERE token_hash = $1",
            token_hash,
        )
        return dict(row) if row else None

    async def rotate_refresh_token(self, old_id, new_id) -> None:
        await self.pool.execute(
            "UPDATE refresh_tokens SET revoked_at = CURRENT_TIMESTAMP, replaced_by = $2 WHERE id = $1",
            old_id,
            new_id,
        )

    async def revoke_refresh_token_family(self, family_id) -> None:
        """A presented token that is already revoked is a reuse signal, not
        an expected race — the whole family (every token descended from the
        same original login) is revoked, not just the one presented, since a
        stolen-and-already-used token means the thief and the legitimate
        holder may both still have valid-looking copies."""
        await self.pool.execute(
            "UPDATE refresh_tokens SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE family_id = $1 AND revoked_at IS NULL",
            family_id,
        )
