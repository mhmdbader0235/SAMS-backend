"""SchoolRepository — database operations for the Day-1 onboarding tables:
school_profile (singleton), school_campus, school_contact.

STRICT RULE: the only layer permitted to execute raw SQL for this domain.
"""

import asyncpg


class SchoolRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    # =========================================================================
    # Profile (singleton row)
    # =========================================================================
    async def get_profile_row(self) -> dict | None:
        row = await self.pool.fetchrow("SELECT * FROM school_profile ORDER BY id ASC LIMIT 1")
        return dict(row) if row else None

    async def ensure_profile_row(self) -> dict:
        """Guarantee exactly one school_profile row exists and return it."""
        row = await self.get_profile_row()
        if row:
            return row
        row = await self.pool.fetchrow(
            "INSERT INTO school_profile (currency) VALUES ('JOD') RETURNING *"
        )
        return dict(row)

    async def update_profile(self, fields: dict) -> dict:
        """Patch the singleton profile row with the given column -> value fields."""
        await self.ensure_profile_row()
        if not fields:
            return await self.get_profile_row()

        columns = list(fields.keys())
        set_sql = ", ".join(f"{col} = ${i + 1}" for i, col in enumerate(columns))
        values = list(fields.values())
        row = await self.pool.fetchrow(
            f"""
            UPDATE school_profile
            SET {set_sql}, updated_at = CURRENT_TIMESTAMP
            WHERE id = (SELECT id FROM school_profile ORDER BY id ASC LIMIT 1)
            RETURNING *
            """,
            *values,
        )
        return dict(row)

    async def stamp_profile_committed(self) -> None:
        await self.pool.execute(
            """
            UPDATE school_profile
            SET profile_committed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = (SELECT id FROM school_profile ORDER BY id ASC LIMIT 1)
            """
        )

    async def activate(self) -> None:
        await self.pool.execute(
            """
            UPDATE school_profile
            SET activated_at = CURRENT_TIMESTAMP,
                curriculum_locked_at = CURRENT_TIMESTAMP,
                structure_committed_at = COALESCE(structure_committed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = (SELECT id FROM school_profile ORDER BY id ASC LIMIT 1)
            """
        )

    async def grandfather_activate_if_missing(self) -> None:
        """Legacy-tenant support: if the profile row was never activated but the
        tenant already has a real academic structure (predates this feature),
        stamp it live so it never gets dropped into the onboarding wizard."""
        await self.pool.execute(
            """
            UPDATE school_profile
            SET activated_at = CURRENT_TIMESTAMP,
                curriculum_locked_at = CURRENT_TIMESTAMP,
                profile_committed_at = COALESCE(profile_committed_at, CURRENT_TIMESTAMP),
                structure_committed_at = COALESCE(structure_committed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = (SELECT id FROM school_profile ORDER BY id ASC LIMIT 1)
              AND activated_at IS NULL
            """
        )

    # =========================================================================
    # Campus
    # =========================================================================
    async def list_campuses(self) -> list[dict]:
        rows = await self.pool.fetch("SELECT * FROM school_campus ORDER BY is_primary DESC, id ASC")
        return [dict(r) for r in rows]

    async def upsert_primary_campus(self, fields: dict) -> dict:
        """There is exactly one primary campus in this phase — insert it if
        missing, otherwise patch the existing row."""
        existing_id = await self.pool.fetchval(
            "SELECT id FROM school_campus WHERE is_primary = TRUE ORDER BY id ASC LIMIT 1"
        )
        if existing_id:
            if not fields:
                row = await self.pool.fetchrow("SELECT * FROM school_campus WHERE id = $1", existing_id)
                return dict(row)
            columns = list(fields.keys())
            set_sql = ", ".join(f"{col} = ${i + 1}" for i, col in enumerate(columns))
            values = list(fields.values()) + [existing_id]
            row = await self.pool.fetchrow(
                f"UPDATE school_campus SET {set_sql} WHERE id = ${len(values)} RETURNING *",
                *values,
            )
        else:
            columns = list(fields.keys()) + ["is_primary"]
            placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
            values = list(fields.values()) + [True]
            row = await self.pool.fetchrow(
                f"INSERT INTO school_campus ({', '.join(columns)}) VALUES ({placeholders}) RETURNING *",
                *values,
            )
        return dict(row)

    # =========================================================================
    # Contacts
    # =========================================================================
    async def list_contacts(self) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT * FROM school_contact ORDER BY escalation_order ASC NULLS LAST, id ASC"
        )
        return [dict(r) for r in rows]

    async def create_contact(self, fields: dict) -> dict:
        columns = list(fields.keys())
        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
        values = list(fields.values())
        row = await self.pool.fetchrow(
            f"INSERT INTO school_contact ({', '.join(columns)}) VALUES ({placeholders}) RETURNING *",
            *values,
        )
        return dict(row)

    async def update_contact(self, contact_id: int, fields: dict) -> dict | None:
        if not fields:
            row = await self.pool.fetchrow("SELECT * FROM school_contact WHERE id = $1", contact_id)
            return dict(row) if row else None
        columns = list(fields.keys())
        set_sql = ", ".join(f"{col} = ${i + 1}" for i, col in enumerate(columns))
        values = list(fields.values()) + [contact_id]
        row = await self.pool.fetchrow(
            f"UPDATE school_contact SET {set_sql} WHERE id = ${len(values)} RETURNING *",
            *values,
        )
        return dict(row) if row else None

    async def delete_contact(self, contact_id: int) -> None:
        await self.pool.execute("DELETE FROM school_contact WHERE id = $1", contact_id)

    async def has_emergency_contact(self) -> bool:
        return bool(
            await self.pool.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM school_contact
                    WHERE is_emergency_contact = TRUE AND phone IS NOT NULL AND phone <> ''
                )
                """
            )
        )
