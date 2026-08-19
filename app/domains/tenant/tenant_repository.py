"""TenantRepository — database operations for tenant (school) databases."""

from datetime import datetime
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


class TenantRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    # =========================================================================
    # Levels
    # =========================================================================
    async def create_level(
        self,
        name: str,
        isced_level: int = 1,
        age_band_min: int = 6,
        age_band_max: int = 7,
        ordinal: int = 1,
    ) -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO levels (name, isced_level, age_band_min, age_band_max, ordinal)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING level_id
            """,
            name,
            isced_level,
            age_band_min,
            age_band_max,
            ordinal,
        )

    async def get_level_by_name(self, name: str) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT level_id, name FROM levels WHERE LOWER(name) = LOWER($1)",
            name.strip()
        )
        return dict(row) if row else None

    async def get_level_by_id(self, level_id: int) -> dict | None:
        row = await self.pool.fetchrow("SELECT level_id, name FROM levels WHERE level_id = $1", parse_id(level_id))
        return dict(row) if row else None

    async def get_all_levels(self) -> list[dict]:
        rows = await self.pool.fetch("SELECT level_id, name FROM levels ORDER BY name ASC")
        return [dict(row) for row in rows]

    async def save_academic_structure(self, payload: dict) -> None:
        """
        Saves or updates the school academic structure:
        - Curriculums / Levels
        - Sections / Classes with capacities
        - Academic Settings (year, start month, weekend days)
        - Blackout Dates / Holidays
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Ensure table schema columns exist
                await conn.execute("ALTER TABLE levels ADD COLUMN IF NOT EXISTS isced_level INTEGER;")
                await conn.execute("ALTER TABLE levels ADD COLUMN IF NOT EXISTS age_band_min INTEGER;")
                await conn.execute("ALTER TABLE levels ADD COLUMN IF NOT EXISTS age_band_max INTEGER;")
                await conn.execute("ALTER TABLE levels ADD COLUMN IF NOT EXISTS ordinal INTEGER;")
                await conn.execute("ALTER TABLE levels ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;")
                await conn.execute("ALTER TABLE class ADD COLUMN IF NOT EXISTS capacity INTEGER NOT NULL DEFAULT 25;")
                
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS academic_settings (
                        id BIGSERIAL PRIMARY KEY,
                        system TEXT DEFAULT 'US',
                        academic_year TEXT NOT NULL,
                        start_month INTEGER NOT NULL,
                        weekend_days TEXT[] NOT NULL DEFAULT '{}',
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                await conn.execute("ALTER TABLE academic_settings ADD COLUMN IF NOT EXISTS system TEXT DEFAULT 'US';")
                
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS blackout_dates (
                        id BIGSERIAL PRIMARY KEY,
                        date DATE NOT NULL,
                        title TEXT NOT NULL,
                        tags TEXT[] NOT NULL DEFAULT '{}',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                # 2. Save Academic Settings
                system_val = payload.get("system") or "US"
                cal = payload.get("calendar") or {}
                acad_year = cal.get("academic_year") or "2026-2027"
                start_month = int(cal.get("start_month") or 9)
                weekend_days = cal.get("weekend_days") or ["Saturday", "Sunday"]

                existing_settings = await conn.fetchval("SELECT id FROM academic_settings LIMIT 1")
                if existing_settings:
                    await conn.execute(
                        """
                        UPDATE academic_settings
                        SET system = $1, academic_year = $2, start_month = $3, weekend_days = $4, updated_at = CURRENT_TIMESTAMP
                        WHERE id = $5
                        """,
                        system_val, acad_year, start_month, weekend_days, existing_settings
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO academic_settings (system, academic_year, start_month, weekend_days)
                        VALUES ($1, $2, $3, $4)
                        """,
                        system_val, acad_year, start_month, weekend_days
                    )

                # 3. Save Blackout Dates
                blackout_dates = payload.get("blackout_dates") or []
                await conn.execute("DELETE FROM blackout_dates")
                for bd in blackout_dates:
                    d_val = bd.get("date")
                    if d_val:
                        if isinstance(d_val, str):
                            d_parsed = datetime.strptime(d_val[:10], "%Y-%m-%d").date()
                        else:
                            d_parsed = d_val
                        await conn.execute(
                            "INSERT INTO blackout_dates (date, title, tags) VALUES ($1, $2, $3)",
                            d_parsed, bd.get("title") or "Holiday", bd.get("tags") or []
                        )

                # 4. Upsert Levels and Classes / Sections
                levels = payload.get("levels") or []
                default_teacher_id = await conn.fetchval("SELECT id FROM teachers LIMIT 1")
                if default_teacher_id is None:
                    t_uid = await conn.fetchval("SELECT id FROM users WHERE role = 'teacher' LIMIT 1")
                    if t_uid is None:
                        t_uid = await conn.fetchval(
                            "INSERT INTO users (email, role, password_hash) VALUES ('admin.teacher@school.com', 'teacher', 'managed') RETURNING id"
                        )
                    default_teacher_id = await conn.fetchval(
                        "INSERT INTO teachers (id, name) VALUES ($1, 'Lead Teacher') ON CONFLICT DO NOTHING RETURNING id",
                        t_uid
                    ) or t_uid

                for lvl in levels:
                    lvl_name = lvl.get("name") or "Grade"
                    ordinal = lvl.get("ordinal")
                    isced = lvl.get("isced_level")
                    age_min = lvl.get("age_band_min")
                    age_max = lvl.get("age_band_max")
                    is_active = bool(lvl.get("is_active", True))

                    existing_lvl_id = None
                    if ordinal is not None:
                        existing_lvl_id = await conn.fetchval("SELECT level_id FROM levels WHERE ordinal = $1", ordinal)
                    if not existing_lvl_id:
                        existing_lvl_id = await conn.fetchval("SELECT level_id FROM levels WHERE LOWER(name) = LOWER($1)", lvl_name.strip())

                    if existing_lvl_id:
                        await conn.execute(
                            """
                            UPDATE levels
                            SET name = $1, isced_level = $2, age_band_min = $3, age_band_max = $4, ordinal = $5, is_active = $6
                            WHERE level_id = $7
                            """,
                            lvl_name, isced, age_min, age_max, ordinal, is_active, existing_lvl_id
                        )
                        lvl_id = existing_lvl_id
                    else:
                        lvl_id = await conn.fetchval(
                            """
                            INSERT INTO levels (name, isced_level, age_band_min, age_band_max, ordinal, is_active)
                            VALUES ($1, $2, $3, $4, $5, $6)
                            RETURNING level_id
                            """,
                            lvl_name, isced, age_min, age_max, ordinal, is_active
                        )

                    sections = lvl.get("sections") or []
                    for sec in sections:
                        sec_name = sec.get("name") or f"{lvl_name} - A"
                        sec_cap = int(sec.get("capacity") or 25)

                        existing_class_id = await conn.fetchval(
                            "SELECT id FROM class WHERE level_id = $1 AND LOWER(name) = LOWER($2)",
                            lvl_id, sec_name.strip()
                        )
                        if existing_class_id:
                            await conn.execute(
                                "UPDATE class SET capacity = $1 WHERE id = $2",
                                sec_cap, existing_class_id
                            )
                        else:
                            await conn.execute(
                                """
                                INSERT INTO class (name, level_id, capacity, head_teacher_id)
                                VALUES ($1, $2, $3, $4)
                                """,
                                sec_name, lvl_id, sec_cap, default_teacher_id
                            )

    async def get_academic_structure(self) -> dict:
        """Fetch complete saved academic structure & calendar for tenant."""
        try:
            await self.pool.execute("ALTER TABLE levels ADD COLUMN IF NOT EXISTS isced_level INTEGER;")
            await self.pool.execute("ALTER TABLE levels ADD COLUMN IF NOT EXISTS age_band_min INTEGER;")
            await self.pool.execute("ALTER TABLE levels ADD COLUMN IF NOT EXISTS age_band_max INTEGER;")
            await self.pool.execute("ALTER TABLE levels ADD COLUMN IF NOT EXISTS ordinal INTEGER;")
            await self.pool.execute("ALTER TABLE levels ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;")
            await self.pool.execute("ALTER TABLE class ADD COLUMN IF NOT EXISTS capacity INTEGER NOT NULL DEFAULT 25;")
            await self.pool.execute("ALTER TABLE academic_settings ADD COLUMN IF NOT EXISTS system TEXT DEFAULT 'US';")
        except Exception:
            pass

        # 1. Academic Settings
        settings_row = await self.pool.fetchrow("SELECT system, academic_year, start_month, weekend_days FROM academic_settings ORDER BY id DESC LIMIT 1")
        if settings_row:
            system = settings_row["system"] or "US"
            calendar = {
                "academic_year": settings_row["academic_year"],
                "start_month": settings_row["start_month"],
                "weekend_days": settings_row["weekend_days"] or ["Saturday", "Sunday"]
            }
        else:
            system = "US"
            calendar = {
                "academic_year": "2026-2027",
                "start_month": 9,
                "weekend_days": ["Saturday", "Sunday"]
            }

        # 2. Blackout Dates
        bd_rows = await self.pool.fetch("SELECT date, title, tags FROM blackout_dates ORDER BY date ASC")
        blackout_dates = [
            {
                "date": str(r["date"]),
                "title": r["title"],
                "tags": r["tags"] or []
            }
            for r in bd_rows
        ]

        # 3. Levels and Classes
        lvl_rows = await self.pool.fetch("SELECT level_id, name, isced_level, age_band_min, age_band_max, ordinal, is_active FROM levels ORDER BY COALESCE(ordinal, 999), level_id ASC")
        levels = []
        for lr in lvl_rows:
            c_rows = await self.pool.fetch("SELECT id, name, capacity FROM class WHERE level_id = $1 ORDER BY name ASC", lr["level_id"])
            sections = [
                {
                    "id": cr["id"],
                    "name": cr["name"],
                    "capacity": cr.get("capacity") or 25
                }
                for cr in c_rows
            ]
            levels.append({
                "level_id": lr["level_id"],
                "name": lr["name"],
                "isced_level": lr.get("isced_level") or 1,
                "age_band_min": lr.get("age_band_min"),
                "age_band_max": lr.get("age_band_max"),
                "ordinal": lr.get("ordinal"),
                "is_active": lr.get("is_active", True),
                "sections": sections
            })

        # A saved calendar alone (settings_row) does not mean the school has a
        # real academic structure — it must have at least one active level
        # with at least one class section under it.
        has_structure = len(levels) > 0 and any(len(l["sections"]) > 0 for l in levels)
        return {
            "has_structure": has_structure,
            "system": system,
            "calendar": calendar,
            "blackout_dates": blackout_dates,
            "levels": levels
        }

    # =========================================================================
    # Teachers
    # =========================================================================
    async def create_teacher(self, user_id, name: str) -> int:
        u_id = parse_id(user_id)
        await self.pool.execute(
            "INSERT INTO teachers (id, name) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
            u_id,
            name,
        )
        return u_id

    async def get_teacher_by_id(self, teacher_id) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT t.id, t.name, u.email
            FROM teachers t
            JOIN users u ON t.id = u.id
            WHERE t.id = $1
            """,
            parse_id(teacher_id),
        )
        return dict(row) if row else None

    async def get_all_teachers(self) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT t.id, t.name, u.email
            FROM teachers t
            JOIN users u ON t.id = u.id
            ORDER BY t.name ASC
            """
        )
        return [dict(row) for row in rows]

    # =========================================================================
    # Parents
    # =========================================================================
    async def create_parent(self, user_id, name: str, phone: str | None = None) -> int:
        u_id = parse_id(user_id)
        await self.pool.execute(
            "INSERT INTO parenets (id, name, phone) VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, phone = EXCLUDED.phone",
            u_id,
            name,
            phone,
        )
        return u_id

    async def get_parent_by_id(self, parent_id) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT p.id, p.name, u.phone, u.email
            FROM parenets p
            JOIN users u ON p.id = u.id
            WHERE p.id = $1
            """,
            parse_id(parent_id),
        )
        return dict(row) if row else None

    async def get_parent_by_email(self, email: str) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT p.id, p.name, u.phone, u.email
            FROM parenets p
            JOIN users u ON p.id = u.id
            WHERE u.email = $1
            """,
            email,
        )
        return dict(row) if row else None

    async def get_all_parents(self) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT p.id, p.name, u.phone, u.email
            FROM parenets p
            JOIN users u ON p.id = u.id
            ORDER BY p.name ASC
            """
        )
        return [dict(row) for row in rows]

    # =========================================================================
    # Students
    # =========================================================================
    async def create_student(self, user_id, name: str, class_id: int, gender: str | None = None, birth_data: str | None = None) -> int:
        u_id = parse_id(user_id)
        await self.pool.execute(
            """
            INSERT INTO students (id, name, class_id, gender, birth_data)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE SET 
                name = EXCLUDED.name, 
                class_id = EXCLUDED.class_id, 
                gender = EXCLUDED.gender, 
                birth_data = EXCLUDED.birth_data
            """,
            u_id,
            name,
            parse_id(class_id),
            gender,
            birth_data,
        )
        return u_id

    async def get_student_by_id(self, student_id) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT s.id, s.name, s.class_id, s.gender, s.birth_data, s.created_at, u.email, c.name AS class_name
            FROM students s
            JOIN users u ON s.id = u.id
            LEFT JOIN class c ON s.class_id = c.id
            WHERE s.id = $1
            """,
            parse_id(student_id),
        )
        return dict(row) if row else None

    async def get_student_by_user_id(self, user_id) -> dict | None:
        return await self.get_student_by_id(user_id)

    async def get_all_students(self) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT s.id, s.name, s.class_id, s.gender, s.birth_data, s.created_at, u.email, c.name AS class_name,
                   (
                       SELECT COALESCE(json_agg(json_build_object(
                           'id', p.id,
                           'name', p.name,
                           'email', pu.email,
                           'phone', pu.phone
                       )), '[]'::json)
                       FROM student_parent_map m
                       JOIN parenets p ON m.parent_id = p.id
                       JOIN users pu ON p.id = pu.id
                       WHERE m.student_id = s.id
                   ) as parents
            FROM students s
            JOIN users u ON s.id = u.id
            LEFT JOIN class c ON s.class_id = c.id
            ORDER BY s.name ASC
            """
        )
        results = []
        for row in rows:
            d = dict(row)
            import json
            if isinstance(d.get('parents'), str):
                try:
                    d['parents'] = json.loads(d['parents'])
                except:
                    d['parents'] = []
            results.append(d)
        return results

    async def add_student_parent_link(self, student_id, parent_id) -> None:
        await self.pool.execute(
            "INSERT INTO student_parent_map (student_id, parent_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            parse_id(student_id),
            parse_id(parent_id),
        )

    async def get_linked_students_for_parent(self, parent_id) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT s.id, s.name, s.class_id, s.gender, s.birth_data, s.created_at, u.email, c.name AS class_name
            FROM students s
            JOIN student_parent_map m ON s.id = m.student_id
            JOIN users u ON s.id = u.id
            LEFT JOIN class c ON s.class_id = c.id
            WHERE m.parent_id = $1
            """,
            parse_id(parent_id),
        )
        return [dict(row) for row in rows]

    async def is_student_linked_to_parent(self, student_id, parent_id) -> bool:
        row = await self.pool.fetchrow(
            "SELECT 1 FROM student_parent_map WHERE student_id = $1 AND parent_id = $2",
            parse_id(student_id),
            parse_id(parent_id),
        )
        return row is not None


    async def get_parent_email_for_student(self, student_id) -> str | None:
        row = await self.pool.fetchrow(
            """
            SELECT u.email
            FROM student_parent_map m
            JOIN parenets p ON m.parent_id = p.id
            JOIN users u ON p.id = u.id
            WHERE m.student_id = $1
            LIMIT 1
            """,
            parse_id(student_id),
        )
        return row["email"] if row else None

    async def get_parent_for_student(self, student_id) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT p.id, p.name, u.email
            FROM student_parent_map m
            JOIN parenets p ON m.parent_id = p.id
            JOIN users u ON p.id = u.id
            WHERE m.student_id = $1
            LIMIT 1
            """,
            parse_id(student_id),
        )
        return dict(row) if row else None

    # =========================================================================
    # Classes
    # =========================================================================
    async def create_class(self, name: str, level_id: int, head_teacher_id = None, capacity: int = 25) -> int:
        h_id = parse_id(head_teacher_id) if head_teacher_id else None
        return await self.pool.fetchval(
            """
            INSERT INTO class (name, level_id, head_teacher_id, capacity)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            name.strip(),
            parse_id(level_id),
            h_id,
            int(capacity or 25),
        )

    async def get_class_by_name_and_level(self, name: str, level_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT id, name, level_id, head_teacher_id, COALESCE(capacity, 25) AS capacity, created_at
            FROM class
            WHERE LOWER(name) = LOWER($1) AND level_id = $2
            """,
            name.strip(),
            parse_id(level_id)
        )
        return dict(row) if row else None

    async def get_class_by_id(self, class_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT c.id, c.name, c.level_id, c.head_teacher_id, c.created_at, COALESCE(c.capacity, 25) AS capacity,
                   t.name AS teacher_name, u.email AS teacher_email, l.name AS level_name,
                   (SELECT COUNT(*) FROM students s WHERE s.class_id = c.id) AS student_count
            FROM class c
            LEFT JOIN teachers t ON c.head_teacher_id = t.id
            LEFT JOIN users u ON t.id = u.id
            LEFT JOIN levels l ON c.level_id = l.level_id
            WHERE c.id = $1
            """,
            parse_id(class_id),
        )
        return dict(row) if row else None

    async def update_class(
        self,
        class_id: int,
        name: str | None = None,
        level_id: int | None = None,
        head_teacher_id = None,
        capacity: int | None = None,
    ) -> dict:
        cid = parse_id(class_id)
        current = await self.get_class_by_id(cid)
        if not current:
            raise ValueError(f"Class {cid} not found")

        new_name = name.strip() if name is not None else current["name"]
        new_level_id = parse_id(level_id) if level_id is not None else current["level_id"]
        new_head = parse_id(head_teacher_id) if head_teacher_id is not None else current["head_teacher_id"]
        new_capacity = int(capacity) if capacity is not None else current.get("capacity", 25)

        await self.pool.execute(
            """
            UPDATE class
            SET name = $1, level_id = $2, head_teacher_id = $3, capacity = $4
            WHERE id = $5
            """,
            new_name,
            new_level_id,
            new_head,
            new_capacity,
            cid,
        )
        return await self.get_class_by_id(cid)

    async def delete_class(self, class_id: int) -> bool:
        cid = parse_id(class_id)
        # Ensure students class_id column is nullable
        await self.pool.execute("ALTER TABLE students ALTER COLUMN class_id DROP NOT NULL")
        # Unlink students if any
        await self.pool.execute("UPDATE students SET class_id = NULL WHERE class_id = $1", cid)
        await self.pool.execute("DELETE FROM event_class_map WHERE class_id = $1", cid)
        await self.pool.execute("DELETE FROM class WHERE id = $1", cid)
        return True

    async def delete_level(self, level_id: int) -> bool:
        lid = parse_id(level_id)
        # Delete/unlink all classes in this level
        classes = await self.pool.fetch("SELECT id FROM class WHERE level_id = $1", lid)
        for c in classes:
            await self.delete_class(c["id"])
        await self.pool.execute("DELETE FROM levels WHERE level_id = $1", lid)
        return True

    async def update_level(
        self,
        level_id: int,
        name: str | None = None,
        isced_level: int | None = None,
        age_band_min: int | None = None,
        age_band_max: int | None = None,
        ordinal: int | None = None,
        is_active: bool | None = None,
    ) -> dict | None:
        lid = parse_id(level_id)
        current = await self.get_level_by_id(lid)
        if not current:
            raise ValueError(f"Level {lid} not found")
        
        new_name = name.strip() if name is not None else current.get("name")
        new_isced = isced_level if isced_level is not None else current.get("isced_level")
        new_min = age_band_min if age_band_min is not None else current.get("age_band_min")
        new_max = age_band_max if age_band_max is not None else current.get("age_band_max")
        new_ord = ordinal if ordinal is not None else current.get("ordinal")
        new_active = is_active if is_active is not None else current.get("is_active", True)

        await self.pool.execute(
            """
            UPDATE levels
            SET name = $1, isced_level = $2, age_band_min = $3, age_band_max = $4, ordinal = $5, is_active = $6
            WHERE level_id = $7
            """,
            new_name, new_isced, new_min, new_max, new_ord, new_active, lid
        )
        return await self.get_level_by_id(lid)

    async def get_students_for_class(self, class_id: int) -> list[dict]:
        cid = parse_id(class_id)
        rows = await self.pool.fetch(
            """
            SELECT s.id, s.name, s.gender, s.birth_data, s.created_at, s.class_id, u.email,
                   COALESCE((
                       SELECT string_agg(p.name, ', ')
                       FROM student_parent_map spm
                       JOIN parenets p ON spm.parent_id = p.id
                       WHERE spm.student_id = s.id
                   ), '') AS parent_names
            FROM students s
            JOIN users u ON s.id = u.id
            WHERE s.class_id = $1
            ORDER BY s.name ASC
            """,
            cid,
        )
        return [dict(row) for row in rows]

    async def reassign_student_class(self, student_id, new_class_id: int | None) -> bool:
        sid = parse_id(student_id)
        cid = parse_id(new_class_id) if new_class_id else None
        await self.pool.execute("UPDATE students SET class_id = $1 WHERE id = $2", cid, sid)
        return True

    async def bulk_reassign_students(self, student_ids: list[int], new_class_id: int | None) -> int:
        if not student_ids:
            return 0
        sids = [parse_id(s) for s in student_ids]
        cid = parse_id(new_class_id) if new_class_id else None
        await self.pool.execute("UPDATE students SET class_id = $1 WHERE id = ANY($2::bigint[])", cid, sids)
        return len(sids)

    async def get_class_by_head_teacher(self, teacher_id) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT c.id, c.name, c.level_id, c.head_teacher_id, COALESCE(c.capacity, 25) AS capacity, l.name AS level_name
            FROM class c
            JOIN levels l ON c.level_id = l.level_id
            WHERE c.head_teacher_id = $1
            LIMIT 1
            """,
            parse_id(teacher_id),
        )
        return dict(row) if row else None

    async def get_all_classes(self) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT c.id, c.name, c.level_id, c.head_teacher_id, c.created_at, COALESCE(c.capacity, 25) AS capacity,
                   t.name AS teacher_name, u.email AS teacher_email, l.name AS level_name,
                   (SELECT COUNT(*) FROM students s WHERE s.class_id = c.id) AS student_count
            FROM class c
            LEFT JOIN teachers t ON c.head_teacher_id = t.id
            LEFT JOIN users u ON t.id = u.id
            LEFT JOIN levels l ON c.level_id = l.level_id
            ORDER BY COALESCE(l.ordinal, 999) ASC, c.name ASC
            """
        )
        return [dict(row) for row in rows]

    # =========================================================================
    # Events & Mappings
    # =========================================================================
    async def create_event(
        self,
        title: str,
        description: str,
        address: str | None,
        school_subsidy: float,
        date_val: datetime,
        created_by,
        class_mappings: list[dict],  # list of {"class_id": int, "ticket_price": float, "costbudget_id": int | None, "budget_description": str | None, "budget_price": float | None}
    ) -> dict:
        event_id = None
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                event_id = await conn.fetchval(
                    """
                    INSERT INTO event (title, description, address, school_subsidy, date, created_by)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    title,
                    description,
                    address,
                    school_subsidy,
                    date_val,
                    parse_id(created_by),
                )

                # Insert class mappings
                for mapping in class_mappings:
                    ecm_id = await conn.fetchval(
                        """
                        INSERT INTO event_class_map (event_id, class_id, ticket_price)
                        VALUES ($1, $2, $3)
                        RETURNING id
                        """,
                        event_id,
                        parse_id(mapping["class_id"]),
                        mapping.get("ticket_price", 0.0),
                    )

        return await self.get_event_by_id(event_id)

    async def get_event_by_id(self, event_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT id, title, description, address, event_map_id, school_subsidy, date, created_by, created_at,
                   status, predicted_attendance, manager_reviewer_id, finance_reviewer_id, total_cost,
                   submitted_at, manager_approved_at, finance_priced_at, published_at, rejection_reason
            FROM event WHERE id = $1
            """,
            parse_id(event_id),
        )
        if not row:
            return None

        result = dict(row)
        maps = await self.pool.fetch(
            """
            SELECT ecm.id, ecm.class_id, ecm.ticket_price, c.name AS class_name, l.name AS level_name,
                   (SELECT COUNT(*) FROM students s WHERE s.class_id = ecm.class_id) AS student_count
            FROM event_class_map ecm
            JOIN class c ON ecm.class_id = c.id
            JOIN levels l ON c.level_id = l.level_id
            WHERE ecm.event_id = $1
            """,
            parse_id(event_id),
        )
        processed_maps = []
        for m in maps:
            md = dict(m)
            md["budgets"] = []
            processed_maps.append(md)
        result["class_mappings"] = processed_maps
        return result

    async def get_all_events(self, statuses: list[str] | None = None) -> list[dict]:
        query = """
            SELECT id, title, description, address, event_map_id, school_subsidy, date, created_by, created_at,
                   status, predicted_attendance, manager_reviewer_id, finance_reviewer_id, total_cost,
                   submitted_at, manager_approved_at, finance_priced_at, published_at, rejection_reason
            FROM event
        """

        params = []
        if statuses:
            query += " WHERE status = ANY($1)"
            params.append(statuses)
        query += " ORDER BY date ASC"
        rows = await self.pool.fetch(query, *params)
        results = []
        for r in rows:
            ev = dict(r)
            maps = await self.pool.fetch(
                """
                SELECT ecm.id, ecm.class_id, ecm.ticket_price, c.name AS class_name, l.name AS level_name,
                       (SELECT COUNT(*) FROM students s WHERE s.class_id = ecm.class_id) AS student_count
                FROM event_class_map ecm
                JOIN class c ON ecm.class_id = c.id
                JOIN levels l ON c.level_id = l.level_id
                WHERE ecm.event_id = $1
                """,
                ev["id"],
            )
            processed_maps = []
            for m in maps:
                md = dict(m)
                md["budgets"] = []
                processed_maps.append(md)
            ev["class_mappings"] = processed_maps
            results.append(ev)
        return results

    async def get_events_for_student(self, student_id) -> list[dict]:
        s_id = parse_id(student_id)
        student = await self.get_student_by_id(s_id)
        if not student or not student.get("class_id"):
            return []
        class_id = student["class_id"]
        
        rows = await self.pool.fetch(
            """
            SELECT DISTINCT e.id, e.title, e.description, e.address, e.event_map_id, e.school_subsidy, e.date, e.created_by, e.created_at,
                            e.status, e.predicted_attendance, e.manager_reviewer_id, e.finance_reviewer_id, e.total_cost,
                            e.submitted_at, e.manager_approved_at, e.finance_priced_at, e.published_at
            FROM event e
            JOIN event_class_map ecm ON e.id = ecm.event_id
            WHERE e.status = 'published' AND ecm.class_id = $1
            ORDER BY e.date ASC
            """,
            class_id,
        )

        results = []
        for r in rows:
            ev = dict(r)
            maps = await self.pool.fetch(
                """
                SELECT ecm.id, ecm.class_id, ecm.ticket_price, c.name AS class_name, l.name AS level_name,
                       (SELECT COUNT(*) FROM students s WHERE s.class_id = ecm.class_id) AS student_count
                FROM event_class_map ecm
                JOIN class c ON ecm.class_id = c.id
                JOIN levels l ON c.level_id = l.level_id
                WHERE ecm.event_id = $1
                """,
                ev["id"],
            )
            processed_maps = []
            for m in maps:
                md = dict(m)
                md["budgets"] = []
                processed_maps.append(md)
            ev["class_mappings"] = processed_maps
            results.append(ev)
        return results

    async def update_event(self, event_id: int, title: str, description: str, address: str | None, school_subsidy: float, date_val: datetime) -> dict | None:
        ev_id = parse_id(event_id)
        row = await self.pool.fetchrow(
            """
            UPDATE event
            SET title = $1, description = $2, address = $3, school_subsidy = $4, date = $5
            WHERE id = $6
            RETURNING id, title, description, address, event_map_id, school_subsidy, date, created_by, created_at
            """,
            title,
            description,
            address,
            school_subsidy,
            date_val,
            ev_id,
        )
        return dict(row) if row else None

    async def update_event_full(
        self,
        event_id: int,
        title: str,
        description: str,
        address: str | None,
        school_subsidy: float,
        date_val: datetime,
        class_mappings: list[dict],
    ) -> dict:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Update general event details
                await conn.execute(
                    """
                    UPDATE event
                    SET title = $1, description = $2, address = $3, school_subsidy = $4, date = $5
                    WHERE id = $6
                    """,
                    title,
                    description,
                    address,
                    school_subsidy,
                    date_val,
                    parse_id(event_id),
                )

                # 2. Query existing mappings
                existing_rows = await conn.fetch(
                    "SELECT id, class_id FROM event_class_map WHERE event_id = $1",
                    parse_id(event_id),
                )
                existing_map = {int(row["class_id"]): row["id"] for row in existing_rows}

                # 3. Upsert mappings and delete/insert budgets
                for mapping in class_mappings:
                    class_id = int(parse_id(mapping["class_id"]))
                    ticket_price = float(mapping.get("ticket_price", 0.0))

                    if class_id in existing_map:
                        ecm_id = existing_map[class_id]
                        await conn.execute(
                            "UPDATE event_class_map SET ticket_price = $1 WHERE id = $2",
                            ticket_price,
                            ecm_id,
                        )
                    else:
                        ecm_id = await conn.fetchval(
                            """
                            INSERT INTO event_class_map (event_id, class_id, ticket_price)
                            VALUES ($1, $2, $3)
                            RETURNING id
                            """,
                            parse_id(event_id),
                            class_id,
                            ticket_price,
                        )

                    # No budgets to handle since cost_budget is dropped
                    pass

        return await self.get_event_by_id(event_id)

    async def delete_event(self, event_id: int) -> bool:
        result = await self.pool.execute("DELETE FROM event WHERE id = $1", parse_id(event_id))
        return result == "DELETE 1"

    async def get_class_map_by_id(self, map_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT id, event_id, class_id, ticket_price FROM event_class_map WHERE id = $1",
            parse_id(map_id),
        )
        return dict(row) if row else None

    async def get_enrollments_for_student_and_event(self, student_id, event_id) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT en.id, en.student_id, en.event_class_map_id, en.state
            FROM enrollment en
            JOIN event_class_map ecm ON en.event_class_map_id = ecm.id
            WHERE en.student_id = $1 AND ecm.event_id = $2
            """,
            parse_id(student_id),
            parse_id(event_id),
        )
        return [dict(row) for row in rows]

    # =========================================================================
    # Enrollments
    # =========================================================================
    async def create_enrollment(self, student_id, event_class_map_id: int, state: str, teacher_id = None, parent_id = None) -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO enrollment (student_id, event_class_map_id, state, teacher_id, parent_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (student_id, event_class_map_id) DO UPDATE SET state = EXCLUDED.state
            RETURNING id
            """,
            parse_id(student_id),
            parse_id(event_class_map_id),
            state,
            parse_id(teacher_id) if teacher_id else None,
            parse_id(parent_id) if parent_id else None,
        )

    async def update_enrollment_state(self, enrollment_id: int, state: str, teacher_id = None, parent_id = None) -> bool:
        query = "UPDATE enrollment SET state = $1"
        params = [state]
        if teacher_id:
            params.append(parse_id(teacher_id))
            query += f", teacher_id = ${len(params)}"
        if parent_id:
            params.append(parse_id(parent_id))
            query += f", parent_id = ${len(params)}"
        params.append(parse_id(enrollment_id))
        query += f" WHERE id = ${len(params)}"
        res = await self.pool.execute(query, *params)
        return res == "UPDATE 1"

    async def get_enrollment_by_id(self, enrollment_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT en.id, en.student_id, en.event_class_map_id, en.state, en.teacher_id, en.parent_id, en.created_at,
                   s.name AS student_name, c.name AS class_name, e.title AS event_title, ecm.ticket_price
            FROM enrollment en
            JOIN students s ON en.student_id = s.id
            JOIN event_class_map ecm ON en.event_class_map_id = ecm.id
            JOIN class c ON ecm.class_id = c.id
            JOIN event e ON ecm.event_id = e.id
            WHERE en.id = $1
            """,
            parse_id(enrollment_id),
        )
        return dict(row) if row else None

    async def get_enrollment_by_student_and_map(self, student_id, event_class_map_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT id, student_id, event_class_map_id, state, teacher_id, parent_id, created_at FROM enrollment WHERE student_id = $1 AND event_class_map_id = $2",
            parse_id(student_id),
            parse_id(event_class_map_id),
        )
        return dict(row) if row else None

    async def get_enrollments_for_teacher(self, teacher_id) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT en.id, en.student_id, en.event_class_map_id, en.state, en.teacher_id, en.parent_id, en.created_at,
                   s.name AS student_name, c.name AS class_name, e.title AS event_title, ecm.ticket_price, u.email AS student_email
            FROM enrollment en
            JOIN students s ON en.student_id = s.id
            JOIN users u ON s.id = u.id
            JOIN event_class_map ecm ON en.event_class_map_id = ecm.id
            JOIN class c ON ecm.class_id = c.id
            JOIN event e ON ecm.event_id = e.id
            WHERE c.head_teacher_id = $1
              AND en.state NOT IN ('requested_by_student', 'rejected_by_parent')
            """,
            parse_id(teacher_id),
        )
        return [dict(row) for row in rows]

    async def get_enrollments_for_parent(self, parent_id) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT en.id, en.student_id, en.event_class_map_id, en.state, en.teacher_id, en.parent_id, en.created_at,
                   s.name AS student_name, c.name AS class_name, e.title AS event_title, ecm.ticket_price
            FROM enrollment en
            JOIN students s ON en.student_id = s.id
            JOIN student_parent_map m ON s.id = m.student_id
            JOIN event_class_map ecm ON en.event_class_map_id = ecm.id
            JOIN class c ON ecm.class_id = c.id
            JOIN event e ON ecm.event_id = e.id
            WHERE m.parent_id = $1
            """,
            parse_id(parent_id),
        )
        return [dict(row) for row in rows]

    async def get_enrollments_for_student(self, student_id) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT en.id, en.student_id, en.event_class_map_id, en.state, en.teacher_id, en.parent_id, en.created_at,
                   s.name AS student_name, c.name AS class_name, e.title AS event_title, ecm.ticket_price
            FROM enrollment en
            JOIN students s ON en.student_id = s.id
            JOIN event_class_map ecm ON en.event_class_map_id = ecm.id
            JOIN class c ON ecm.class_id = c.id
            JOIN event e ON ecm.event_id = e.id
            WHERE en.student_id = $1
            """,
            parse_id(student_id),
        )
        return [dict(row) for row in rows]

    async def delete_enrollment(self, enrollment_id: int) -> bool:
        result = await self.pool.execute("DELETE FROM enrollment WHERE id = $1", parse_id(enrollment_id))
        return result == "DELETE 1"

    # =========================================================================
    # Payments
    # =========================================================================
    async def create_payment(self, enrollment_id: int, amount: float, status: str) -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO payments (enrollment_id, amount, status)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            parse_id(enrollment_id),
            amount,
            status,
        )

    async def get_payment_by_enrollment(self, enrollment_id: int) -> dict | None:
        row = await self.pool.fetchrow("SELECT id, enrollment_id, amount, status, created_at FROM payments WHERE enrollment_id = $1", parse_id(enrollment_id))
        return dict(row) if row else None

    # =========================================================================
    # Feedbacks
    # =========================================================================
    async def create_event_feedback(self, event_id: int, user_id, rating: int, comments: str | None) -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO event_feedback (event_id, user_id, rating, comments)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            parse_id(event_id),
            parse_id(user_id),
            rating,
            comments,
        )

    async def get_feedback_for_event(self, event_id: int) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT id, event_id, user_id, rating, comments, created_at FROM event_feedback WHERE event_id = $1",
            parse_id(event_id),
        )
        return [dict(row) for row in rows]

    # =========================================================================
    # Notifications
    # =========================================================================
    async def create_notification(
        self, event_id: int, recipient_user_id, title_override: str | None = None
    ) -> UUID:
        return await self.pool.fetchval(
            """
            INSERT INTO notifications (event_id, recipient_user_id, title_override)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            parse_id(event_id),
            parse_id(recipient_user_id),
            title_override,
        )

    async def get_notifications_for_user(self, user_id) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT n.id, n.event_id, n.recipient_user_id, n.delivered_at, n.read_at,
                   COALESCE(n.title_override, e.title) AS title,
                   e.description
            FROM notifications n
            JOIN event e ON n.event_id = e.id
            WHERE n.recipient_user_id = $1
            ORDER BY n.delivered_at DESC
            """,
            parse_id(user_id),
        )
        return [dict(row) for row in rows]

    async def mark_notification_read(self, notif_id: UUID) -> bool:
        result = await self.pool.execute(
            "UPDATE notifications SET read_at = CURRENT_TIMESTAMP WHERE id = $1 AND read_at IS NULL",
            notif_id,
        )
        return result == "UPDATE 1"

    # =========================================================================
    # Student Health & Records (PII Table)
    # =========================================================================
    async def create_or_update_student_health(
        self,
        student_id,
        national_id_encrypted: str,
        medical_conditions_encrypted: str,
        emergency_contact_encrypted: str,
    ) -> UUID:
        return await self.pool.fetchval(
            """
            INSERT INTO student_health_and_records (
                student_id,
                national_id_encrypted,
                medical_conditions_encrypted,
                emergency_contact_encrypted
            )
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (student_id) DO UPDATE SET
                national_id_encrypted = EXCLUDED.national_id_encrypted,
                medical_conditions_encrypted = EXCLUDED.medical_conditions_encrypted,
                emergency_contact_encrypted = EXCLUDED.emergency_contact_encrypted
            RETURNING id
            """,
            parse_id(student_id),
            national_id_encrypted,
            medical_conditions_encrypted,
            emergency_contact_encrypted,
        )

    async def get_student_health_by_student_id(self, student_id) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT id, student_id, national_id_encrypted, medical_conditions_encrypted, emergency_contact_encrypted
            FROM student_health_and_records
            WHERE student_id = $1
            """,
            parse_id(student_id),
        )
        return dict(row) if row else None

    async def get_analytics_summary(self) -> dict:
        """Fetch aggregated counts for analytics without using SELECT *."""
        row = await self.pool.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM students) AS student_count,
                (SELECT COUNT(*) FROM class) AS class_count,
                (SELECT COUNT(*) FROM enrollment) AS enrollment_count,
                (SELECT COUNT(*) FROM event) AS event_count
            """
        )
        return dict(row) if row else {
            "student_count": 0,
            "class_count": 0,
            "enrollment_count": 0,
            "event_count": 0,
        }

    # =========================================================================
    # Resource Types (workflow & resource schema)
    # =========================================================================
    async def create_resource_type(
        self, name: str, category: str, is_custom: bool = False, created_by_user_id = None
    ) -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO resource_types (name, category, is_custom, created_by_user_id, is_active)
            VALUES ($1, $2, $3, $4, true)
            RETURNING id
            """,
            name,
            category,
            is_custom,
            parse_id(created_by_user_id) if created_by_user_id else None,
        )

    async def get_resource_type_by_id(self, rt_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT id, name, category, is_custom, created_by_user_id, is_active, created_at FROM resource_types WHERE id = $1",
            parse_id(rt_id),
        )
        return dict(row) if row else None

    async def get_all_resource_types(self, category: str | None = None) -> list[dict]:
        if category:
            rows = await self.pool.fetch(
                "SELECT id, name, category, is_custom, created_by_user_id, is_active, created_at FROM resource_types WHERE is_active = true AND category = $1 ORDER BY name ASC",
                category,
            )
        else:
            rows = await self.pool.fetch(
                "SELECT id, name, category, is_custom, created_by_user_id, is_active, created_at FROM resource_types WHERE is_active = true ORDER BY name ASC"
            )
        return [dict(row) for row in rows]

    # =========================================================================
    # Resources (workflow & resource schema)
    # =========================================================================
    async def create_resource(
        self, event_id: int, resource_type_id: int, description: str | None, quantity: int, added_by_user_id: int
    ) -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO resources (event_id, resource_type_id, description, quantity, added_by_user_id, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, now(), now())
            RETURNING id
            """,
            parse_id(event_id),
            parse_id(resource_type_id),
            description,
            quantity,
            parse_id(added_by_user_id),
        )

    async def get_resource_by_id(self, resource_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT id, event_id, resource_type_id, description, quantity, added_by_user_id, updated_by_user_id, created_at, updated_at FROM resources WHERE id = $1",
            parse_id(resource_id),
        )
        return dict(row) if row else None

    async def get_resources_for_event(self, event_id: int) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT r.id, r.event_id, r.resource_type_id, r.description, r.quantity, 
                   r.added_by_user_id, r.updated_by_user_id, r.created_at, r.updated_at,
                   rt.name AS resource_type_name, rt.category AS resource_type_category
            FROM resources r
            JOIN resource_types rt ON r.resource_type_id = rt.id
            WHERE r.event_id = $1
            ORDER BY rt.category ASC, rt.name ASC
            """,
            parse_id(event_id),
        )
        return [dict(row) for row in rows]

    async def delete_resources_for_event(self, event_id: int) -> None:
        await self.pool.execute(
            "DELETE FROM resources WHERE event_id = $1",
            parse_id(event_id),
        )

    async def update_resource(
        self, resource_id: int, resource_type_id: int | None, description: str | None, quantity: int | None, updated_by_user_id: int
    ) -> None:
        await self.pool.execute(
            """
            UPDATE resources
            SET resource_type_id = COALESCE($1, resource_type_id),
                description = COALESCE($2, description),
                quantity = COALESCE($3, quantity),
                updated_by_user_id = $4,
                updated_at = now()
            WHERE id = $5
            """,
            parse_id(resource_type_id) if resource_type_id else None,
            description,
            quantity,
            parse_id(updated_by_user_id),
            parse_id(resource_id),
        )

    # =========================================================================
    # Resource Cost (workflow & resource schema)
    # =========================================================================
    async def set_resource_cost(
        self, resource_id: int, unit_price: float, total_cost: float, currency: str, set_by_user_id: int
    ) -> int:
        event_id = await self.pool.fetchval(
            "SELECT event_id FROM resources WHERE id = $1",
            parse_id(resource_id)
        )
        if not event_id:
            raise ValueError(f"Resource with ID {resource_id} not found")

        return await self.pool.fetchval(
            """
            INSERT INTO resource_cost (event_id, resource_id, unit_price, total_cost, currency, set_by_user_id, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, now())
            ON CONFLICT (resource_id) DO UPDATE SET
                unit_price = EXCLUDED.unit_price,
                total_cost = EXCLUDED.total_cost,
                currency = EXCLUDED.currency,
                set_by_user_id = EXCLUDED.set_by_user_id,
                updated_at = now()
            RETURNING id
            """,
            event_id,
            parse_id(resource_id),
            unit_price,
            total_cost,
            currency,
            parse_id(set_by_user_id),
        )

    async def get_resource_cost_by_resource_id(self, resource_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT id, event_id, resource_id, unit_price, total_cost, currency, set_by_user_id, updated_at FROM resource_cost WHERE resource_id = $1",
            parse_id(resource_id),
        )
        return dict(row) if row else None

    # =========================================================================
    # Predictions & Class student counting
    # =========================================================================
    async def get_student_count_for_classes(self, class_ids: list[int]) -> int:
        if not class_ids:
            return 0
        parsed_ids = [parse_id(cid) for cid in class_ids]
        val = await self.pool.fetchval(
            "SELECT COUNT(*) FROM students WHERE class_id = ANY($1)",
            parsed_ids,
        )
        return val or 0

    async def update_event_total_cost(self, event_id: int, total_cost: float) -> None:
        await self.pool.execute(
            "UPDATE event SET total_cost = $1 WHERE id = $2",
            total_cost,
            parse_id(event_id),
        )

    async def get_all_managers(self) -> list[dict]:
        rows = await self.pool.fetch("SELECT id, email, role FROM users WHERE role = 'manager'")
        return [dict(row) for row in rows]

    async def get_all_finance_users(self) -> list[dict]:
        rows = await self.pool.fetch("SELECT id, email, role FROM users WHERE role = 'finance'")
        return [dict(row) for row in rows]

    async def get_all_event_teachers(self) -> list[dict]:
        rows = await self.pool.fetch("SELECT id, email, role FROM users WHERE role = 'event_teacher'")
        return [dict(row) for row in rows]

