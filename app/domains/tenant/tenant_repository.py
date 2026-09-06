"""TenantRepository — database operations for tenant (school) databases."""

import json
import re
from datetime import datetime
from uuid import UUID

import asyncpg

# Sentinel for "caller did not mention this field" in a partial update,
# distinct from an explicit `None` -- which for head_teacher_id means "clear
# it, the class has no head teacher". A plain `None` default can't tell the
# two apart, which is why a departing teacher could never be un-assigned.
UNSET = object()


_SECTION_LABEL_RE = re.compile(r"(?:-|\bSection)\s*(\S+)\s*$", re.IGNORECASE)


def _derive_section_label(name: str) -> str | None:
    """Best-effort section identity from either of this codebase's two live
    naming conventions: the Curriculum Ladder Wizard / StructureClassesView's
    "<Grade> - <Section>" (AGENTS.md §7's locked grade prefix), or
    `seed_data.py`'s "<Grade> Section <Section>" -- confirmed as a real,
    load-bearing divergence by testing against the actually-running app's
    seeded tenant_a data, not assumed from the doc example alone. Mirrors the
    regex used to backfill existing rows in the tenant_0003 migration -- kept
    in sync here so a class created or renamed after that migration still gets
    a usable rollover-matching key instead of permanently holding at NULL."""
    match = _SECTION_LABEL_RE.search(name or "")
    return match.group(1) if match else None


def parse_id(val) -> int | UUID:
    if isinstance(val, UUID | int):
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
        is_active: bool = True,
    ) -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO levels (name, isced_level, age_band_min, age_band_max, ordinal, is_active)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING level_id
            """,
            name,
            isced_level,
            age_band_min,
            age_band_max,
            ordinal,
            is_active,
        )

    async def get_level_by_name(self, name: str) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT level_id, name, isced_level, age_band_min, age_band_max, ordinal, is_active "
            "FROM levels WHERE LOWER(name) = LOWER($1)",
            name.strip(),
        )
        return dict(row) if row else None

    async def get_level_by_id(self, level_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT level_id, name, isced_level, age_band_min, age_band_max, ordinal, is_active "
            "FROM levels WHERE level_id = $1",
            parse_id(level_id),
        )
        return dict(row) if row else None

    async def get_level_by_ordinal(self, ordinal: int) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT level_id, name, isced_level, age_band_min, age_band_max, ordinal, is_active "
            "FROM levels WHERE ordinal = $1",
            ordinal,
        )
        return dict(row) if row else None

    async def upsert_level_by_name(
        self,
        name: str,
        isced_level: int | None = None,
        age_band_min: int | None = None,
        age_band_max: int | None = None,
        ordinal: int | None = None,
        is_active: bool = True,
    ) -> tuple[int, str]:
        """Idempotent-by-name create-or-update: a name collision updates the
        existing row's other fields instead of being silently ignored or
        duplicated. Extracted from what TenantService.create_level used to do
        inline, so the single-level-create endpoint and the bulk import path
        share one implementation. Returns (level_id, "created"|"updated")."""
        existing = await self.get_level_by_name(name)
        if existing:
            await self.update_level(
                level_id=existing["level_id"],
                name=name,
                isced_level=isced_level,
                age_band_min=age_band_min,
                age_band_max=age_band_max,
                ordinal=ordinal,
                is_active=is_active,
            )
            return existing["level_id"], "updated"
        new_id = await self.create_level(
            name,
            isced_level=isced_level if isced_level is not None else 1,
            age_band_min=age_band_min if age_band_min is not None else 6,
            age_band_max=age_band_max if age_band_max is not None else 7,
            ordinal=ordinal if ordinal is not None else 1,
            is_active=is_active,
        )
        return new_id, "created"

    async def get_all_levels(self) -> list[dict]:
        # Natural grade order, never alphabetical -- "Grade 10" must not land
        # between "Grade 1" and "Grade 2". Matches get_academic_structure()'s
        # ordering below.
        rows = await self.pool.fetch(
            "SELECT level_id, name, isced_level, age_band_min, age_band_max, ordinal, is_active "
            "FROM levels ORDER BY COALESCE(ordinal, 999), level_id ASC"
        )
        return [dict(row) for row in rows]

    async def save_academic_structure(self, payload: dict, changed_by=None) -> None:
        """
        Saves or updates the school academic structure:
        - Curriculums / Levels
        - Sections / Classes with capacities
        - Academic Settings (year, start month, weekend days)
        - Blackout Dates / Holidays

        Levels and sections are matched by their stable id (level_id / id)
        when the client provides one -- never by ordinal or name, which are
        mutable display attributes a client can legitimately change.
        Matching on them meant swapping two grades' ordinals silently
        renamed whichever row already held the target ordinal (re-parenting
        its classes and students under the wrong label), and renaming a
        section created a second, empty row while the original -- and its
        whole roster -- stayed behind, invisible under a name nothing
        pointed at anymore.

        Levels are never hard-deleted here: levels.is_active is the level
        lifecycle mechanism, and a level missing from a partial wizard
        payload is not distinguishable from one the client simply hasn't
        loaded yet -- treating "absent" as "delete" would turn every
        deactivation into an irreversible loss. Sections *are* removed when
        dropped from their level's list (that is the wizard's only way to
        represent "delete this section", via removeSection()), guarded by
        the same enrollment/payment-history check the standalone delete
        endpoints use, and any student unlinked by the removal is logged to
        student_class_history exactly like every other placement change.
        """
        async with self.pool.acquire() as conn, conn.transaction():
            # academic_year_id is NOT NULL on class with no DB-side default
            # (ADR 0002) -- resolve once, up front, for every new section
            # this save creates.
            active_year_id = await conn.fetchval(
                "SELECT id FROM academic_years WHERE status = 'active' LIMIT 1"
            )

            # 1. Academic Settings -- resolve "the current row" exactly
            # the way get_academic_structure() reads it (ORDER BY id
            # DESC), so a tenant that somehow ends up with more than one
            # settings row (e.g. two concurrent first-time saves both
            # seeing "none exists yet") can't have this UPDATE a
            # different row than the one every reader displays.
            system_val = payload.get("system") or "US"
            cal = payload.get("calendar") or {}
            acad_year = cal.get("academic_year") or "2026-2027"
            start_month = int(cal.get("start_month") or 9)
            weekend_days = cal.get("weekend_days") or ["Saturday", "Sunday"]

            existing_settings = await conn.fetchval(
                "SELECT id FROM academic_settings ORDER BY id DESC LIMIT 1"
            )
            if existing_settings:
                await conn.execute(
                    """
                    UPDATE academic_settings
                    SET system = $1, academic_year = $2, start_month = $3, weekend_days = $4, updated_at = CURRENT_TIMESTAMP
                    WHERE id = $5
                    """,
                    system_val,
                    acad_year,
                    start_month,
                    weekend_days,
                    existing_settings,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO academic_settings (system, academic_year, start_month, weekend_days)
                    VALUES ($1, $2, $3, $4)
                    """,
                    system_val,
                    acad_year,
                    start_month,
                    weekend_days,
                )

            # 2. Blackout Dates -- diff against the payload by date (its
            # natural key), update/insert what's given, and delete only
            # what's genuinely absent -- instead of DELETE-then-
            # reinsert-everything, which erases every date the caller's
            # in-memory state doesn't happen to be holding, even ones it
            # was never asked to touch.
            blackout_dates = payload.get("blackout_dates") or []
            existing_bd_rows = await conn.fetch("SELECT id, date FROM blackout_dates")
            existing_bd_by_date = {row["date"]: row["id"] for row in existing_bd_rows}
            incoming_dates = set()
            for bd in blackout_dates:
                d_val = bd.get("date")
                if not d_val:
                    continue
                d_parsed = (
                    datetime.strptime(d_val[:10], "%Y-%m-%d").date()
                    if isinstance(d_val, str)
                    else d_val
                )
                incoming_dates.add(d_parsed)
                title = bd.get("title") or "Holiday"
                tags = bd.get("tags") or []
                if d_parsed in existing_bd_by_date:
                    await conn.execute(
                        "UPDATE blackout_dates SET title = $1, tags = $2 WHERE id = $3",
                        title,
                        tags,
                        existing_bd_by_date[d_parsed],
                    )
                else:
                    await conn.execute(
                        "INSERT INTO blackout_dates (date, title, tags) VALUES ($1, $2, $3)",
                        d_parsed,
                        title,
                        tags,
                    )
            stale_dates = set(existing_bd_by_date.keys()) - incoming_dates
            if stale_dates:
                await conn.execute(
                    "DELETE FROM blackout_dates WHERE date = ANY($1::date[])",
                    list(stale_dates),
                )

            # 3. Upsert Levels and Classes / Sections
            levels = payload.get("levels") or []
            # Sections created before any real teacher exists simply have no
            # head teacher yet -- head_teacher_id is nullable for exactly this
            # case; the admin assigns one later via PUT /students/classes/{id}.
            # No fallback to `users` here: head_teacher_id REFERENCES
            # teachers(id), not users(id), so a users.id whose owner has
            # no teachers row is a foreign key violation that would
            # abort this entire save -- settings, calendar, every other
            # level -- the moment one such user exists.
            default_teacher_id = await conn.fetchval(
                "SELECT id FROM teachers ORDER BY id ASC LIMIT 1"
            )

            # Collected up front so a section that legitimately moves
            # between two levels in the same save (still present, just
            # under a different parent) is never treated as "removed"
            # by the level it used to belong to, purely because that
            # level happens to be processed before the section's UPDATE
            # actually moves it.
            all_incoming_section_ids = {
                sec["id"] for lvl in levels for sec in (lvl.get("sections") or []) if sec.get("id")
            }

            for lvl in levels:
                lvl_name = (lvl.get("name") or "Grade").strip()
                ordinal = lvl.get("ordinal")
                isced = lvl.get("isced_level")
                age_min = lvl.get("age_band_min")
                age_max = lvl.get("age_band_max")
                is_active = bool(lvl.get("is_active", True))
                level_id_in = lvl.get("level_id")

                lvl_id = None
                if level_id_in:
                    lvl_id = await conn.fetchval(
                        "SELECT level_id FROM levels WHERE level_id = $1", level_id_in
                    )
                if not lvl_id:
                    # No id given (a caller that fetched /structure and
                    # resent it without carrying level_id forward), or
                    # the id no longer exists. Fall back to matching by
                    # name only -- never by ordinal, which is exactly
                    # what let two grades swap labels when their
                    # ordinals were swapped instead of tracking identity.
                    lvl_id = await conn.fetchval(
                        "SELECT level_id FROM levels WHERE LOWER(name) = LOWER($1)", lvl_name
                    )

                if lvl_id:
                    await conn.execute(
                        """
                        UPDATE levels
                        SET name = $1, isced_level = $2, age_band_min = $3, age_band_max = $4, ordinal = $5, is_active = $6
                        WHERE level_id = $7
                        """,
                        lvl_name,
                        isced,
                        age_min,
                        age_max,
                        ordinal,
                        is_active,
                        lvl_id,
                    )
                else:
                    # No id, or the id no longer exists (stale client
                    # state) -- this is a new level, not a rename target.
                    lvl_id = await conn.fetchval(
                        """
                        INSERT INTO levels (name, isced_level, age_band_min, age_band_max, ordinal, is_active)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        RETURNING level_id
                        """,
                        lvl_name,
                        isced,
                        age_min,
                        age_max,
                        ordinal,
                        is_active,
                    )

                sections = lvl.get("sections") or []
                incoming_section_ids = set()
                for sec in sections:
                    sec_name = (sec.get("name") or f"{lvl_name} - A").strip()
                    sec_cap = int(sec.get("capacity") or 25)
                    sec_id_in = sec.get("id")

                    cid = None
                    if sec_id_in:
                        cid = await conn.fetchval("SELECT id FROM class WHERE id = $1", sec_id_in)
                    if not cid:
                        # Same rationale as the level fallback above.
                        # This still can't detect a rename with no id
                        # attached (the new name won't match the old
                        # row) -- that is exactly why id is now the
                        # primary key; this exists only so a caller that
                        # doesn't supply one yet doesn't get duplicate
                        # sections on every save.
                        cid = await conn.fetchval(
                            "SELECT id FROM class WHERE level_id = $1 AND LOWER(name) = LOWER($2)",
                            lvl_id,
                            sec_name,
                        )

                    if cid:
                        await conn.execute(
                            "UPDATE class SET name = $1, level_id = $2, capacity = $3, section_label = $4 WHERE id = $5",
                            sec_name,
                            lvl_id,
                            sec_cap,
                            _derive_section_label(sec_name),
                            cid,
                        )
                    elif is_active:
                        # A deactivated level must not accept new sections
                        # -- an existing one can still be edited above,
                        # but nothing new gets attached to it.
                        if active_year_id is None:
                            raise ValueError("No active academic year is set for this tenant")
                        cid = await conn.fetchval(
                            """
                            INSERT INTO class (name, level_id, capacity, head_teacher_id, academic_year_id, section_label)
                            VALUES ($1, $2, $3, $4, $5, $6)
                            RETURNING id
                            """,
                            sec_name,
                            lvl_id,
                            sec_cap,
                            default_teacher_id,
                            active_year_id,
                            _derive_section_label(sec_name),
                        )
                    if cid:
                        incoming_section_ids.add(cid)

                existing_section_rows = await conn.fetch(
                    "SELECT id FROM class WHERE level_id = $1", lvl_id
                )
                stale_section_ids = [
                    r["id"]
                    for r in existing_section_rows
                    if r["id"] not in incoming_section_ids
                    and r["id"] not in all_incoming_section_ids
                ]
                if stale_section_ids:
                    await self._block_if_classes_have_enrollment_history(conn, stale_section_ids)
                    affected = await conn.fetch(
                        "SELECT id, class_id FROM students WHERE class_id = ANY($1::bigint[])",
                        stale_section_ids,
                    )
                    await conn.execute(
                        "UPDATE students SET class_id = NULL WHERE class_id = ANY($1::bigint[])",
                        stale_section_ids,
                    )
                    for row in affected:
                        await self._record_class_change(
                            conn, row["id"], row["class_id"], None, changed_by
                        )
                    await conn.execute(
                        "DELETE FROM event_class_map WHERE class_id = ANY($1::bigint[])",
                        stale_section_ids,
                    )
                    await conn.execute(
                        "DELETE FROM class WHERE id = ANY($1::bigint[])", stale_section_ids
                    )

    async def get_academic_structure(self) -> dict:
        """Fetch complete saved academic structure & calendar for tenant."""
        # 1. Academic Settings
        settings_row = await self.pool.fetchrow(
            "SELECT system, academic_year, start_month, weekend_days FROM academic_settings ORDER BY id DESC LIMIT 1"
        )
        if settings_row:
            system = settings_row["system"] or "US"
            calendar = {
                "academic_year": settings_row["academic_year"],
                "start_month": settings_row["start_month"],
                "weekend_days": settings_row["weekend_days"] or ["Saturday", "Sunday"],
            }
        else:
            system = "US"
            calendar = {
                "academic_year": "2026-2027",
                "start_month": 9,
                "weekend_days": ["Saturday", "Sunday"],
            }

        # 2. Blackout Dates
        bd_rows = await self.pool.fetch(
            "SELECT date, title, tags FROM blackout_dates ORDER BY date ASC"
        )
        blackout_dates = [
            {"date": str(r["date"]), "title": r["title"], "tags": r["tags"] or []} for r in bd_rows
        ]

        # 3. Levels and Classes -- one query for all levels, one query for
        # every class under any of them (grouped in Python), instead of one
        # class query per level. This is a hot path: both require_tenant_live
        # and get_setup_state call it, up to twice per request, and a
        # curriculum with a dozen-plus grades used to mean 1 + N acquisitions
        # from a tenant pool capped at 5 connections.
        lvl_rows = await self.pool.fetch(
            "SELECT level_id, name, isced_level, age_band_min, age_band_max, ordinal, is_active FROM levels ORDER BY COALESCE(ordinal, 999), level_id ASC"
        )
        level_ids = [lr["level_id"] for lr in lvl_rows]
        class_rows = await self.pool.fetch(
            "SELECT id, name, capacity, level_id FROM class WHERE level_id = ANY($1::bigint[]) ORDER BY name ASC",
            level_ids,
        )
        sections_by_level: dict = {}
        for cr in class_rows:
            sections_by_level.setdefault(cr["level_id"], []).append(
                {"id": cr["id"], "name": cr["name"], "capacity": cr.get("capacity") or 25}
            )

        levels = []
        for lr in lvl_rows:
            sections = sections_by_level.get(lr["level_id"], [])
            levels.append(
                {
                    "level_id": lr["level_id"],
                    "name": lr["name"],
                    "isced_level": lr.get("isced_level") or 1,
                    "age_band_min": lr.get("age_band_min"),
                    "age_band_max": lr.get("age_band_max"),
                    "ordinal": lr.get("ordinal"),
                    "is_active": lr.get("is_active", True),
                    "sections": sections,
                }
            )

        # A saved calendar alone (settings_row) does not mean the school has a
        # real academic structure — it must have at least one active level
        # with at least one class section under it.
        has_structure = any(lvl["is_active"] and len(lvl["sections"]) > 0 for lvl in levels)
        return {
            "has_structure": has_structure,
            "system": system,
            "calendar": calendar,
            "blackout_dates": blackout_dates,
            "levels": levels,
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

    async def get_teacher_by_email(self, email: str) -> dict | None:
        """Case-insensitive lookup, for resolving a bulk-import row's
        head_teacher_email to a teachers.id -- there is no other identifier
        a flat import file could reasonably carry for this."""
        row = await self.pool.fetchrow(
            """
            SELECT t.id, t.name, u.email
            FROM teachers t
            JOIN users u ON t.id = u.id
            WHERE UPPER(u.email) = UPPER($1)
            """,
            (email or "").strip(),
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
    async def _record_class_change(
        self, conn, student_id: int, old_class_id, new_class_id, changed_by=None
    ) -> None:
        """Append one row to the placement history log. Callers are
        expected to run this inside the same transaction as the students
        UPDATE/INSERT it documents, so history can never disagree with the
        current class_id snapshot."""
        await conn.execute(
            """
            INSERT INTO student_class_history (student_id, old_class_id, new_class_id, changed_by)
            VALUES ($1, $2, $3, $4)
            """,
            student_id,
            old_class_id,
            new_class_id,
            parse_id(changed_by) if changed_by else None,
        )

    async def create_student(
        self,
        user_id,
        name: str,
        class_id: int,
        gender: str | None = None,
        birth_data: str | None = None,
        changed_by=None,
    ) -> int:
        u_id = parse_id(user_id)
        new_cid = parse_id(class_id) if class_id else None
        async with self.pool.acquire() as conn, conn.transaction():
            existing_cid = await conn.fetchval("SELECT class_id FROM students WHERE id = $1", u_id)
            await conn.execute(
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
                new_cid,
                gender,
                birth_data,
            )
            if new_cid != existing_cid:
                await self._record_class_change(conn, u_id, existing_cid, new_cid, changed_by)
        return u_id

    async def get_class_history(self, student_id) -> list[dict]:
        sid = parse_id(student_id)
        rows = await self.pool.fetch(
            """
            SELECT h.id, h.student_id, h.old_class_id, h.new_class_id, h.changed_by, h.changed_at,
                   oc.name AS old_class_name, nc.name AS new_class_name
            FROM student_class_history h
            LEFT JOIN class oc ON h.old_class_id = oc.id
            LEFT JOIN class nc ON h.new_class_id = nc.id
            WHERE h.student_id = $1
            ORDER BY h.changed_at ASC, h.id ASC
            """,
            sid,
        )
        return [dict(row) for row in rows]

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
                           'phone', pu.phone,
                           'relationship_type', m.relationship_type,
                           'is_primary_contact', m.is_primary_contact,
                           'can_approve', m.can_approve
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

            if isinstance(d.get("parents"), str):
                try:
                    d["parents"] = json.loads(d["parents"])
                except Exception:
                    d["parents"] = []
            results.append(d)
        return results

    async def add_student_parent_link(
        self,
        student_id,
        parent_id,
        relationship_type: str | None = None,
        is_primary_contact: bool = False,
        can_approve: bool = True,
    ) -> None:
        # ON CONFLICT DO UPDATE, not DO NOTHING: re-linking an already-linked
        # pair is how staff correct or update relationship_type /
        # is_primary_contact / can_approve after the fact (a custody
        # arrangement changing, a contact being added as primary). A silent
        # no-op would make those fields effectively write-once.
        await self.pool.execute(
            """
            INSERT INTO student_parent_map
                (student_id, parent_id, relationship_type, is_primary_contact, can_approve)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (student_id, parent_id) DO UPDATE SET
                relationship_type = EXCLUDED.relationship_type,
                is_primary_contact = EXCLUDED.is_primary_contact,
                can_approve = EXCLUDED.can_approve
            """,
            parse_id(student_id),
            parse_id(parent_id),
            relationship_type,
            is_primary_contact,
            can_approve,
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
        """Bare relationship-exists check -- says nothing about whether this
        parent is authorized to consent on the student's behalf. Use
        can_parent_approve_for_student for anything approval-shaped."""
        row = await self.pool.fetchrow(
            "SELECT 1 FROM student_parent_map WHERE student_id = $1 AND parent_id = $2",
            parse_id(student_id),
            parse_id(parent_id),
        )
        return row is not None

    async def can_parent_approve_for_student(self, student_id, parent_id) -> bool:
        """Whether this parent is both linked to the student AND flagged as
        allowed to approve on their behalf. student_parent_map has no
        concept of custody or legal consent authority -- can_approve is this
        product's own, narrower flag ("may this contact approve/pay for a
        school trip"), defaulting True so every pre-existing link keeps
        behaving exactly as before; a link must be explicitly created or
        edited with can_approve=false to be restricted. A student with no
        link to this parent at all returns False, same as before."""
        value = await self.pool.fetchval(
            "SELECT can_approve FROM student_parent_map WHERE student_id = $1 AND parent_id = $2",
            parse_id(student_id),
            parse_id(parent_id),
        )
        return bool(value)

    async def get_parent_email_for_student(self, student_id) -> str | None:
        # Deterministic: prefer the flagged primary contact, then the
        # lowest parent id -- LIMIT 1 with no ORDER BY previously returned
        # whichever linked parent the query planner felt like that day.
        row = await self.pool.fetchrow(
            """
            SELECT u.email
            FROM student_parent_map m
            JOIN parenets p ON m.parent_id = p.id
            JOIN users u ON p.id = u.id
            WHERE m.student_id = $1
            ORDER BY m.is_primary_contact DESC, p.id ASC
            LIMIT 1
            """,
            parse_id(student_id),
        )
        return row["email"] if row else None

    async def get_parent_for_student(self, student_id) -> dict | None:
        # Same determinism fix as get_parent_email_for_student -- this feeds
        # a student's own profile view, which only has room to display one
        # parent's name/email.
        row = await self.pool.fetchrow(
            """
            SELECT p.id, p.name, u.email
            FROM student_parent_map m
            JOIN parenets p ON m.parent_id = p.id
            JOIN users u ON p.id = u.id
            WHERE m.student_id = $1
            ORDER BY m.is_primary_contact DESC, p.id ASC
            LIMIT 1
            """,
            parse_id(student_id),
        )
        return dict(row) if row else None

    # =========================================================================
    # Classes
    # =========================================================================
    async def create_class(
        self,
        name: str,
        level_id: int,
        head_teacher_id=None,
        capacity: int = 25,
        is_active: bool = True,
        academic_year_id: int | None = None,
    ) -> int:
        h_id = parse_id(head_teacher_id) if head_teacher_id else None
        # academic_year_id is NOT NULL with no DB-side default (ADR 0002) --
        # resolve to the active year here so every caller doesn't have to.
        ay_id = (
            parse_id(academic_year_id)
            if academic_year_id
            else await self.get_active_academic_year_id()
        )
        if ay_id is None:
            raise ValueError("No active academic year is set for this tenant")
        clean_name = name.strip()
        return await self.pool.fetchval(
            """
            INSERT INTO class (name, level_id, head_teacher_id, capacity, is_active, academic_year_id, section_label)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            clean_name,
            parse_id(level_id),
            h_id,
            int(capacity or 25),
            is_active,
            ay_id,
            _derive_section_label(clean_name),
        )

    async def get_class_by_name_and_level(
        self, name: str, level_id: int, academic_year_id: int | None = None
    ) -> dict | None:
        ay_id = (
            parse_id(academic_year_id)
            if academic_year_id
            else await self.get_active_academic_year_id()
        )
        row = await self.pool.fetchrow(
            """
            SELECT id, name, level_id, head_teacher_id, COALESCE(capacity, 25) AS capacity,
                   is_active, created_at, academic_year_id
            FROM class
            WHERE LOWER(name) = LOWER($1) AND level_id = $2 AND academic_year_id = $3
            """,
            name.strip(),
            parse_id(level_id),
            ay_id,
        )
        return dict(row) if row else None

    async def upsert_class_by_name_and_level(
        self,
        name: str,
        level_id: int,
        head_teacher_id=UNSET,
        capacity: int | None = None,
        is_active: bool = True,
        academic_year_id: int | None = None,
    ) -> tuple[int, str]:
        """Idempotent-by-(name, level, academic year) create-or-update.
        Unlike the old create_class behavior -- which silently kept the
        existing row and dropped whatever capacity/head_teacher/is_active
        was just sent on a name collision -- this UPDATEs those fields,
        matching how levels already behave via upsert_level_by_name. That
        fix is what makes "re-upload a corrected import file" actually
        correct anything for classes, not just for grades.

        head_teacher_id uses the UNSET sentinel (module docstring): not
        mentioning it at all leaves the current head teacher alone (or
        creates a new class with no teacher); an explicit None clears it.
        This mirrors update_class's existing convention. Returns
        (class_id, "created"|"updated")."""
        ay_id = (
            parse_id(academic_year_id)
            if academic_year_id
            else await self.get_active_academic_year_id()
        )
        if ay_id is None:
            raise ValueError("No active academic year is set for this tenant")

        existing = await self.get_class_by_name_and_level(name, level_id, ay_id)
        if existing:
            await self.update_class(
                class_id=existing["id"],
                name=name,
                level_id=level_id,
                head_teacher_id=head_teacher_id,
                capacity=capacity,
                is_active=is_active,
            )
            return existing["id"], "updated"

        new_id = await self.create_class(
            name,
            level_id,
            head_teacher_id=None if head_teacher_id is UNSET else head_teacher_id,
            capacity=capacity if capacity is not None else 25,
            is_active=is_active,
            academic_year_id=ay_id,
        )
        return new_id, "created"

    async def get_class_by_id(self, class_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT c.id, c.name, c.level_id, c.head_teacher_id, c.created_at, COALESCE(c.capacity, 25) AS capacity,
                   c.is_active,
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
        head_teacher_id=UNSET,
        capacity: int | None = None,
        is_active: bool | None = None,
    ) -> dict:
        cid = parse_id(class_id)
        current = await self.get_class_by_id(cid)
        if not current:
            raise ValueError(f"Class {cid} not found")

        new_name = name.strip() if name is not None else current["name"]
        new_level_id = parse_id(level_id) if level_id is not None else current["level_id"]
        # UNSET means the caller didn't mention head_teacher_id at all --
        # keep the current value. An explicit None means "clear it", which
        # must actually reach the database rather than being treated the
        # same as "not mentioned".
        new_head = (
            current["head_teacher_id"] if head_teacher_id is UNSET else parse_id(head_teacher_id)
        )
        new_capacity = int(capacity) if capacity is not None else current.get("capacity", 25)
        new_active = is_active if is_active is not None else current.get("is_active", True)

        await self.pool.execute(
            """
            UPDATE class
            SET name = $1, level_id = $2, head_teacher_id = $3, capacity = $4, is_active = $5,
                section_label = $6
            WHERE id = $7
            """,
            new_name,
            new_level_id,
            new_head,
            new_capacity,
            new_active,
            _derive_section_label(new_name),
            cid,
        )
        return await self.get_class_by_id(cid)

    async def _block_if_classes_have_enrollment_history(self, conn, class_ids: list) -> None:
        """Refuse to touch class_ids that have any enrollment (and therefore
        possibly payment) history. Both enrollment.event_class_map_id and
        payments.enrollment_id are ON DELETE CASCADE from class, so deleting a
        class with existing enrollments would silently destroy that trip's
        payment record along with it — a class/level scoped delete must never
        be the thing that removes financial history."""
        if not class_ids:
            return
        enrollment_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM enrollment e
            JOIN event_class_map ecm ON e.event_class_map_id = ecm.id
            WHERE ecm.class_id = ANY($1::bigint[])
            """,
            class_ids,
        )
        if enrollment_count:
            raise ValueError(
                f"Cannot delete: {enrollment_count} enrollment(s), and any payments tied to "
                "them, exist for trips involving this class. Enrollment and payment history "
                "cannot be deleted."
            )

    async def delete_class(self, class_id: int, changed_by=None, audit_hook=None) -> bool:
        """`audit_hook`, if given, is an `async def hook(conn) -> None` invoked
        inside this same transaction right before it commits -- so an audit
        row is written if and only if the delete itself actually commits (see
        tests/integration/test_audit_log.py). Kept as an opaque callback
        rather than importing the audit domain here, so this repository has
        zero coupling to it; the service layer composes the two."""
        cid = parse_id(class_id)
        async with self.pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchval("SELECT id FROM class WHERE id = $1", cid)
            if not existing:
                raise ValueError(f"Class {cid} not found")

            await self._block_if_classes_have_enrollment_history(conn, [cid])

            # Unlink students if any -- record each as a placement
            # change (old=cid, new=NULL) before the row disappears.
            affected = await conn.fetch("SELECT id FROM students WHERE class_id = $1", cid)
            await conn.execute("UPDATE students SET class_id = NULL WHERE class_id = $1", cid)
            for row in affected:
                await self._record_class_change(conn, row["id"], cid, None, changed_by)
            await conn.execute("DELETE FROM event_class_map WHERE class_id = $1", cid)
            await conn.execute("DELETE FROM class WHERE id = $1", cid)
            if audit_hook is not None:
                await audit_hook(conn)
        return True

    async def delete_level(self, level_id: int, changed_by=None, audit_hook=None) -> bool:
        """See `delete_class`'s `audit_hook` docstring -- same contract."""
        lid = parse_id(level_id)
        async with self.pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchval("SELECT level_id FROM levels WHERE level_id = $1", lid)
            if not existing:
                raise ValueError(f"Level {lid} not found")

            class_rows = await conn.fetch("SELECT id FROM class WHERE level_id = $1", lid)
            class_ids = [c["id"] for c in class_rows]

            await self._block_if_classes_have_enrollment_history(conn, class_ids)

            if class_ids:
                affected = await conn.fetch(
                    "SELECT id, class_id FROM students WHERE class_id = ANY($1::bigint[])",
                    class_ids,
                )
                await conn.execute(
                    "UPDATE students SET class_id = NULL WHERE class_id = ANY($1::bigint[])",
                    class_ids,
                )
                for row in affected:
                    await self._record_class_change(
                        conn, row["id"], row["class_id"], None, changed_by
                    )
                await conn.execute(
                    "DELETE FROM event_class_map WHERE class_id = ANY($1::bigint[])",
                    class_ids,
                )
                await conn.execute("DELETE FROM class WHERE level_id = $1", lid)

            await conn.execute("DELETE FROM levels WHERE level_id = $1", lid)
            if audit_hook is not None:
                await audit_hook(conn)
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
            new_name,
            new_isced,
            new_min,
            new_max,
            new_ord,
            new_active,
            lid,
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

    async def reassign_student_class(
        self, student_id, new_class_id: int | None, changed_by=None
    ) -> bool:
        sid = parse_id(student_id)
        cid = parse_id(new_class_id) if new_class_id else None
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT class_id FROM students WHERE id = $1 FOR UPDATE", sid)
            if row is None:
                raise ValueError(f"Student {sid} not found")
            existing_cid = row["class_id"]
            if cid != existing_cid:
                await conn.execute("UPDATE students SET class_id = $1 WHERE id = $2", cid, sid)
                await self._record_class_change(conn, sid, existing_cid, cid, changed_by)
        return True

    async def bulk_reassign_students(
        self, student_ids: list[int], new_class_id: int | None, changed_by=None
    ) -> dict:
        """Reassign every student_id that actually exists; report both the
        real affected count and which requested ids matched no student, so
        a client can't be told "50 enrolled" when some (or all) ids were
        bogus and nothing happened for them."""
        if not student_ids:
            return {"updated_count": 0, "missing_student_ids": []}
        sids = [parse_id(s) for s in student_ids]
        cid = parse_id(new_class_id) if new_class_id else None
        async with self.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                "SELECT id, class_id FROM students WHERE id = ANY($1::bigint[]) FOR UPDATE",
                sids,
            )
            found = {row["id"]: row["class_id"] for row in rows}
            missing = sorted(set(sids) - set(found.keys()))
            if found:
                await conn.execute(
                    "UPDATE students SET class_id = $1 WHERE id = ANY($2::bigint[])",
                    cid,
                    list(found.keys()),
                )
                for sid, old_cid in found.items():
                    if old_cid != cid:
                        await self._record_class_change(conn, sid, old_cid, cid, changed_by)
        return {"updated_count": len(found), "missing_student_ids": missing}

    async def get_classes_by_head_teacher(self, teacher_id) -> list[dict]:
        """All classes this teacher heads -- there is no uniqueness
        constraint on head_teacher_id, so a teacher can legitimately head
        more than one section. Callers that gate access on "is this teacher
        the head of class X" must check membership in the full list, not
        assume a single class."""
        rows = await self.pool.fetch(
            """
            SELECT c.id, c.name, c.level_id, c.head_teacher_id, COALESCE(c.capacity, 25) AS capacity, l.name AS level_name
            FROM class c
            JOIN levels l ON c.level_id = l.level_id
            WHERE c.head_teacher_id = $1
            ORDER BY c.id ASC
            """,
            parse_id(teacher_id),
        )
        return [dict(row) for row in rows]

    async def get_all_classes(self) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT c.id, c.name, c.level_id, c.head_teacher_id, c.created_at, COALESCE(c.capacity, 25) AS capacity,
                   c.is_active,
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
    # Structure Import (bulk grades + classes from a CSV/XLSX file)
    # =========================================================================
    @staticmethod
    def _parse_import_bool(value, default: bool = True) -> bool:
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in ("true", "1", "yes", "y")

    @staticmethod
    def _parse_import_int(value, field_label: str, errors: list[str]) -> int | None:
        raw = "" if value is None else str(value).strip()
        if raw == "":
            return None
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            errors.append(f"{field_label} must be a whole number, got {raw!r}")
            return None

    async def _plan_new_grade_ordinals(self, rows: list[dict]) -> dict[str, int]:
        """New grades appearing in this file, in first-appearance order, get
        ordinals continuing on from whatever's already the highest -- not the
        single-row default of 1 create_level normally uses, which would tie
        every new grade in a multi-grade import at the same ordinal."""
        max_ordinal = await self.pool.fetchval("SELECT MAX(ordinal) FROM levels") or 0
        existing_names = {
            (lvl["name"] or "").strip().lower() for lvl in await self.get_all_levels()
        }
        plan: dict[str, int] = {}
        next_ordinal = max_ordinal
        for row in rows:
            gname = (row.get("grade_name") or "").strip()
            if not gname or gname.lower() in existing_names or gname in plan:
                continue
            next_ordinal += 1
            plan[gname] = next_ordinal
        return plan

    async def _resolve_import_row(
        self, row: dict, row_number: int, ordinal_plan: dict[str, int], apply: bool
    ) -> dict:
        """Validate one import row and, only if apply=True, write it.
        Preview and commit share this exact method so they can never
        disagree about what's valid -- preview simply calls it with
        apply=False, performing the same read-side lookups (does this grade
        /class/teacher already exist?) without ever calling the upsert
        writer methods. Never issues a DELETE; a row that fails is skipped,
        not raised, so one bad row in a batch doesn't stop the rest."""
        errors: list[str] = []
        warnings: list[str] = []

        grade_name = (row.get("grade_name") or "").strip()
        class_name = (row.get("class_name") or "").strip() or None
        if not grade_name:
            return {
                "row_number": row_number,
                "grade_name": "",
                "class_name": class_name,
                "action": "skipped",
                "status": "error",
                "errors": ["grade_name is required"],
                "warnings": [],
            }

        isced_level = self._parse_import_int(
            row.get("grade_isced_level"), "grade_isced_level", errors
        )
        age_band_min = self._parse_import_int(row.get("grade_age_min"), "grade_age_min", errors)
        age_band_max = self._parse_import_int(row.get("grade_age_max"), "grade_age_max", errors)
        grade_ordinal = self._parse_import_int(row.get("grade_ordinal"), "grade_ordinal", errors)
        grade_active = self._parse_import_bool(row.get("grade_active"), default=True)

        existing_level = await self.get_level_by_name(grade_name)
        grade_action = "update_grade" if existing_level else "create_grade"
        if grade_ordinal is None and not existing_level:
            grade_ordinal = ordinal_plan.get(grade_name)

        class_capacity = None
        class_active = True
        head_teacher_id = UNSET
        class_action = "grade_only"
        if class_name:
            class_capacity = self._parse_import_int(
                row.get("class_capacity"), "class_capacity", errors
            )
            class_active = self._parse_import_bool(row.get("class_active"), default=True)

            teacher_email = (row.get("head_teacher_email") or "").strip()
            if teacher_email:
                teacher = await self.get_teacher_by_email(teacher_email)
                if teacher:
                    head_teacher_id = teacher["id"]
                else:
                    warnings.append(
                        f"No teacher found for head_teacher_email {teacher_email!r} -- class will have no head teacher"
                    )

            if existing_level:
                existing_class = await self.get_class_by_name_and_level(
                    class_name, existing_level["level_id"]
                )
                class_action = "update_class" if existing_class else "create_class"
            else:
                # Grade is new -- its class can only ever be a create, never
                # an update, since nothing with that level_id can exist yet.
                class_action = "create_class"

        if errors:
            return {
                "row_number": row_number,
                "grade_name": grade_name,
                "class_name": class_name,
                "action": "skipped",
                "status": "error",
                "errors": errors,
                "warnings": warnings,
            }

        action = grade_action if not class_name else f"{grade_action}+{class_action}"

        if not apply:
            return {
                "row_number": row_number,
                "grade_name": grade_name,
                "class_name": class_name,
                "action": action,
                "status": "valid",
                "errors": [],
                "warnings": warnings,
            }

        try:
            level_id, _ = await self.upsert_level_by_name(
                grade_name,
                isced_level=isced_level,
                age_band_min=age_band_min,
                age_band_max=age_band_max,
                ordinal=grade_ordinal,
                is_active=grade_active,
            )
            if class_name:
                await self.upsert_class_by_name_and_level(
                    class_name,
                    level_id,
                    head_teacher_id=head_teacher_id,
                    capacity=class_capacity,
                    is_active=class_active,
                )
        except ValueError as exc:
            return {
                "row_number": row_number,
                "grade_name": grade_name,
                "class_name": class_name,
                "action": "skipped",
                "status": "error",
                "errors": [str(exc)],
                "warnings": warnings,
            }

        return {
            "row_number": row_number,
            "grade_name": grade_name,
            "class_name": class_name,
            "action": action,
            "status": "valid",
            "errors": [],
            "warnings": warnings,
        }

    async def preview_import_rows(self, rows: list[dict]) -> dict:
        """Read-only: validates every row and reports what WOULD happen,
        without writing anything."""
        ordinal_plan = await self._plan_new_grade_ordinals(rows)
        results = [
            await self._resolve_import_row(row, row_number, ordinal_plan, apply=False)
            for row_number, row in enumerate(rows, start=2)
        ]
        valid = sum(1 for r in results if r["status"] == "valid")
        return {
            "total_rows": len(results),
            "valid_rows": valid,
            "error_rows": len(results) - valid,
            "rows": results,
        }

    async def import_academic_rows(self, rows: list[dict]) -> dict:
        """The additive-only bulk writer: applies every valid row (partial
        success -- an invalid row is skipped and reported, not a reason to
        abort the whole file), never deletes anything. Safe to re-run: the
        underlying upsert_* methods are idempotent by name, so a corrected
        re-upload of the same file just applies the corrections."""
        ordinal_plan = await self._plan_new_grade_ordinals(rows)
        results = [
            await self._resolve_import_row(row, row_number, ordinal_plan, apply=True)
            for row_number, row in enumerate(rows, start=2)
        ]
        applied = [r for r in results if r["status"] == "valid"]
        return {
            "total_rows": len(results),
            "applied_rows": len(applied),
            "skipped_rows": len(results) - len(applied),
            "created_grades": sum(1 for r in applied if r["action"].startswith("create_grade")),
            "updated_grades": sum(1 for r in applied if r["action"].startswith("update_grade")),
            "created_classes": sum(1 for r in applied if r["action"].endswith("create_class")),
            "updated_classes": sum(1 for r in applied if r["action"].endswith("update_class")),
            "row_results": results,
        }

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
        class_mappings: list[
            dict
        ],  # list of {"class_id": int, "ticket_price": float, "costbudget_id": int | None, "budget_description": str | None, "budget_price": float | None}
    ) -> dict:
        event_id = None
        async with self.pool.acquire() as conn, conn.transaction():
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
                await conn.fetchval(
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

    async def update_event(
        self,
        event_id: int,
        title: str,
        description: str,
        address: str | None,
        school_subsidy: float,
        date_val: datetime,
    ) -> dict | None:
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
        async with self.pool.acquire() as conn, conn.transaction():
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
    async def create_enrollment(
        self, student_id, event_class_map_id: int, state: str, teacher_id=None, parent_id=None
    ) -> int:
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

    async def update_enrollment_state(
        self, enrollment_id: int, state: str, teacher_id=None, parent_id=None
    ) -> bool:
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

    async def get_enrollment_by_student_and_map(
        self, student_id, event_class_map_id: int
    ) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT id, student_id, event_class_map_id, state, teacher_id, parent_id, created_at FROM enrollment WHERE student_id = $1 AND event_class_map_id = $2",
            parse_id(student_id),
            parse_id(event_class_map_id),
        )
        return dict(row) if row else None

    async def get_enrollments_for_teacher(self, teacher_id) -> list[dict]:
        # `requested_by_student` used to be excluded here on the assumption a
        # teacher never creates a row in that state -- only a student did, by
        # self-service, before any teacher or parent had touched it. Since
        # students/router.py::enroll_student was changed to require explicit
        # parent consent (a teacher-initiated request now also starts at
        # requested_by_student instead of jumping straight to
        # approved_by_teacher), that assumption broke: a teacher who requests
        # enrollment for their own student could no longer see the request
        # they had just made -- it vanished from their own class roster until
        # a parent acted on it, even though EventDetailsView.vue already
        # renders a "Waiting on the parent" state for exactly this case.
        # `rejected_by_parent` stays excluded on purpose: that's a dead end
        # the UI treats as "not enrolled" so the row disappears and a fresh
        # request can be made, not as an active state to keep showing.
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
              AND en.state != 'rejected_by_parent'
            """,
            parse_id(teacher_id),
        )
        return [dict(row) for row in rows]

    async def get_all_enrollments(self) -> list[dict]:
        """The whole tenant's roster -- for school_admin/super_admin, who
        lead no single class and so cannot use get_enrollments_for_teacher()."""
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
            WHERE en.state NOT IN ('requested_by_student', 'rejected_by_parent')
            """
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
        result = await self.pool.execute(
            "DELETE FROM enrollment WHERE id = $1", parse_id(enrollment_id)
        )
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
        row = await self.pool.fetchrow(
            "SELECT id, enrollment_id, amount, status, created_at FROM payments WHERE enrollment_id = $1",
            parse_id(enrollment_id),
        )
        return dict(row) if row else None

    # =========================================================================
    # Feedbacks
    # =========================================================================
    async def create_event_feedback(
        self, event_id: int, user_id, rating: int, comments: str | None
    ) -> int:
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
        # user_name is best-effort: name lives per-role in teachers/students/
        # parents, not on users itself, so a manager/school_admin/super_admin
        # who happens to leave feedback (the endpoint has no role gate) falls
        # back to their email instead.
        rows = await self.pool.fetch(
            """
            SELECT f.id, f.event_id, f.user_id, f.rating, f.comments, f.created_at,
                   COALESCE(t.name, s.name, p.name, u.email) AS user_name
            FROM event_feedback f
            JOIN users u ON u.id = f.user_id
            LEFT JOIN teachers t ON t.id = f.user_id
            LEFT JOIN students s ON s.id = f.user_id
            LEFT JOIN parenets p ON p.id = f.user_id
            WHERE f.event_id = $1
            ORDER BY f.created_at DESC
            """,
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
        audit_hook=None,
    ) -> UUID:
        """`audit_hook`, if given: see delete_class's docstring for the contract."""
        async with self.pool.acquire() as conn, conn.transaction():
            record_id = await conn.fetchval(
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
            if audit_hook is not None:
                await audit_hook(conn)
        return record_id

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
        return (
            dict(row)
            if row
            else {
                "student_count": 0,
                "class_count": 0,
                "enrollment_count": 0,
                "event_count": 0,
            }
        )

    # =========================================================================
    # Resource Types (workflow & resource schema)
    # =========================================================================
    async def create_resource_type(
        self, name: str, category: str, is_custom: bool = False, created_by_user_id=None
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
        self,
        event_id: int,
        resource_type_id: int,
        description: str | None,
        quantity: int,
        added_by_user_id: int,
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

    async def delete_resource(self, resource_id: int) -> None:
        await self.pool.execute(
            "DELETE FROM resources WHERE id = $1",
            parse_id(resource_id),
        )

    async def update_resource(
        self,
        resource_id: int,
        resource_type_id: int | None,
        description: str | None,
        quantity: int | None,
        updated_by_user_id: int,
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
        self,
        resource_id: int,
        unit_price: float,
        total_cost: float,
        currency: str,
        set_by_user_id: int,
    ) -> int:
        event_id = await self.pool.fetchval(
            "SELECT event_id FROM resources WHERE id = $1", parse_id(resource_id)
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

    async def get_all_event_teachers(self) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT id, email, role FROM users WHERE role = 'event_teacher'"
        )
        return [dict(row) for row in rows]

    # =========================================================================
    # Academic Years (ADR 0002 / ADR 0003)
    # =========================================================================
    async def get_active_academic_year_id(self) -> int | None:
        return await self.pool.fetchval(
            "SELECT id FROM academic_years WHERE status = 'active' LIMIT 1"
        )

    async def get_academic_year_by_id(self, year_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT id, name, status, start_date, end_date, rolled_from_id, created_at "
            "FROM academic_years WHERE id = $1",
            parse_id(year_id),
        )
        return dict(row) if row else None

    async def get_all_academic_years(self) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT id, name, status, start_date, end_date, rolled_from_id, created_at "
            "FROM academic_years ORDER BY id DESC"
        )
        return [dict(row) for row in rows]

    async def create_academic_year(self, name: str, start_date=None, end_date=None) -> int:
        return await self.pool.fetchval(
            """
            INSERT INTO academic_years (name, status, start_date, end_date)
            VALUES ($1, 'planned', $2, $3)
            RETURNING id
            """,
            name.strip(),
            start_date,
            end_date,
        )

    # =========================================================================
    # Year Rollover (ADR 0003)
    # =========================================================================
    async def get_rollover_plan_facts(self, from_year_id: int) -> list[dict]:
        """One row per `status = 'enrolled'` student currently placed in
        `from_year_id`. A class clones forward to the next level (ordinal + 1)
        only if it is `is_active` and has a parseable `section_label` -- a
        student whose class doesn't clone forward has no computed target
        (the service layer turns that into a 'hold')."""
        rows = await self.pool.fetch(
            """
            SELECT
                s.id AS student_id,
                c.id AS from_class_id,
                c.capacity AS from_class_capacity,
                c.is_active AS from_class_active,
                c.section_label,
                l.ordinal AS from_ordinal,
                nl.level_id AS to_level_id
            FROM students s
            JOIN class c ON c.id = s.class_id
            JOIN levels l ON l.level_id = c.level_id
            LEFT JOIN levels nl ON nl.ordinal = l.ordinal + 1
            WHERE c.academic_year_id = $1 AND s.status = 'enrolled'
            """,
            parse_id(from_year_id),
        )
        return [dict(row) for row in rows]

    async def create_year_rollover(self, from_year_id, to_year_id, created_by=None) -> dict:
        row = await self.pool.fetchrow(
            """
            INSERT INTO year_rollover (from_year_id, to_year_id, created_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (from_year_id, to_year_id) DO NOTHING
            RETURNING *
            """,
            parse_id(from_year_id),
            parse_id(to_year_id),
            parse_id(created_by) if created_by else None,
        )
        if row:
            return dict(row)
        return await self.get_year_rollover_by_years(from_year_id, to_year_id)

    async def get_year_rollover_by_years(self, from_year_id, to_year_id) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM year_rollover WHERE from_year_id = $1 AND to_year_id = $2",
            parse_id(from_year_id),
            parse_id(to_year_id),
        )
        return dict(row) if row else None

    async def get_year_rollover_by_id(self, rollover_id) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM year_rollover WHERE id = $1", parse_id(rollover_id)
        )
        return dict(row) if row else None

    async def update_rollover_state(
        self, rollover_id, state: str, summary: dict | None = None
    ) -> None:
        await self.pool.execute(
            """
            UPDATE year_rollover
            SET state = $1, summary = COALESCE($2, summary), updated_at = CURRENT_TIMESTAMP
            WHERE id = $3
            """,
            state,
            json.dumps(summary) if summary is not None else None,
            parse_id(rollover_id),
        )

    async def upsert_rollover_lines(self, rollover_id: int, lines: list[dict]) -> None:
        """Upsert one line per student_id, preserving any existing
        `override_action` an admin already set. Deletes lines for students no
        longer in `lines` (e.g. withdrawn since the last generate/preview) --
        'excluded, not held', per ADR 0003."""
        rid = parse_id(rollover_id)
        student_ids = [parse_id(line["student_id"]) for line in lines]
        async with self.pool.acquire() as conn, conn.transaction():
            if student_ids:
                await conn.execute(
                    "DELETE FROM year_rollover_line WHERE rollover_id = $1 AND student_id != ALL($2::bigint[])",
                    rid,
                    student_ids,
                )
            else:
                await conn.execute("DELETE FROM year_rollover_line WHERE rollover_id = $1", rid)
            for line in lines:
                await conn.execute(
                    """
                    INSERT INTO year_rollover_line
                        (rollover_id, student_id, from_class_id, to_level_id, to_section_label,
                         proposed_action, exception_code)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (rollover_id, student_id) DO UPDATE SET
                        from_class_id = EXCLUDED.from_class_id,
                        proposed_action = EXCLUDED.proposed_action,
                        exception_code = EXCLUDED.exception_code,
                        -- Once a line carries an override_action, its target
                        -- was deliberately chosen by an admin (possibly via
                        -- resolving a hold to a manually-picked level/section)
                        -- -- a recompute must not silently overwrite that
                        -- choice back to the engine's raw proposal.
                        to_level_id = CASE
                            WHEN year_rollover_line.override_action IS NULL THEN EXCLUDED.to_level_id
                            ELSE year_rollover_line.to_level_id
                        END,
                        to_section_label = CASE
                            WHEN year_rollover_line.override_action IS NULL THEN EXCLUDED.to_section_label
                            ELSE year_rollover_line.to_section_label
                        END
                    """,
                    rid,
                    parse_id(line["student_id"]),
                    parse_id(line["from_class_id"]) if line.get("from_class_id") else None,
                    parse_id(line["to_level_id"]) if line.get("to_level_id") else None,
                    line.get("to_section_label"),
                    line["proposed_action"],
                    line.get("exception_code"),
                )

    async def get_rollover_lines(self, rollover_id: int) -> list[dict]:
        rows = await self.pool.fetch(
            """
            SELECT rl.*, s.name AS student_name, fc.name AS from_class_name, tl.name AS to_level_name
            FROM year_rollover_line rl
            JOIN students s ON s.id = rl.student_id
            LEFT JOIN class fc ON fc.id = rl.from_class_id
            LEFT JOIN levels tl ON tl.level_id = rl.to_level_id
            WHERE rl.rollover_id = $1
            ORDER BY tl.ordinal NULLS LAST, rl.to_section_label, s.name
            """,
            parse_id(rollover_id),
        )
        return [dict(row) for row in rows]

    async def get_rollover_line_by_id(self, line_id: int) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM year_rollover_line WHERE id = $1", parse_id(line_id)
        )
        return dict(row) if row else None

    async def set_rollover_line_override(
        self,
        line_id: int,
        override_action: str | None,
        to_level_id: int | None = None,
        to_section_label: str | None = None,
    ) -> dict | None:
        """`to_level_id`/`to_section_label` let an admin resolve a 'hold' line's
        target when overriding it to 'promote'; omitted (None), they leave the
        line's existing target untouched."""
        row = await self.pool.fetchrow(
            """
            UPDATE year_rollover_line
            SET override_action = $1,
                to_level_id = COALESCE($2, to_level_id),
                to_section_label = COALESCE($3, to_section_label)
            WHERE id = $4
            RETURNING *
            """,
            override_action,
            parse_id(to_level_id) if to_level_id else None,
            to_section_label,
            parse_id(line_id),
        )
        return dict(row) if row else None

    async def commit_year_rollover(self, rollover_id: int, changed_by=None) -> dict:
        """The rollover commit, per ADR 0003: one transaction, re-entrant.

        Adapted from the original spec to the year-scoped `class` model (ADR
        0002) already shipped -- there is no separate placement table to open
        or close; "closing" a placement is simply repointing
        `students.class_id` at the new year's (already-immutable) class row.
        """
        rid = parse_id(rollover_id)
        cb = parse_id(changed_by) if changed_by else None
        async with self.pool.acquire() as conn, conn.transaction():
            rollover = await conn.fetchrow(
                "SELECT * FROM year_rollover WHERE id = $1 FOR UPDATE", rid
            )
            if not rollover:
                raise ValueError(f"Rollover {rid} not found")
            if rollover["state"] == "committed":
                # Re-entrant: a retried commit request after the first
                # already succeeded returns the existing result, not an error.
                return dict(rollover)
            if rollover["state"] != "previewed":
                raise ValueError(
                    f"Rollover must be previewed before it can be committed "
                    f"(current state: {rollover['state']})"
                )

            from_year_id = rollover["from_year_id"]
            to_year_id = rollover["to_year_id"]

            await conn.execute(
                "UPDATE year_rollover SET state = 'committing', updated_at = CURRENT_TIMESTAMP WHERE id = $1",
                rid,
            )

            lines = await conn.fetch(
                "SELECT * FROM year_rollover_line WHERE rollover_id = $1 AND applied_at IS NULL",
                rid,
            )

            # 1. Ensure a to_year class exists for every distinct
            #    (to_level_id, to_section_label) a promote line targets,
            #    cloned from its source class's capacity/head_teacher.
            #    ON CONFLICT DO NOTHING absorbs a retry after a partial crash.
            resolved_class_ids: dict[tuple, int] = {}
            for line in lines:
                effective = line["override_action"] or line["proposed_action"]
                if effective != "promote":
                    continue
                key = (line["to_level_id"], line["to_section_label"])
                if key in resolved_class_ids or not line["from_class_id"]:
                    continue
                source = await conn.fetchrow(
                    "SELECT capacity, head_teacher_id FROM class WHERE id = $1",
                    line["from_class_id"],
                )
                level_row = await conn.fetchrow(
                    "SELECT name FROM levels WHERE level_id = $1", line["to_level_id"]
                )
                target_name = f"{level_row['name']} - {line['to_section_label']}"
                new_class_id = await conn.fetchval(
                    """
                    INSERT INTO class (name, level_id, head_teacher_id, capacity, is_active,
                                        academic_year_id, section_label)
                    VALUES ($1, $2, $3, $4, TRUE, $5, $6)
                    ON CONFLICT (name, academic_year_id) DO NOTHING
                    RETURNING id
                    """,
                    target_name,
                    line["to_level_id"],
                    source["head_teacher_id"],
                    source["capacity"],
                    to_year_id,
                    line["to_section_label"],
                )
                if new_class_id is None:
                    new_class_id = await conn.fetchval(
                        "SELECT id FROM class WHERE name = $1 AND academic_year_id = $2",
                        target_name,
                        to_year_id,
                    )
                resolved_class_ids[key] = new_class_id

            # 2. Apply each line's effective action (override, if any set, else
            #    the engine's proposal). 'hold' lines are left unresolved --
            #    no class_id change, no applied_at stamp, so they stay
            #    visibly outstanding for a future rollover attempt or a
            #    manual fix after commit.
            to_year_row = await conn.fetchrow(
                "SELECT end_date FROM academic_years WHERE id = $1", from_year_id
            )
            exit_date = to_year_row["end_date"] if to_year_row else None
            applied_line_ids = []
            for line in lines:
                effective = line["override_action"] or line["proposed_action"]
                if effective == "promote":
                    target_class_id = resolved_class_ids.get(
                        (line["to_level_id"], line["to_section_label"])
                    )
                    if not target_class_id:
                        # A promote line with no resolvable target is a data
                        # integrity problem (e.g. its from_class was deleted
                        # between preview and commit), not a normal outcome
                        # -- raise and let the whole transaction roll back
                        # rather than silently leaving one student unmoved
                        # while everyone else's promotion is stamped applied.
                        raise ValueError(
                            f"Rollover line {line['id']} (student {line['student_id']}) "
                            f"could not resolve a target class for level={line['to_level_id']}, "
                            f"section={line['to_section_label']!r} -- aborting commit"
                        )
                    await conn.execute(
                        "UPDATE students SET class_id = $1 WHERE id = $2",
                        target_class_id,
                        line["student_id"],
                    )
                    await conn.execute(
                        "UPDATE year_rollover_line SET to_class_id = $1 WHERE id = $2",
                        target_class_id,
                        line["id"],
                    )
                    await self._record_class_change(
                        conn, line["student_id"], line["from_class_id"], target_class_id, cb
                    )
                    applied_line_ids.append(line["id"])
                elif effective in ("graduate", "withdraw"):
                    new_status = "graduated" if effective == "graduate" else "withdrawn"
                    await conn.execute(
                        "UPDATE students SET class_id = NULL, status = $1, exited_on = $2 WHERE id = $3",
                        new_status,
                        exit_date,
                        line["student_id"],
                    )
                    await self._record_class_change(
                        conn, line["student_id"], line["from_class_id"], None, cb
                    )
                    applied_line_ids.append(line["id"])
                # 'hold': nothing to apply -- left out of applied_line_ids on purpose.

            await conn.execute(
                "UPDATE year_rollover_line SET applied_at = CURRENT_TIMESTAMP "
                "WHERE id = ANY($1::bigint[]) AND applied_at IS NULL",
                applied_line_ids,
            )

            # 3. Flip academic-year states. from_year first: the
            #    one-active-year partial unique index rejects the reverse
            #    order (both years being 'active' at once, even briefly).
            await conn.execute(
                "UPDATE academic_years SET status = 'closed' WHERE id = $1", from_year_id
            )
            await conn.execute(
                "UPDATE academic_years SET status = 'active', rolled_from_id = $1 WHERE id = $2",
                from_year_id,
                to_year_id,
            )

            def _count(action: str) -> int:
                return sum(
                    1
                    for line in lines
                    if (line["override_action"] or line["proposed_action"]) == action
                )

            summary = {
                "promoted": _count("promote"),
                "graduated": _count("graduate"),
                "withdrawn": _count("withdraw"),
                "held": _count("hold"),
            }
            await conn.execute(
                "UPDATE year_rollover SET state = 'committed', summary = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                json.dumps(summary),
                rid,
            )
            return dict(await conn.fetchrow("SELECT * FROM year_rollover WHERE id = $1", rid))
