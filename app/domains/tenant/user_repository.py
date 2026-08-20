"""UserRepository — user-related database queries for tenant databases."""

from uuid import UUID

import asyncpg


def parse_id(val) -> int | UUID | str:
    """Parse and convert user_id into int, UUID, or original string safely."""
    if isinstance(val, (UUID, int)):
        return val
    if not val:
        return val
    if isinstance(val, str):
        val_str = val.strip()
        if val_str.isdigit():
            return int(val_str)
        try:
            return UUID(val_str)
        except ValueError:
            return val_str
    return val


class UserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create_user(self, email: str, password_hash: str, role: str) -> int:
        """Insert a new tenant user and return their bigint ID."""
        valid_roles = (
            "super_admin",
            "school_admin",
            "admin",
            "teacher",
            "parent",
            "student",
            "manager",
            "event_teacher",
            "pending",
        )
        if role not in valid_roles:
            raise ValueError(f"Invalid tenant user role: {role}")
        try:
            return await self.pool.fetchval(
                """
                INSERT INTO users (email, password_hash, role)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                email.strip().lower(),
                password_hash,
                role,
            )
        except asyncpg.UniqueViolationError as exc:
            raise ValueError("Email already registered") from exc

    async def get_users_by_role(self, role: str) -> list[dict]:
        """Fetch users matching a specific role."""
        rows = await self.pool.fetch(
            "SELECT id, email, role, created_at FROM users WHERE role = $1 ORDER BY created_at DESC",
            role,
        )
        return [dict(row) for row in rows]

    async def update_user_role(self, email: str, new_role: str) -> bool:
        """Update a user's role and roles array."""
        valid_roles = (
            "super_admin",
            "school_admin",
            "admin",
            "teacher",
            "parent",
            "student",
            "manager",
            "event_teacher",
            "pending",
        )
        if new_role not in valid_roles:
            raise ValueError(f"Invalid tenant user role: {new_role}")

        try:
            res = await self.pool.execute(
                "UPDATE users SET role = $1, roles = ARRAY[$1]::TEXT[] WHERE UPPER(email) = UPPER($2)",
                new_role,
                email.strip(),
            )
            return res == "UPDATE 1"
        except Exception:
            # Fallback if 'roles' column doesn't exist
            res = await self.pool.execute(
                "UPDATE users SET role = $1 WHERE UPPER(email) = UPPER($2)", new_role, email.strip()
            )
            return res == "UPDATE 1"

    async def get_user_by_email(self, email: str) -> dict | None:
        """Fetch a user record by email, or None if not found."""
        if not email:
            return None
        try:
            row = await self.pool.fetchrow(
                """
                SELECT id, email, password_hash, role, phone, address,
                       COALESCE(roles, ARRAY[]::TEXT[]) as roles,
                       COALESCE(permissions, ARRAY[]::TEXT[]) as permissions,
                       created_at
                FROM users
                WHERE UPPER(email) = UPPER($1)
                """,
                email.strip(),
            )
            return dict(row) if row else None
        except Exception:
            try:
                row = await self.pool.fetchrow(
                    """
                    SELECT id, email, password_hash, role, phone, address, created_at
                    FROM users
                    WHERE UPPER(email) = UPPER($1)
                    """,
                    email.strip(),
                )
                if row:
                    d = dict(row)
                    d["roles"] = [d["role"]] if d.get("role") else []
                    d["permissions"] = []
                    return d
                return None
            except Exception:
                return None

    async def get_user_by_id(self, user_id) -> dict | None:
        """Fetch a user record by ID, or None if not found."""
        parsed = parse_id(user_id)
        if isinstance(parsed, (int, UUID)):
            try:
                row = await self.pool.fetchrow(
                    """
                    SELECT id, email, role,
                           COALESCE(roles, ARRAY[]::TEXT[]) as roles,
                           COALESCE(permissions, ARRAY[]::TEXT[]) as permissions,
                           created_at
                    FROM users
                    WHERE id = $1
                    """,
                    parsed,
                )
                return dict(row) if row else None
            except Exception:
                try:
                    row = await self.pool.fetchrow(
                        """
                        SELECT id, email, role, created_at
                        FROM users
                        WHERE id = $1
                        """,
                        parsed,
                    )
                    if row:
                        d = dict(row)
                        d["roles"] = [d["role"]] if d.get("role") else []
                        d["permissions"] = []
                        return d
                    return None
                except Exception:
                    return None
        return None

    async def get_user_profile(self, user_id) -> dict | None:
        """Fetch tenant user profile fields by integer/UUID ID."""
        parsed = parse_id(user_id)
        if isinstance(parsed, (int, UUID)):
            try:
                row = await self.pool.fetchrow(
                    """
                    SELECT id, email, role, phone, address,
                           COALESCE(roles, ARRAY[]::TEXT[]) as roles,
                           COALESCE(permissions, ARRAY[]::TEXT[]) as permissions
                    FROM users
                    WHERE id = $1
                    """,
                    parsed,
                )
                return dict(row) if row else None
            except Exception:
                try:
                    row = await self.pool.fetchrow(
                        """
                        SELECT id, email, role, phone, address
                        FROM users
                        WHERE id = $1
                        """,
                        parsed,
                    )
                    if row:
                        d = dict(row)
                        d["roles"] = [d["role"]] if d.get("role") else []
                        d["permissions"] = []
                        return d
                    return None
                except Exception:
                    return None
        return None

    async def get_all_tenant_users(self) -> list[dict]:
        """Fetch all users in the tenant schema with their assigned roles and permissions."""
        try:
            rows = await self.pool.fetch(
                """
                SELECT id, email, role, phone, address,
                       COALESCE(roles, ARRAY[]::TEXT[]) as roles,
                       COALESCE(permissions, ARRAY[]::TEXT[]) as permissions,
                       created_at
                FROM users
                ORDER BY id ASC
                """
            )
            return [dict(r) for r in rows]
        except Exception:
            try:
                rows = await self.pool.fetch(
                    "SELECT id, email, role, phone, address, created_at FROM users ORDER BY id ASC"
                )
                results = []
                for r in rows:
                    d = dict(r)
                    d["roles"] = [d["role"]] if d.get("role") else []
                    d["permissions"] = []
                    results.append(d)
                return results
            except Exception:
                try:
                    rows = await self.pool.fetch(
                        "SELECT id, email, role, created_at FROM users ORDER BY id ASC"
                    )
                    results = []
                    for r in rows:
                        d = dict(r)
                        d["roles"] = [d["role"]] if d.get("role") else []
                        d["permissions"] = []
                        d["phone"] = None
                        d["address"] = None
                        results.append(d)
                    return results
                except Exception as e:
                    print(f"[UserRepository.get_all_tenant_users] Error fetching users: {e}")
                    return []

    async def update_user_roles_and_permissions(
        self, user_id, primary_role: str, roles: list[str], permissions: list[str]
    ) -> dict | None:
        """Update a tenant user's primary role, composite roles, and custom permissions."""
        parsed = parse_id(user_id)
        if primary_role in ("pending", "none", "unassigned"):
            all_roles = [primary_role]
            permissions = []
        else:
            clean_roles = [r for r in roles if r and r not in ("pending", "none", "unassigned")]
            all_roles = list(dict.fromkeys([primary_role] + clean_roles))

        if isinstance(parsed, (int, UUID)):
            row = await self.pool.fetchrow(
                """
                UPDATE users
                SET role = $1, roles = $2, permissions = $3
                WHERE id = $4
                RETURNING id, email, role, roles, permissions, phone, address, created_at
                """,
                primary_role,
                all_roles,
                permissions,
                parsed,
            )
            if row:
                return dict(row)

        # Fallback: if user_id was an email string
        if isinstance(user_id, str) and "@" in user_id:
            row = await self.pool.fetchrow(
                """
                UPDATE users
                SET role = $1, roles = $2, permissions = $3
                WHERE UPPER(email) = UPPER($4)
                RETURNING id, email, role, roles, permissions, phone, address, created_at
                """,
                primary_role,
                all_roles,
                permissions,
                user_id.strip(),
            )
            if row:
                return dict(row)
        return None

    async def update_user_profile(self, user_id, phone: str | None, address: str | None) -> None:
        """Update tenant user profile fields."""
        parsed = parse_id(user_id)
        if isinstance(parsed, (int, UUID)):
            await self.pool.execute(
                "UPDATE users SET phone = $1, address = $2 WHERE id = $3",
                phone,
                address,
                parsed,
            )

    async def delete_user(self, user_id) -> dict | None:
        """Hard-delete a tenant user row and return the deleted record, or
        None if it didn't exist.

        Most dependent records (teachers, parenets, students, event_feedback,
        notifications, student_health_and_records) cascade automatically via
        their own FK definitions. Event manager/finance review references are
        nullable, so they're cleared here first rather than blocking the
        delete. Resource authorship (resources.added_by_user_id,
        resource_cost.set_by_user_id) is intentionally NOT nullable — deleting
        a user who priced or added event resources raises ValueError, so the
        caller gets a clear "reassign these records first" message rather
        than the raw DB error, and that audit trail is never silently lost.
        """
        parsed = parse_id(user_id)
        if not isinstance(parsed, (int, UUID)):
            return None

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT id, email, role,
                           COALESCE(roles, ARRAY[]::TEXT[]) as roles,
                           COALESCE(permissions, ARRAY[]::TEXT[]) as permissions
                    FROM users
                    WHERE id = $1
                    """,
                    parsed,
                )
                if not row:
                    return None

                await conn.execute(
                    "UPDATE event SET manager_reviewer_id = NULL WHERE manager_reviewer_id = $1",
                    parsed,
                )
                await conn.execute(
                    "UPDATE event SET finance_reviewer_id = NULL WHERE finance_reviewer_id = $1",
                    parsed,
                )
                try:
                    await conn.execute("DELETE FROM users WHERE id = $1", parsed)
                except asyncpg.exceptions.ForeignKeyViolationError as exc:
                    raise ValueError(
                        "This user has added or priced event resources and cannot be deleted "
                        "until those records are reassigned to another staff member."
                    ) from exc
                return dict(row)
