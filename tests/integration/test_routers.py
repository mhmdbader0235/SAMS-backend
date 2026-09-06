"""Integration tests for routers."""

from datetime import UTC, datetime

import asyncpg
from httpx import AsyncClient

from app.domains.tenant.tenant_repository import TenantRepository
from tests.integration._helpers import register_school_admin


# =============================================================================
# Authentication Tests
# =============================================================================
class TestAuthRouter:
    async def test_register_and_login_super_admin(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import SUPER_ADMIN_BOOTSTRAP_CODE

        reg_payload = {
            "email": "sa@desk.com",
            "password": "sapassword123",
            "role": "super_admin",
            "invite_code": SUPER_ADMIN_BOOTSTRAP_CODE,
        }
        reg_resp = await test_client.post("/api/v1/auth/register", json=reg_payload)
        assert reg_resp.status_code == 200
        assert "access_token" in reg_resp.json()

        login_payload = {
            "email": "sa@desk.com",
            "password": "sapassword123",
        }
        login_resp = await test_client.post("/api/v1/auth/login", json=login_payload)
        assert login_resp.status_code == 200
        assert "access_token" in login_resp.json()

    async def test_register_and_login_parent(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        reg_payload = {
            "email": "parent@school.com",
            "password": "parentpassword",
            "role": "parent",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        reg_resp = await test_client.post("/api/v1/auth/register", json=reg_payload)
        assert reg_resp.status_code == 200
        token = reg_resp.json()["access_token"]

        me_resp = await test_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["role"] == "parent"
        assert me_resp.json()["tenant_id"] == "tenant_a"

    async def test_register_and_login_teacher(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        reg_payload = {
            "email": "teacher@school.com",
            "password": "teacherpass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        reg_resp = await test_client.post("/api/v1/auth/register", json=reg_payload)
        assert reg_resp.status_code == 200
        token = reg_resp.json()["access_token"]

        me_resp = await test_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["role"] == "teacher"

    async def test_register_and_login_student(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        # Student registration automatically maps level and class if none exists
        reg_payload = {
            "email": "student@school.com",
            "password": "studentpass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        reg_resp = await test_client.post("/api/v1/auth/register", json=reg_payload)
        assert reg_resp.status_code == 200
        token = reg_resp.json()["access_token"]

        me_resp = await test_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["role"] == "student"


# =============================================================================
# Student and Class Router Tests
# =============================================================================
class TestStudentsAndClassesRouter:
    async def test_create_level_stores_and_returns_the_real_requested_values(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: POST /levels must persist and echo the values actually
        given, not silently substitute the repository's bootstrap defaults
        (isced_level=1, age_band_min=6, age_band_max=7, ordinal=1) while
        echoing the request payload back as if it had been stored."""
        token = await register_school_admin(test_client, "admin_levels_real@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        payload = {
            "name": "Grade 9",
            "isced_level": 2,
            "age_band_min": 14,
            "age_band_max": 15,
            "ordinal": 9,
        }
        resp = await test_client.post("/api/v1/students/levels", json=payload, headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["isced_level"] == 2
        assert body["age_band_min"] == 14
        assert body["age_band_max"] == 15
        assert body["ordinal"] == 9

        # The response must reflect what was actually written, not just what
        # was asked for -- verify by re-reading independently of the create
        # response.
        listing = await test_client.get("/api/v1/students/levels", headers=headers)
        stored = next(lvl for lvl in listing.json() if lvl["level_id"] == body["level_id"])
        assert stored["isced_level"] == 2
        assert stored["age_band_min"] == 14
        assert stored["age_band_max"] == 15
        assert stored["ordinal"] == 9

    async def test_get_levels_sorted_by_ordinal_not_alphabetically(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant (AGENTS.md): natural grade order everywhere a level list
        is rendered -- "Grade 10" must not land between "Grade 1" and
        "Grade 2"."""
        token = await register_school_admin(test_client, "admin_levels_sort@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        for name, ordinal in [("Grade 10", 10), ("Grade 2", 2), ("Grade 1", 1)]:
            r = await test_client.post(
                "/api/v1/students/levels", json={"name": name, "ordinal": ordinal}, headers=headers
            )
            assert r.status_code == 200, r.text

        listing = await test_client.get("/api/v1/students/levels", headers=headers)
        assert listing.status_code == 200
        names = [lvl["name"] for lvl in listing.json()]
        assert names == ["Grade 1", "Grade 2", "Grade 10"], names
        # The frontend needs ordinal/is_active to sort/gray-out correctly.
        assert all("ordinal" in lvl and "is_active" in lvl for lvl in listing.json())

    async def test_update_level_partial_rename_preserves_other_fields_and_active_state(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: a partial PUT (e.g. rename only) must not null out the
        fields it didn't mention, and must not silently reactivate a level
        that was deliberately deactivated."""
        token = await register_school_admin(test_client, "admin_levels_partial@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        create_resp = await test_client.post(
            "/api/v1/students/levels",
            json={
                "name": "Grade 11",
                "isced_level": 3,
                "age_band_min": 16,
                "age_band_max": 17,
                "ordinal": 11,
            },
            headers=headers,
        )
        level_id = create_resp.json()["level_id"]

        deactivate_resp = await test_client.put(
            f"/api/v1/students/levels/{level_id}", json={"is_active": False}, headers=headers
        )
        assert deactivate_resp.status_code == 200, deactivate_resp.text
        assert deactivate_resp.json()["is_active"] is False

        rename_resp = await test_client.put(
            f"/api/v1/students/levels/{level_id}",
            json={"name": "Grade 11 Renamed"},
            headers=headers,
        )
        assert rename_resp.status_code == 200, rename_resp.text
        renamed = rename_resp.json()
        assert renamed["name"] == "Grade 11 Renamed"
        assert renamed["isced_level"] == 3
        assert renamed["age_band_min"] == 16
        assert renamed["age_band_max"] == 17
        assert renamed["ordinal"] == 11
        assert renamed["is_active"] is False, "renaming must not silently reactivate the level"

    async def test_update_level_on_nonexistent_level_returns_400_not_500(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: a missing resource on PUT must be a client-facing 4xx,
        not a raw 500 leaking the repository's ValueError text -- matching
        how PUT /classes/{id} already handles the same situation."""
        headers = {
            "Authorization": f"Bearer {await register_school_admin(test_client, 'admin_level_404@school.com')}"
        }
        resp = await test_client.put(
            "/api/v1/students/levels/999999", json={"name": "Ghost Grade"}, headers=headers
        )
        assert resp.status_code == 400, resp.text

    async def test_create_level_duplicate_name_updates_existing_row_not_discarded(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: POSTing an already-used level name must not silently
        discard the request's fields and hand back stale data under a 200 --
        it must apply them to the existing row."""
        headers = {
            "Authorization": f"Bearer {await register_school_admin(test_client, 'admin_level_dup@school.com')}"
        }
        first = await test_client.post(
            "/api/v1/students/levels",
            json={"name": "Dup Grade", "isced_level": 1, "ordinal": 5},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        first_id = first.json()["level_id"]

        second = await test_client.post(
            "/api/v1/students/levels",
            json={"name": "Dup Grade", "isced_level": 2, "ordinal": 9},
            headers=headers,
        )
        assert second.status_code == 200, second.text
        assert (
            second.json()["level_id"] == first_id
        ), "must not create a second row for the same name"
        assert second.json()["isced_level"] == 2
        assert second.json()["ordinal"] == 9

        listing = await test_client.get("/api/v1/students/levels", headers=headers)
        matching = [lvl for lvl in listing.json() if lvl["name"] == "Dup Grade"]
        assert len(matching) == 1
        assert matching[0]["isced_level"] == 2
        assert matching[0]["ordinal"] == 9

    async def test_create_level_duplicate_name_omitted_fields_keep_current_values(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: a duplicate-name POST that omits a field must not
        clobber that field with the bootstrap default (matches
        update_level's own "None means unchanged" convention)."""
        headers = {
            "Authorization": f"Bearer {await register_school_admin(test_client, 'admin_level_dup_partial@school.com')}"
        }
        first = await test_client.post(
            "/api/v1/students/levels",
            json={
                "name": "Partial Dup Grade",
                "isced_level": 3,
                "age_band_min": 10,
                "age_band_max": 11,
                "ordinal": 6,
            },
            headers=headers,
        )
        assert first.status_code == 200, first.text

        second = await test_client.post(
            "/api/v1/students/levels", json={"name": "Partial Dup Grade"}, headers=headers
        )
        assert second.status_code == 200, second.text
        assert (
            second.json()["isced_level"] == 3
        ), "omitted isced_level must keep its real value, not reset to the bootstrap default of 1"
        assert second.json()["age_band_min"] == 10
        assert second.json()["ordinal"] == 6

    async def test_deactivated_level_is_excluded_from_has_structure_and_refuses_new_sections(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: deactivating a level must have a real effect -- it must
        stop counting toward has_structure (which gates tenant activation),
        and it must stop accepting new class sections."""
        token = await register_school_admin(test_client, "admin_levels_deactivate@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 12", "ordinal": 12}, headers=headers
        )
        level_id = lvl_resp.json()["level_id"]

        cls_resp = await test_client.post(
            "/api/v1/students/classes", json={"name": "12A", "level_id": level_id}, headers=headers
        )
        assert cls_resp.status_code == 200, cls_resp.text

        structure_before = await test_client.get("/api/v1/students/structure", headers=headers)
        assert structure_before.json()["has_structure"] is True

        deactivate_resp = await test_client.put(
            f"/api/v1/students/levels/{level_id}", json={"is_active": False}, headers=headers
        )
        assert deactivate_resp.status_code == 200, deactivate_resp.text

        structure_after = await test_client.get("/api/v1/students/structure", headers=headers)
        assert (
            structure_after.json()["has_structure"] is False
        ), "a deactivated level's sections must not count toward has_structure"

        blocked_resp = await test_client.post(
            "/api/v1/students/classes", json={"name": "12B", "level_id": level_id}, headers=headers
        )
        assert blocked_resp.status_code == 400, blocked_resp.text

    async def test_update_class_head_teacher_null_clears_but_omitted_preserves(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: PUT /classes/{id} must distinguish "head_teacher_id not
        mentioned" (keep it) from "head_teacher_id explicitly null" (clear
        it) -- a departing teacher must be removable as head teacher without
        forcing the caller to name a replacement."""
        from app.core.config import TEACHER_INVITE_CODE

        admin_token = await register_school_admin(test_client, "admin_head_clear@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        t_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "teacher_head_clear@school.com",
                "password": "pass",
                "role": "teacher",
                "tenant_id": "tenant_a",
                "invite_code": TEACHER_INVITE_CODE,
            },
        )
        assert t_reg.status_code == 200, t_reg.text

        lvl_resp = await test_client.post(
            "/api/v1/students/levels",
            json={"name": "Grade 20", "ordinal": 20},
            headers=admin_headers,
        )
        level_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=admin_headers)
        teacher_id = teachers_list.json()[0]["id"]

        cls_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "20A", "level_id": level_id, "head_teacher_id": teacher_id},
            headers=admin_headers,
        )
        assert cls_resp.status_code == 200, cls_resp.text
        class_id = cls_resp.json()["id"]
        assert cls_resp.json()["head_teacher_id"] == teacher_id

        # Renaming without mentioning head_teacher_id must not touch it.
        rename_resp = await test_client.put(
            f"/api/v1/students/classes/{class_id}",
            json={"name": "20A Renamed"},
            headers=admin_headers,
        )
        assert rename_resp.status_code == 200, rename_resp.text
        assert rename_resp.json()["head_teacher_id"] == teacher_id

        # An explicit null must actually clear it.
        clear_resp = await test_client.put(
            f"/api/v1/students/classes/{class_id}",
            json={"head_teacher_id": None},
            headers=admin_headers,
        )
        assert clear_resp.status_code == 200, clear_resp.text
        assert (
            clear_resp.json()["head_teacher_id"] is None
        ), "an explicit null head_teacher_id must clear it, not be treated as omitted"

    async def test_class_capacity_must_be_positive(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: capacity must be a sane positive number -- there was no
        CHECK constraint, so a negative capacity could be stored."""
        headers = {
            "Authorization": f"Bearer {await register_school_admin(test_client, 'admin_capacity_check@school.com')}"
        }

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 21", "ordinal": 21}, headers=headers
        )
        level_id = lvl_resp.json()["level_id"]

        bad_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "21A", "level_id": level_id, "capacity": -30},
            headers=headers,
        )
        assert bad_resp.status_code == 422, bad_resp.text

    async def test_class_is_active_lifecycle(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: a class section needs a real lifecycle state -- closing
        one must not require hard-deleting it (which is blocked once it has
        enrollment history anyway) nor leave it permanently indistinguishable
        from an open section."""
        headers = {
            "Authorization": f"Bearer {await register_school_admin(test_client, 'admin_class_lifecycle@school.com')}"
        }

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 22", "ordinal": 22}, headers=headers
        )
        level_id = lvl_resp.json()["level_id"]

        cls_resp = await test_client.post(
            "/api/v1/students/classes", json={"name": "22A", "level_id": level_id}, headers=headers
        )
        assert cls_resp.status_code == 200, cls_resp.text
        assert cls_resp.json()["is_active"] is True
        class_id = cls_resp.json()["id"]

        close_resp = await test_client.put(
            f"/api/v1/students/classes/{class_id}", json={"is_active": False}, headers=headers
        )
        assert close_resp.status_code == 200, close_resp.text
        assert close_resp.json()["is_active"] is False

        # Still visible to admin management views (not hard-deleted), but now
        # distinguishable as closed rather than indistinguishable from open.
        listing = await test_client.get("/api/v1/students/classes", headers=headers)
        closed = next(c for c in listing.json() if c["id"] == class_id)
        assert closed["is_active"] is False

    async def test_bulk_enroll_rejects_missing_or_null_class_id(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: bulk-enroll has no "unassign a whole cohort" use case
        in this product -- a request that omits class_id (or sends it null)
        must be rejected outright, not silently clear class_id for every
        listed student while still reporting success."""
        headers = {
            "Authorization": f"Bearer {await register_school_admin(test_client, 'admin_bulk_missing@school.com')}"
        }

        missing_resp = await test_client.post(
            "/api/v1/students/bulk-enroll", json={"student_ids": [1]}, headers=headers
        )
        assert missing_resp.status_code == 422, missing_resp.text

        null_resp = await test_client.post(
            "/api/v1/students/bulk-enroll",
            json={"student_ids": [1], "class_id": None},
            headers=headers,
        )
        assert null_resp.status_code == 422, null_resp.text

    async def test_bulk_enroll_reports_real_count_and_flags_missing_student_ids(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: the response must reflect how many students were
        actually reassigned, and must name any requested ids that matched no
        student -- not report "Successfully enrolled N students" for a
        request where some (or all) of those N ids were bogus."""
        from app.core.config import TEACHER_INVITE_CODE

        admin_token = await register_school_admin(test_client, "admin_bulk_real@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels",
            json={"name": "Grade 23", "ordinal": 23},
            headers=admin_headers,
        )
        level_id = lvl_resp.json()["level_id"]
        cls_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "23A", "level_id": level_id},
            headers=admin_headers,
        )
        class_id = cls_resp.json()["id"]

        s_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "student_bulk_real@school.com",
                "password": "pass",
                "role": "student",
                "tenant_id": "tenant_a",
                "invite_code": TEACHER_INVITE_CODE,
            },
        )
        assert s_reg.status_code == 200, s_reg.text
        s_me = await test_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {s_reg.json()['access_token']}"}
        )
        student_id = int(s_me.json()["user_id"])

        repo = TenantRepository(db_pool)
        await repo.create_student(student_id, "Real Student", None)

        bogus_id = 999999
        resp = await test_client.post(
            "/api/v1/students/bulk-enroll",
            json={"student_ids": [student_id, bogus_id], "class_id": class_id},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["enrolled_count"] == 1, "must count only the student that actually exists"
        assert body["missing_student_ids"] == [bogus_id]

        roster = await test_client.get(
            f"/api/v1/students/classes/{class_id}/students", headers=admin_headers
        )
        assert any(s["id"] == student_id for s in roster.json())

    async def test_student_class_history_endpoint_returns_placement_transitions(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: a student's placement history must be retrievable
        through the API, not only inferable (badly) from the single mutable
        students.class_id column."""
        headers = {
            "Authorization": f"Bearer {await register_school_admin(test_client, 'admin_history_api@school.com')}"
        }

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 24", "ordinal": 24}, headers=headers
        )
        level_id = lvl_resp.json()["level_id"]
        cls_a = await test_client.post(
            "/api/v1/students/classes", json={"name": "24A", "level_id": level_id}, headers=headers
        )
        class_a_id = cls_a.json()["id"]
        cls_b = await test_client.post(
            "/api/v1/students/classes", json={"name": "24B", "level_id": level_id}, headers=headers
        )
        class_b_id = cls_b.json()["id"]

        from app.domains.tenant.user_repository import UserRepository

        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)
        s_uid = await user_repo.create_user("history_api_student@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "History Api Student", class_a_id)

        reassign_resp = await test_client.put(
            f"/api/v1/students/{student_id}/class", json={"class_id": class_b_id}, headers=headers
        )
        assert reassign_resp.status_code == 200, reassign_resp.text

        history_resp = await test_client.get(
            f"/api/v1/students/{student_id}/class-history", headers=headers
        )
        assert history_resp.status_code == 200, history_resp.text
        entries = history_resp.json()
        assert len(entries) == 2
        assert entries[0]["old_class_id"] is None
        assert entries[0]["new_class_id"] == class_a_id
        assert entries[1]["old_class_id"] == class_a_id
        assert entries[1]["new_class_id"] == class_b_id

    async def test_staff_can_manage_levels_and_classes(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # A teacher account still exists, purely to supply a real teacher id for
        # head_teacher_id below -- it is no longer the ACTOR for any of the
        # level/class management calls (Academic Administration Hub territory,
        # school_admin only).
        t_payload = {
            "email": "teacher@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        admin_token = await register_school_admin(test_client, "admin_manage_lc@school.com")
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Create level
        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 5"}, headers=headers
        )
        assert lvl_resp.status_code == 200
        lvl_id = lvl_resp.json()["level_id"]

        # List levels
        lvls_list = await test_client.get("/api/v1/students/levels", headers=headers)
        assert lvls_list.status_code == 200
        assert len(lvls_list.json()) == 1

        # Fetch teachers list
        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        assert teachers_list.status_code == 200
        t_id = teachers_list.json()[0]["id"]

        # Create Class
        cls_payload = {"name": "Class A", "level_id": lvl_id, "head_teacher_id": t_id}
        cls_resp = await test_client.post(
            "/api/v1/students/classes", json=cls_payload, headers=headers
        )
        assert cls_resp.status_code == 200
        assert cls_resp.json()["name"] == "Class A"

    async def test_duplicate_level_and_class_prevention(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # Same as above -- teacher exists only to supply a head_teacher_id;
        # level/class creation is school_admin-only.
        t_payload = {
            "email": "teacher_dup@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        admin_token = await register_school_admin(test_client, "admin_dup@school.com")
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Create a level
        lvl_resp1 = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 6"}, headers=headers
        )
        assert lvl_resp1.status_code == 200
        lvl_id1 = lvl_resp1.json()["level_id"]

        # 2. Try to create the same level (case-insensitive and trimmed)
        lvl_resp2 = await test_client.post(
            "/api/v1/students/levels", json={"name": "  grade 6  "}, headers=headers
        )
        assert lvl_resp2.status_code == 200
        lvl_id2 = lvl_resp2.json()["level_id"]

        # They should return the exact same level_id
        assert lvl_id1 == lvl_id2

        # 3. Create a class
        teachers_list = await test_client.get("/api/v1/students/teachers", headers=headers)
        assert teachers_list.status_code == 200
        t_id = teachers_list.json()[0]["id"]

        cls_payload1 = {"name": "Class B", "level_id": lvl_id1, "head_teacher_id": t_id}
        cls_resp1 = await test_client.post(
            "/api/v1/students/classes", json=cls_payload1, headers=headers
        )
        assert cls_resp1.status_code == 200
        cls_id1 = cls_resp1.json()["id"]

        # 4. Try to create the same class under same level (case-insensitive and trimmed)
        cls_payload2 = {"name": "  class b  ", "level_id": lvl_id1, "head_teacher_id": t_id}
        cls_resp2 = await test_client.post(
            "/api/v1/students/classes", json=cls_payload2, headers=headers
        )
        assert cls_resp2.status_code == 200
        cls_id2 = cls_resp2.json()["id"]

        # They should return the exact same class ID
        assert cls_id1 == cls_id2

    async def test_update_class_head_teacher(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # Setup teacher 1 & teacher 2
        t1_payload = {
            "email": "head1@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t1_reg = await test_client.post("/api/v1/auth/register", json=t1_payload)
        t1_headers = {"Authorization": f"Bearer {t1_reg.json()['access_token']}"}

        t2_payload = {
            "email": "head2@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        await test_client.post("/api/v1/auth/register", json=t2_payload)

        admin_token = await register_school_admin(test_client, "admin_head_teacher@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Create level & class as school_admin -- a bare "teacher" can no
        # longer do either (Academic Administration Hub territory).
        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 10"}, headers=admin_headers
        )
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=admin_headers)
        t_objs = teachers_list.json()
        t1_id = next(t["id"] for t in t_objs if t["email"] == "head1@school.com")
        t2_id = next(t["id"] for t in t_objs if t["email"] == "head2@school.com")

        cls_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "Grade 10A", "level_id": lvl_id, "head_teacher_id": t1_id},
            headers=admin_headers,
        )
        assert cls_resp.status_code == 200
        cls_id = cls_resp.json()["id"]
        assert cls_resp.json()["head_teacher_id"] == t1_id

        # A teacher -- even the class's own head teacher -- may not update it.
        # This is the actual invariant under test now: update_class is
        # school_admin-only, with no "you may edit your own class" carve-out.
        denied_resp = await test_client.put(
            f"/api/v1/students/classes/{cls_id}",
            json={"head_teacher_id": t2_id, "name": "Grade 10-A Updated"},
            headers=t1_headers,
        )
        assert denied_resp.status_code == 403

        # school_admin can still update the class's head teacher.
        update_resp = await test_client.put(
            f"/api/v1/students/classes/{cls_id}",
            json={"head_teacher_id": t2_id, "name": "Grade 10-A Updated"},
            headers=admin_headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["head_teacher_id"] == t2_id
        assert update_resp.json()["name"] == "Grade 10-A Updated"

    async def test_student_enrollments_and_approvals(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # 1. Setup teacher & class
        t_payload = {
            "email": "teacher@class.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Level/class creation is school_admin-only (Academic Administration
        # Hub territory) -- this admin exists solely for that setup step.
        admin_token = await register_school_admin(test_client, "admin_enroll_approve@class.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 6"}, headers=admin_headers
        )
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_payload = {"name": "Science B", "level_id": lvl_id, "head_teacher_id": t_id}
        cls_resp = await test_client.post(
            "/api/v1/students/classes", json=cls_payload, headers=admin_headers
        )
        class_id = cls_resp.json()["id"]

        # 2. Setup Student
        s_payload = {
            "email": "student@class.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        s_reg = await test_client.post("/api/v1/auth/register", json=s_payload)
        s_headers = {"Authorization": f"Bearer {s_reg.json()['access_token']}"}
        s_me = await test_client.get("/api/v1/auth/me", headers=s_headers)
        student_id = int(s_me.json()["user_id"])

        # Move student to the class
        repo = TenantRepository(db_pool)
        await repo.create_student(student_id, "Bob Cooper", class_id)

        event_payload = {
            "title": "Stargazing Trip",
            "description": "Astronomy night",
            "address": "Astrodome",
            "school_subsidy": 4.0,
            "date": datetime.now(UTC).isoformat(),
            "class_mappings": [
                {
                    "class_id": class_id,
                    "ticket_price": 6.0,
                }
            ],
        }
        event_resp = await test_client.post("/api/v1/events", json=event_payload, headers=t_headers)
        if event_resp.status_code != 200:
            print("\nEVENT RESP ERROR DETAILS:", event_resp.text)
        assert event_resp.status_code == 200
        ecm_id = event_resp.json()["class_mappings"][0]["id"]
        event_id = event_resp.json()["id"]

        # Publish event directly in test DB -- enrollment is only allowed
        # against a published event (see TenantService.enroll_student).
        await db_pool.execute("UPDATE event SET status = 'published' WHERE id = $1", event_id)

        # Register Parent & Link Student
        p_payload = {
            "email": "parent_wf_new@class.com",
            "password": "pass",
            "role": "parent",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        p_reg = await test_client.post("/api/v1/auth/register", json=p_payload)
        p_headers = {"Authorization": f"Bearer {p_reg.json()['access_token']}"}
        p_me = await test_client.get("/api/v1/auth/me", headers=p_headers)
        parent_id = int(p_me.json()["user_id"])

        link_resp = await test_client.post(
            "/api/v1/students/link-parent",
            json={"student_id": student_id, "parent_id": parent_id},
            headers=t_headers,
        )
        assert link_resp.status_code == 200

        # 4. Student requests enrollment
        enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": ecm_id},
            headers=s_headers,
        )
        assert enroll_resp.status_code == 200
        assert enroll_resp.json()["state"] == "requested_by_student"
        enrollment_id = enroll_resp.json()["id"]

        # 5. Teacher attempts to approve directly (should fail with 400 because parent hasn't approved yet)
        failed_app_resp = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_teacher"},
            headers=t_headers,
        )
        assert failed_app_resp.status_code == 400
        assert "must be approved by a parent" in failed_app_resp.json()["detail"]

        # 6. Parent approves enrollment (state becomes approved_by_parent)
        parent_app_resp = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_parent"},
            headers=p_headers,
        )
        assert parent_app_resp.status_code == 200
        assert parent_app_resp.json()["state"] == "approved_by_parent"

        # 7. Teacher approves enrollment (state becomes approved_by_teacher)
        app_resp = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_teacher"},
            headers=t_headers,
        )
        assert app_resp.status_code == 200
        assert app_resp.json()["state"] == "approved_by_teacher"

    async def _setup_event_with_custodial_and_noncustodial_parent(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, tag: str
    ):
        """Shared fixture for the guardian-authority tests below: a class, an
        event, a student, and two linked parents -- one with can_approve
        (the default, "custodial" for these tests' purposes) and one
        explicitly linked with can_approve=False ("non-custodial")."""
        from app.core.config import TEACHER_INVITE_CODE

        t_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": f"teacher_{tag}@class.com",
                "password": "pass",
                "role": "teacher",
                "tenant_id": "tenant_a",
                "invite_code": TEACHER_INVITE_CODE,
            },
        )
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Level/class creation is school_admin-only (Academic Administration Hub
        # territory) -- this admin exists solely for that setup step; every
        # actual test behavior below still runs as the teacher/parents/student.
        admin_token = await register_school_admin(test_client, f"admin_{tag}@class.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": f"Grade {tag}"}, headers=admin_headers
        )
        lvl_id = lvl_resp.json()["level_id"]
        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]
        cls_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": f"Section {tag}", "level_id": lvl_id, "head_teacher_id": t_id},
            headers=admin_headers,
        )
        class_id = cls_resp.json()["id"]

        s_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": f"student_{tag}@class.com",
                "password": "pass",
                "role": "student",
                "tenant_id": "tenant_a",
                "invite_code": "regester123",
            },
        )
        s_headers = {"Authorization": f"Bearer {s_reg.json()['access_token']}"}
        s_me = await test_client.get("/api/v1/auth/me", headers=s_headers)
        student_id = int(s_me.json()["user_id"])
        repo = TenantRepository(db_pool)
        await repo.create_student(student_id, f"Student {tag}", class_id)

        event_resp = await test_client.post(
            "/api/v1/events",
            json={
                "title": f"Trip {tag}",
                "description": "",
                "address": "",
                "school_subsidy": 0.0,
                "date": datetime.now(UTC).isoformat(),
                "class_mappings": [{"class_id": class_id, "ticket_price": 5.0}],
            },
            headers=t_headers,
        )
        assert event_resp.status_code == 200, event_resp.text
        ecm_id = event_resp.json()["class_mappings"][0]["id"]
        event_id = event_resp.json()["id"]

        # Publish event directly in test DB -- enrollment is only allowed
        # against a published event (see TenantService.enroll_student).
        await db_pool.execute("UPDATE event SET status = 'published' WHERE id = $1", event_id)

        custodial_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": f"custodial_{tag}@class.com",
                "password": "pass",
                "role": "parent",
                "tenant_id": "tenant_a",
                "invite_code": "regester123",
            },
        )
        custodial_headers = {"Authorization": f"Bearer {custodial_reg.json()['access_token']}"}
        custodial_id = int(
            (await test_client.get("/api/v1/auth/me", headers=custodial_headers)).json()["user_id"]
        )
        link1 = await test_client.post(
            "/api/v1/students/link-parent",
            json={"student_id": student_id, "parent_id": custodial_id, "can_approve": True},
            headers=t_headers,
        )
        assert link1.status_code == 200, link1.text

        noncustodial_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": f"noncustodial_{tag}@class.com",
                "password": "pass",
                "role": "parent",
                "tenant_id": "tenant_a",
                "invite_code": "regester123",
            },
        )
        noncustodial_headers = {
            "Authorization": f"Bearer {noncustodial_reg.json()['access_token']}"
        }
        noncustodial_id = int(
            (await test_client.get("/api/v1/auth/me", headers=noncustodial_headers)).json()[
                "user_id"
            ]
        )
        link2 = await test_client.post(
            "/api/v1/students/link-parent",
            json={
                "student_id": student_id,
                "parent_id": noncustodial_id,
                "relationship_type": "non-custodial parent",
                "can_approve": False,
            },
            headers=t_headers,
        )
        assert link2.status_code == 200, link2.text

        return {
            "t_headers": t_headers,
            "s_headers": s_headers,
            "student_id": student_id,
            "ecm_id": ecm_id,
            "custodial_headers": custodial_headers,
            "noncustodial_headers": noncustodial_headers,
        }

    async def test_non_custodial_parent_cannot_approve_or_reject_enrollment(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: a linked parent explicitly marked can_approve=False
        must not be able to approve or reject a paid trip -- being linked at
        all used to be the only thing that mattered."""
        ctx = await self._setup_event_with_custodial_and_noncustodial_parent(
            test_client, db_pool, "nc1"
        )

        enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": ctx["student_id"], "event_class_map_id": ctx["ecm_id"]},
            headers=ctx["s_headers"],
        )
        assert enroll_resp.status_code == 200, enroll_resp.text
        assert enroll_resp.json()["state"] == "requested_by_student"
        enrollment_id = enroll_resp.json()["id"]

        blocked = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_parent"},
            headers=ctx["noncustodial_headers"],
        )
        assert blocked.status_code == 403, blocked.text
        assert "not authorized to approve" in blocked.json()["detail"]

        allowed = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_parent"},
            headers=ctx["custodial_headers"],
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["state"] == "approved_by_parent"

    async def test_non_custodial_parent_direct_enroll_does_not_auto_approve(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: a non-custodial parent enrolling their child directly
        must land in requested_by_student (awaiting an authorized parent),
        not skip straight to approved_by_parent the way any linked parent
        used to."""
        ctx = await self._setup_event_with_custodial_and_noncustodial_parent(
            test_client, db_pool, "nc2"
        )

        resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": ctx["student_id"], "event_class_map_id": ctx["ecm_id"]},
            headers=ctx["noncustodial_headers"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "requested_by_student"

    async def test_non_custodial_parent_cannot_cancel_enrollment(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: cancelling is the same authority as approving -- a
        parent who can't consent to a trip must not be able to pull it
        after a custodial parent already approved it."""
        ctx = await self._setup_event_with_custodial_and_noncustodial_parent(
            test_client, db_pool, "nc3"
        )

        enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": ctx["student_id"], "event_class_map_id": ctx["ecm_id"]},
            headers=ctx["custodial_headers"],
        )
        assert enroll_resp.status_code == 200, enroll_resp.text
        assert enroll_resp.json()["state"] == "approved_by_parent"
        enrollment_id = enroll_resp.json()["id"]

        blocked = await test_client.delete(
            f"/api/v1/students/enrollments/{enrollment_id}", headers=ctx["noncustodial_headers"]
        )
        assert blocked.status_code == 403, blocked.text

    async def test_parent_direct_enrollment_and_teacher_approval(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # 1. Setup teacher & class
        t_payload = {
            "email": "teacher2@class.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Level/class creation is school_admin-only (Academic Administration
        # Hub territory) -- this admin exists solely for that setup step.
        admin_token = await register_school_admin(test_client, "admin_parent_direct@class.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 7"}, headers=admin_headers
        )
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_payload = {"name": "Science C", "level_id": lvl_id, "head_teacher_id": t_id}
        cls_resp = await test_client.post(
            "/api/v1/students/classes", json=cls_payload, headers=admin_headers
        )
        class_id = cls_resp.json()["id"]

        # 2. Setup Student
        s_payload = {
            "email": "student2@class.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        s_reg = await test_client.post("/api/v1/auth/register", json=s_payload)
        s_headers = {"Authorization": f"Bearer {s_reg.json()['access_token']}"}
        s_me = await test_client.get("/api/v1/auth/me", headers=s_headers)
        student_id = int(s_me.json()["user_id"])

        # Move student to the class
        repo = TenantRepository(db_pool)
        await repo.create_student(student_id, "Emma Johnson", class_id)

        # 3. Setup Parent
        p_payload = {
            "email": "parent2@class.com",
            "password": "pass",
            "role": "parent",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        p_reg = await test_client.post("/api/v1/auth/register", json=p_payload)
        p_headers = {"Authorization": f"Bearer {p_reg.json()['access_token']}"}
        p_me = await test_client.get("/api/v1/auth/me", headers=p_headers)
        parent_id = int(p_me.json()["user_id"])

        # Link parent and student (via teacher/staff)
        link_resp = await test_client.post(
            "/api/v1/students/link-parent",
            json={"student_id": student_id, "parent_id": parent_id},
            headers=t_headers,
        )
        assert link_resp.status_code == 200

        event_payload = {
            "title": "Astronomy Night",
            "description": "Star hunting",
            "address": "Astrodome",
            "school_subsidy": 5.0,
            "date": datetime.now(UTC).isoformat(),
            "class_mappings": [
                {
                    "class_id": class_id,
                    "ticket_price": 7.0,
                }
            ],
        }
        event_resp = await test_client.post("/api/v1/events", json=event_payload, headers=t_headers)
        assert event_resp.status_code == 200
        ecm_id = event_resp.json()["class_mappings"][0]["id"]

        # Publish event directly in test DB -- enrollment is only allowed
        # against a published event (see TenantService.enroll_student).
        await db_pool.execute(
            "UPDATE event SET status = 'published' WHERE id = $1", event_resp.json()["id"]
        )

        # 5. Parent attempts to enroll non-linked student (should fail with 403)
        bad_enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": 9999, "event_class_map_id": ecm_id},
            headers=p_headers,
        )
        assert bad_enroll_resp.status_code == 403

        # 6. Parent directly enrolls their child (state becomes approved_by_parent)
        enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": ecm_id},
            headers=p_headers,
        )
        assert enroll_resp.status_code == 200
        assert enroll_resp.json()["state"] == "approved_by_parent"
        enrollment_id = enroll_resp.json()["id"]

        # 7. Parent tries to approve another student's enrollment (not linked to them) (should fail with 403)
        bad_approve_resp = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_parent"},
            headers=s_headers,  # student doesn't have parent relationship
        )
        assert bad_approve_resp.status_code == 403

        # 8. Teacher approves enrollment (state becomes approved_by_teacher)
        app_resp = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_teacher"},
            headers=t_headers,
        )
        assert app_resp.status_code == 200
        assert app_resp.json()["state"] == "approved_by_teacher"

    async def test_student_class_match_and_one_time_enrollment(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # 1. Setup teacher, level, and Class A
        t_payload = {
            "email": "teacher3@class.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Level/class creation is school_admin-only (Academic Administration
        # Hub territory) -- this admin exists solely for that setup step.
        admin_token = await register_school_admin(test_client, "admin_class_match@class.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 8"}, headers=admin_headers
        )
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_resp1 = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "Science D", "level_id": lvl_id, "head_teacher_id": t_id},
            headers=admin_headers,
        )
        class_id_1 = cls_resp1.json()["id"]

        cls_resp2 = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "Science E", "level_id": lvl_id, "head_teacher_id": t_id},
            headers=admin_headers,
        )
        class_id_2 = cls_resp2.json()["id"]

        # 2. Setup Student (assigned to Class A/class_id_1)
        s_payload = {
            "email": "student3@class.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        s_reg = await test_client.post("/api/v1/auth/register", json=s_payload)
        s_headers = {"Authorization": f"Bearer {s_reg.json()['access_token']}"}
        s_me = await test_client.get("/api/v1/auth/me", headers=s_headers)
        student_id = int(s_me.json()["user_id"])

        repo = TenantRepository(db_pool)
        await repo.create_student(student_id, "Emma Cooper", class_id_1)

        event_payload = {
            "title": "Stargazing Event",
            "description": "Stargazing night",
            "address": "Astrodome",
            "school_subsidy": 4.0,
            "date": datetime.now(UTC).isoformat(),
            "class_mappings": [
                {
                    "class_id": class_id_1,
                    "ticket_price": 6.0,
                },
                {
                    "class_id": class_id_2,
                    "ticket_price": 6.0,
                },
            ],
        }
        event_resp = await test_client.post("/api/v1/events", json=event_payload, headers=t_headers)
        assert event_resp.status_code == 200
        mappings = event_resp.json()["class_mappings"]

        # Identify mapping IDs
        map_id_1 = next(m["id"] for m in mappings if m["class_id"] == class_id_1)
        map_id_2 = next(m["id"] for m in mappings if m["class_id"] == class_id_2)

        # Publish event directly in test DB -- enrollment is only allowed
        # against a published event (see TenantService.enroll_student).
        await db_pool.execute(
            "UPDATE event SET status = 'published' WHERE id = $1", event_resp.json()["id"]
        )

        # Verify profile includes class details
        prof_resp = await test_client.get("/api/v1/auth/profile", headers=s_headers)
        assert prof_resp.status_code == 200
        assert prof_resp.json()["class_id"] == class_id_1

        # 4. Student attempts to enroll in Class B mapping (should fail with 400)
        bad_enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": map_id_2},
            headers=s_headers,
        )
        assert bad_enroll_resp.status_code == 400
        assert "not in the class mapped to this event" in bad_enroll_resp.json()["detail"]

        # 5. Student enrolls in their own Class A mapping (should succeed)
        good_enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": map_id_1},
            headers=s_headers,
        )
        assert good_enroll_resp.status_code == 200

        # 6. Student attempts to enroll again in Class A mapping (should return the same ID)
        dup_enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": map_id_1},
            headers=s_headers,
        )
        assert dup_enroll_resp.status_code == 200
        assert dup_enroll_resp.json()["id"] == good_enroll_resp.json()["id"]

        # 7. Student attempts to enroll in Class B mapping after already enrolling in Class A mapping (should fail with 400)
        dup_event_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": map_id_2},
            headers=s_headers,
        )
        assert dup_event_resp.status_code == 400
        assert "already enrolled in this event" in dup_event_resp.json()["detail"]

    async def test_linked_profile_details(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # 1. Setup teacher and class
        t_payload = {
            "email": "teacher4@class.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Level/class creation is school_admin-only (Academic Administration
        # Hub territory) -- this admin exists solely for that setup step.
        admin_token = await register_school_admin(test_client, "admin_linked_profile@class.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 9"}, headers=admin_headers
        )
        lvl_id = lvl_resp.json()["level_id"]

        t_me = await test_client.get("/api/v1/auth/me", headers=t_headers)
        t_id = int(t_me.json()["user_id"])

        cls_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "Science G", "level_id": lvl_id, "head_teacher_id": t_id},
            headers=admin_headers,
        )
        class_id = cls_resp.json()["id"]

        # 2. Register parent
        p_payload = {
            "email": "parent4@class.com",
            "password": "pass",
            "role": "parent",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        p_reg = await test_client.post("/api/v1/auth/register", json=p_payload)
        p_headers = {"Authorization": f"Bearer {p_reg.json()['access_token']}"}
        p_me = await test_client.get("/api/v1/auth/me", headers=p_headers)
        parent_id = int(p_me.json()["user_id"])

        # 3. Register student
        s_payload = {
            "email": "student4@class.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        s_reg = await test_client.post("/api/v1/auth/register", json=s_payload)
        s_headers = {"Authorization": f"Bearer {s_reg.json()['access_token']}"}
        s_me = await test_client.get("/api/v1/auth/me", headers=s_headers)
        student_id = int(s_me.json()["user_id"])

        repo = TenantRepository(db_pool)
        await repo.create_student(student_id, "Jane Doe", class_id)
        await repo.create_parent(parent_id, "John Doe", "1234567")

        # Link parent and student
        link_resp = await test_client.post(
            "/api/v1/students/link-parent",
            json={"student_id": student_id, "parent_id": parent_id},
            headers=t_headers,
        )
        assert link_resp.status_code == 200

        # Verify student profile shows parent details
        s_prof_resp = await test_client.get("/api/v1/auth/profile", headers=s_headers)
        assert s_prof_resp.status_code == 200
        s_prof = s_prof_resp.json()
        assert s_prof["parent_name"] == "John Doe"
        assert s_prof["parent_email"] == "parent4@class.com"

        # Verify parent profile shows student details
        p_prof_resp = await test_client.get("/api/v1/auth/profile", headers=p_headers)
        assert p_prof_resp.status_code == 200
        p_prof = p_prof_resp.json()
        assert len(p_prof["students"]) == 1
        assert p_prof["students"][0]["name"] == "Jane Doe"
        assert p_prof["students"][0]["email"] == "student4@class.com"

        # Verify teacher profile shows the head class name
        t_prof_resp = await test_client.get("/api/v1/auth/profile", headers=t_headers)
        assert t_prof_resp.status_code == 200
        t_prof = t_prof_resp.json()
        assert t_prof["class_name"] == "Science G (Grade 9)"

    async def test_parent_two_children_different_classes_only_enrolls_eligible_child(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # 1. Setup staff & 2 classes: Class A and Class B
        t_payload = {
            "email": "teacher_two_kids@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Level/class creation is school_admin-only (Academic Administration
        # Hub territory) -- this admin exists solely for that setup step.
        admin_token = await register_school_admin(test_client, "admin_two_kids@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 5"}, headers=admin_headers
        )
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls1_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "Class 5A", "level_id": lvl_id, "head_teacher_id": t_id},
            headers=admin_headers,
        )
        class_a_id = cls1_resp.json()["id"]

        cls2_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "Class 5B", "level_id": lvl_id, "head_teacher_id": t_id},
            headers=admin_headers,
        )
        class_b_id = cls2_resp.json()["id"]

        # 2. Setup 2 Students (Child 1 in Class A, Child 2 in Class B)
        s1_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "child1@school.com",
                "password": "pass",
                "role": "student",
                "tenant_id": "tenant_a",
                "invite_code": "regester123",
            },
        )
        s1_headers = {"Authorization": f"Bearer {s1_reg.json()['access_token']}"}
        s1_me = await test_client.get("/api/v1/auth/me", headers=s1_headers)
        child1_id = int(s1_me.json()["user_id"])

        s2_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "child2@school.com",
                "password": "pass",
                "role": "student",
                "tenant_id": "tenant_a",
                "invite_code": "regester123",
            },
        )
        s2_headers = {"Authorization": f"Bearer {s2_reg.json()['access_token']}"}
        s2_me = await test_client.get("/api/v1/auth/me", headers=s2_headers)
        child2_id = int(s2_me.json()["user_id"])

        repo = TenantRepository(db_pool)
        await repo.create_student(child1_id, "Ahmad (Class A)", class_a_id)
        await repo.create_student(child2_id, "Sami (Class B)", class_b_id)

        # 3. Setup Parent linked to BOTH Child 1 and Child 2
        p_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "parent_two_kids@school.com",
                "password": "pass",
                "role": "parent",
                "tenant_id": "tenant_a",
                "invite_code": "regester123",
            },
        )
        p_headers = {"Authorization": f"Bearer {p_reg.json()['access_token']}"}
        p_me = await test_client.get("/api/v1/auth/me", headers=p_headers)
        parent_id = int(p_me.json()["user_id"])
        await repo.create_parent(parent_id, "Parent User", "1234567")

        await repo.add_student_parent_link(child1_id, parent_id)
        await repo.add_student_parent_link(child2_id, parent_id)

        # 4. Create and publish Event targeted ONLY at Class A
        event_payload = {
            "title": "Class 5A Science Trip",
            "description": "Exclusive to Class 5A",
            "address": "Science Museum",
            "school_subsidy": 0.0,
            "date": datetime.now(UTC).isoformat(),
            "class_mappings": [{"class_id": class_a_id, "ticket_price": 15.0}],
        }
        ev_resp = await test_client.post("/api/v1/events", json=event_payload, headers=t_headers)
        assert ev_resp.status_code == 200
        event_id = ev_resp.json()["id"]
        class_a_map_id = ev_resp.json()["class_mappings"][0]["id"]

        # Publish event directly in test DB
        await db_pool.execute("UPDATE event SET status = 'published' WHERE id = $1", event_id)

        # 5. Parent queries published events
        pub_events_resp = await test_client.get("/api/v1/events/published", headers=p_headers)
        assert pub_events_resp.status_code == 200
        pub_events = pub_events_resp.json()

        matched_ev = next((e for e in pub_events if e["id"] == event_id), None)
        assert matched_ev is not None
        assert len(matched_ev["class_mappings"]) == 1
        assert matched_ev["class_mappings"][0]["class_id"] == class_a_id

        # 6. Parent enrolls Child 1 (Class A) -> SUCCEEDS (200 OK, approved_by_parent)
        enroll_c1 = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": child1_id, "event_class_map_id": class_a_map_id},
            headers=p_headers,
        )
        assert enroll_c1.status_code == 200
        assert enroll_c1.json()["state"] == "approved_by_parent"

        # 7. Parent attempts to enroll Child 2 (Class B) into Class A event -> FAILS with 400
        enroll_c2 = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": child2_id, "event_class_map_id": class_a_map_id},
            headers=p_headers,
        )
        assert enroll_c2.status_code == 400
        assert "not in the class" in enroll_c2.json()["detail"].lower()

    async def test_reassign_and_bulk_enroll_use_undoubled_paths(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """Regression test: these two routes used to be registered at
        /api/v1/students/students/... (doubled segment, since router_gated
        already carries the /api/v1/students prefix) and were unreachable at
        their documented, non-doubled paths."""
        from app.core.config import TEACHER_INVITE_CODE

        t_payload = {
            "email": "teacher_paths@class.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # This test is purely about routing (doubled vs. non-doubled path), not
        # about who may call these endpoints -- so it needs a role that's
        # actually allowed to. Level/class creation and student reassignment
        # are both school_admin-only (Academic Administration Hub /
        # Student Placement territory); a bare "teacher" no longer qualifies
        # for any of them.
        admin_token = await register_school_admin(test_client, "admin_paths@class.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 4"}, headers=admin_headers
        )
        lvl_id = lvl_resp.json()["level_id"]
        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_a = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "Path A", "level_id": lvl_id, "head_teacher_id": t_id},
            headers=admin_headers,
        )
        class_a_id = cls_a.json()["id"]
        cls_b = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "Path B", "level_id": lvl_id, "head_teacher_id": t_id},
            headers=admin_headers,
        )
        class_b_id = cls_b.json()["id"]

        s_payload = {
            "email": "student_paths@class.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        s_reg = await test_client.post("/api/v1/auth/register", json=s_payload)
        s_me = await test_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {s_reg.json()['access_token']}"}
        )
        student_id = int(s_me.json()["user_id"])

        repo = TenantRepository(db_pool)
        await repo.create_student(student_id, "Path Test Student", class_a_id)

        # The correct, non-doubled path must work.
        reassign_resp = await test_client.put(
            f"/api/v1/students/{student_id}/class",
            json={"class_id": class_b_id},
            headers=admin_headers,
        )
        assert reassign_resp.status_code == 200

        bulk_resp = await test_client.post(
            "/api/v1/students/bulk-enroll",
            json={"student_ids": [student_id], "class_id": class_a_id},
            headers=admin_headers,
        )
        assert bulk_resp.status_code == 200
        assert bulk_resp.json()["enrolled_count"] == 1

        # The old, doubled path must no longer resolve to anything.
        stale_reassign_resp = await test_client.put(
            f"/api/v1/students/students/{student_id}/class",
            json={"class_id": class_a_id},
            headers=admin_headers,
        )
        assert stale_reassign_resp.status_code == 404

        stale_bulk_resp = await test_client.post(
            "/api/v1/students/students/bulk-enroll",
            json={"student_ids": [student_id], "class_id": class_a_id},
            headers=admin_headers,
        )
        assert stale_bulk_resp.status_code == 404


# =============================================================================
# Notifications Tests
# =============================================================================
class TestNotificationsRouter:
    async def test_notification_delivery(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # 1. Setup teacher and class
        t_payload = {
            "email": "teacher@notif.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Level/class creation is school_admin-only (Academic Administration
        # Hub territory) -- this admin exists solely for that setup step.
        admin_token = await register_school_admin(test_client, "admin_notif@notif.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 7"}, headers=admin_headers
        )
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_payload = {"name": "Science C", "level_id": lvl_id, "head_teacher_id": t_id}
        cls_resp = await test_client.post(
            "/api/v1/students/classes", json=cls_payload, headers=admin_headers
        )
        class_id = cls_resp.json()["id"]

        # 2. Setup Student
        s_payload = {
            "email": "student@notif.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        s_reg = await test_client.post("/api/v1/auth/register", json=s_payload)
        s_headers = {"Authorization": f"Bearer {s_reg.json()['access_token']}"}
        s_me = await test_client.get("/api/v1/auth/me", headers=s_headers)
        student_id = int(s_me.json()["user_id"])

        repo = TenantRepository(db_pool)
        await repo.create_student(student_id, "Jack Sparrow", class_id)

        # 3. Create Event mapped to Class C (notifies class C students)
        event_payload = {
            "title": "Pirate Day",
            "description": "Ahoy mates",
            "address": "Ocean Harbor",
            "school_subsidy": 0.0,
            "date": datetime.now(UTC).isoformat(),
            "class_mappings": [{"class_id": class_id, "ticket_price": 0.0, "costbudget_id": None}],
        }
        await test_client.post("/api/v1/events", json=event_payload, headers=t_headers)

        # 4. Check notification delivery
        notif_resp = await test_client.get("/api/v1/notifications", headers=s_headers)
        assert notif_resp.status_code == 200
        assert len(notif_resp.json()["notifications"]) == 1
        notif_id = notif_resp.json()["notifications"][0]["id"]

        # Mark read
        read_resp = await test_client.post(
            f"/api/v1/notifications/{notif_id}/read", headers=s_headers
        )
        assert read_resp.status_code == 200


# =============================================================================
# PII Student Health & Records
# =============================================================================
class TestStudentHealthRouter:
    async def test_health_records_pii(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # Setup teacher
        t_payload = {
            "email": "teacher@health.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Setup student
        s_payload = {
            "email": "student@health.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        s_reg = await test_client.post("/api/v1/auth/register", json=s_payload)
        s_me = await test_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {s_reg.json()['access_token']}"}
        )
        student_id = int(s_me.json()["user_id"])

        # Insert health record
        h_payload = {
            "national_id": "NAT-12345",
            "medical_conditions": "Allergy to peanuts",
            "emergency_contact": "+1-202-555-0143",
        }
        h_resp = await test_client.post(
            f"/api/v1/students/{student_id}/health", json=h_payload, headers=t_headers
        )
        assert h_resp.status_code == 200

        # Retrieve masked
        get_resp = await test_client.get(f"/api/v1/students/{student_id}/health", headers=t_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["is_masked"] is True
        assert get_resp.json()["national_id"] != "NAT-12345"


class TestEventUpdateRouter:
    async def test_update_event_and_class_mappings(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        # Setup teacher
        t_payload = {
            "email": "teacher@updateevent.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Setup school_admin (via a real invitation) -- moved ahead of level/
        # class creation, since a bare "teacher" can no longer create either
        # (Academic Administration Hub territory).
        a_token = await register_school_admin(test_client, "admin@updateevent.com")
        a_headers = {"Authorization": f"Bearer {a_token}"}

        # Setup Class
        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 8"}, headers=a_headers
        )
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_payload = {"name": "Science D", "level_id": lvl_id, "head_teacher_id": t_id}
        cls_resp = await test_client.post(
            "/api/v1/students/classes", json=cls_payload, headers=a_headers
        )
        class_id = cls_resp.json()["id"]

        # Create Event
        event_payload = {
            "title": "Old Expedition",
            "description": "Original description",
            "address": "Cave",
            "school_subsidy": 10.0,
            "date": datetime.now(UTC).isoformat(),
            "class_mappings": [
                {
                    "class_id": class_id,
                    "ticket_price": 5.0,
                }
            ],
        }
        create_resp = await test_client.post(
            "/api/v1/events", json=event_payload, headers=a_headers
        )
        assert create_resp.status_code == 200
        event_id = create_resp.json()["id"]

        # Update Event (PUT)
        update_payload = {
            "title": "New Expedition",
            "description": "Updated description",
            "address": "Mountain",
            "school_subsidy": 25.0,
            "date": datetime.now(UTC).isoformat(),
            "class_mappings": [
                {
                    "class_id": class_id,
                    "ticket_price": 15.0,
                }
            ],
        }
        update_resp = await test_client.put(
            f"/api/v1/events/{event_id}", json=update_payload, headers=a_headers
        )
        assert update_resp.status_code == 200

        updated_event = update_resp.json()
        assert updated_event["title"] == "New Expedition"
        assert updated_event["description"] == "Updated description"
        assert updated_event["address"] == "Mountain"
        assert float(updated_event["school_subsidy"]) == 25.0
        assert len(updated_event["class_mappings"]) == 1

        mapping = updated_event["class_mappings"][0]
        assert float(mapping["ticket_price"]) == 15.0

        # GET detail check
        get_resp = await test_client.get(f"/api/v1/events/{event_id}", headers=a_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["title"] == "New Expedition"

        # Setup another teacher and another class
        t2_payload = {
            "email": "teacher2@updateevent.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t2_reg = await test_client.post("/api/v1/auth/register", json=t2_payload)
        t2_headers = {"Authorization": f"Bearer {t2_reg.json()['access_token']}"}

        t2_me = await test_client.get("/api/v1/auth/me", headers=t2_headers)
        t2_id = int(t2_me.json()["user_id"])

        repo = TenantRepository(db_pool)
        await repo.create_teacher(t2_id, "Teacher Two")

        cls2_payload = {"name": "Science E", "level_id": lvl_id, "head_teacher_id": t2_id}
        cls2_resp = await test_client.post(
            "/api/v1/students/classes", json=cls2_payload, headers=a_headers
        )
        cls2_resp.json()["id"]

        # teacher2 (not mapped to event) tries to GET event_id
        get_restricted = await test_client.get(f"/api/v1/events/{event_id}", headers=t2_headers)
        assert get_restricted.status_code == 403

        # teacher2 tries to PUT event_id
        put_restricted = await test_client.put(
            f"/api/v1/events/{event_id}", json=update_payload, headers=t2_headers
        )
        assert put_restricted.status_code == 403


class TestAdminStaffCreation:
    async def test_admin_creates_manager(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        # 1. Register a school_admin (via a real invitation)
        admin_token = await register_school_admin(test_client, "school_admin@test.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 2. Register a manager (admin only)
        mgr_resp = await test_client.post(
            "/api/v1/students/managers",
            json={"email": "new_manager@test.com", "password": "pass"},
            headers=admin_headers,
        )
        assert mgr_resp.status_code == 200
        assert mgr_resp.json()["role"] == "manager"
        assert mgr_resp.json()["email"] == "new_manager@test.com"

        # 3. finance is retired -- the endpoint no longer exists.
        fin_resp = await test_client.post(
            "/api/v1/students/finance",
            json={"email": "new_finance@test.com", "password": "pass"},
            headers=admin_headers,
        )
        assert fin_resp.status_code == 404

        # 4. Teacher tries to create a manager (should fail with 403)
        from app.core.config import TEACHER_INVITE_CODE

        teacher_payload = {
            "email": "teacher_rand@test.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=teacher_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        fail_resp = await test_client.post(
            "/api/v1/students/managers",
            json={"email": "should_fail_mgr@test.com", "password": "pass"},
            headers=t_headers,
        )
        assert fail_resp.status_code == 403
