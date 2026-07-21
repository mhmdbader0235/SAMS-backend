"""UserRepository — user-related database queries for tenant databases."""

from uuid import UUID
import asyncpg


def parse_id(val) -> int | UUID:
    if isinstance(val, (UUID, int)):
        return val
    if not val:
        return val
    if isinstance(val, str):
        if val.isdigit():
            return int(val)
        try:
            return UUID(val)
        except ValueError:
            return val
    return val


class UserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create_user(self, email: str, password_hash: str, role: str) -> int:
        """Insert a new tenant user (school_admin, teacher, parent, student) and return their bigint ID."""
        if role not in ('school_admin', 'teacher', 'parent', 'student', 'manager', 'finance'):
            raise ValueError(f"Invalid tenant user role: {role}")
        return await self.pool.fetchval(
            """
            INSERT INTO users (email, password_hash, role)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            email,
            password_hash,
            role,
        )

    async def get_user_by_email(self, email: str) -> dict | None:
        """Fetch a user record by email, or None if not found."""
        row = await self.pool.fetchrow(
            "SELECT id, email, password_hash, role, created_at FROM users WHERE email = $1",
            email,
        )
        return dict(row) if row else None

    async def get_user_by_id(self, user_id) -> dict | None:
        """Fetch a user record by ID, or None if not found."""
        row = await self.pool.fetchrow(
            "SELECT id, email, role, created_at FROM users WHERE id = $1",
            parse_id(user_id),
        )
        return dict(row) if row else None

    async def get_user_profile(self, user_id) -> dict | None:
        """Fetch tenant user profile fields."""
        row = await self.pool.fetchrow(
            "SELECT email, phone, address FROM users WHERE id = $1",
            parse_id(user_id),
        )
        return dict(row) if row else None

    async def update_user_profile(self, user_id, phone: str | None, address: str | None) -> None:
        """Update tenant user profile fields."""
        await self.pool.execute(
            "UPDATE users SET phone = $1, address = $2 WHERE id = $3",
            phone,
            address,
            parse_id(user_id),
        )
