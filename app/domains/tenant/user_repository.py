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
        row = await self.pool.fetchrow(
            "SELECT id, email, password_hash, role, created_at FROM users WHERE UPPER(email) = UPPER($1)",
            email.strip(),
        )
        return dict(row) if row else None

    async def get_user_by_id(self, user_id) -> dict | None:
        """Fetch a user record by ID, or None if not found."""
        parsed = parse_id(user_id)
        if isinstance(parsed, (int, UUID)):
            row = await self.pool.fetchrow(
                "SELECT id, email, role, created_at FROM users WHERE id = $1",
                parsed,
            )
            return dict(row) if row else None
        return None

    async def get_user_profile(self, user_id) -> dict | None:
        """Fetch tenant user profile fields by integer/UUID ID."""
        parsed = parse_id(user_id)
        if isinstance(parsed, (int, UUID)):
            row = await self.pool.fetchrow(
                "SELECT id, email, role, phone, address FROM users WHERE id = $1",
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

