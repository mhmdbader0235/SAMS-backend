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
        valid_roles = ('school_admin', 'teacher', 'parent', 'student', 'manager', 'finance', 'event_teacher')
        if role not in valid_roles:
            raise ValueError(f"Invalid tenant user role: {role}")
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

    async def get_user_by_email(self, email: str) -> dict | None:
        """Fetch a user record by email, or None if not found."""
        if not email:
            return None
        try:
            row = await self.pool.fetchrow(
                """
                SELECT id, email, password_hash, role, 
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
                    SELECT id, email, password_hash, role, created_at 
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
            await self.pool.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS roles TEXT[] DEFAULT '{}';")
            await self.pool.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS permissions TEXT[] DEFAULT '{}';")
            await self.pool.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(50) DEFAULT NULL;")
            await self.pool.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS address TEXT DEFAULT NULL;")
        except Exception:
            pass

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
        if isinstance(parsed, (int, UUID)):
            # Ensure primary_role is in roles
            all_roles = list(dict.fromkeys([primary_role] + [r for r in roles if r]))
            row = await self.pool.fetchrow(
                """
                UPDATE users 
                SET role = $1, roles = $2, permissions = $3
                WHERE id = $4
                RETURNING id, email, role, roles, permissions, phone, created_at
                """,
                primary_role,
                all_roles,
                permissions,
                parsed,
            )
            return dict(row) if row else None
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


