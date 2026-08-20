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
    async def test_register_and_login_super_admin(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import SUPER_ADMIN_BOOTSTRAP_CODE

        reg_payload = {
            "email": "sa@desk.com",
            "password": "sapassword123",
            "role": "super_admin",
            "invite_code": SUPER_ADMIN_BOOTSTRAP_CODE
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

    async def test_register_and_login_parent(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        reg_payload = {
            "email": "parent@school.com",
            "password": "parentpassword",
            "role": "parent",
            "tenant_id": "tenant_a",
            "invite_code": "regester123"
        }
        reg_resp = await test_client.post("/api/v1/auth/register", json=reg_payload)
        assert reg_resp.status_code == 200
        token = reg_resp.json()["access_token"]

        me_resp = await test_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["role"] == "parent"
        assert me_resp.json()["tenant_id"] == "tenant_a"

    async def test_register_and_login_teacher(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        
        reg_payload = {
            "email": "teacher@school.com",
            "password": "teacherpass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        reg_resp = await test_client.post("/api/v1/auth/register", json=reg_payload)
        assert reg_resp.status_code == 200
        token = reg_resp.json()["access_token"]

        me_resp = await test_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["role"] == "teacher"

    async def test_register_and_login_student(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        # Student registration automatically maps level and class if none exists
        reg_payload = {
            "email": "student@school.com",
            "password": "studentpass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123"
        }
        reg_resp = await test_client.post("/api/v1/auth/register", json=reg_payload)
        assert reg_resp.status_code == 200
        token = reg_resp.json()["access_token"]

        me_resp = await test_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["role"] == "student"


# =============================================================================
# Student and Class Router Tests
# =============================================================================
class TestStudentsAndClassesRouter:
    async def test_staff_can_manage_levels_and_classes(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # Setup teacher
        t_payload = {
            "email": "teacher@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Create level
        lvl_resp = await test_client.post("/api/v1/students/levels", json={"name": "Grade 5"}, headers=headers)
        assert lvl_resp.status_code == 200
        lvl_id = lvl_resp.json()["level_id"]

        # List levels
        lvls_list = await test_client.get("/api/v1/students/levels", headers=headers)
        assert lvls_list.status_code == 200
        assert len(lvls_list.json()) == 1

        # Fetch teachers list
        teachers_list = await test_client.get("/api/v1/students/teachers", headers=headers)
        assert teachers_list.status_code == 200
        t_id = teachers_list.json()[0]["id"]

        # Create Class
        cls_payload = {
            "name": "Class A",
            "level_id": lvl_id,
            "head_teacher_id": t_id
        }
        cls_resp = await test_client.post("/api/v1/students/classes", json=cls_payload, headers=headers)
        assert cls_resp.status_code == 200
        assert cls_resp.json()["name"] == "Class A"

    async def test_duplicate_level_and_class_prevention(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # Setup teacher
        t_payload = {
            "email": "teacher_dup@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # 1. Create a level
        lvl_resp1 = await test_client.post("/api/v1/students/levels", json={"name": "Grade 6"}, headers=headers)
        assert lvl_resp1.status_code == 200
        lvl_id1 = lvl_resp1.json()["level_id"]

        # 2. Try to create the same level (case-insensitive and trimmed)
        lvl_resp2 = await test_client.post("/api/v1/students/levels", json={"name": "  grade 6  "}, headers=headers)
        assert lvl_resp2.status_code == 200
        lvl_id2 = lvl_resp2.json()["level_id"]
        
        # They should return the exact same level_id
        assert lvl_id1 == lvl_id2

        # 3. Create a class
        teachers_list = await test_client.get("/api/v1/students/teachers", headers=headers)
        assert teachers_list.status_code == 200
        t_id = teachers_list.json()[0]["id"]

        cls_payload1 = {
            "name": "Class B",
            "level_id": lvl_id1,
            "head_teacher_id": t_id
        }
        cls_resp1 = await test_client.post("/api/v1/students/classes", json=cls_payload1, headers=headers)
        assert cls_resp1.status_code == 200
        cls_id1 = cls_resp1.json()["id"]

        # 4. Try to create the same class under same level (case-insensitive and trimmed)
        cls_payload2 = {
            "name": "  class b  ",
            "level_id": lvl_id1,
            "head_teacher_id": t_id
        }
        cls_resp2 = await test_client.post("/api/v1/students/classes", json=cls_payload2, headers=headers)
        assert cls_resp2.status_code == 200
        cls_id2 = cls_resp2.json()["id"]

        # They should return the exact same class ID
        assert cls_id1 == cls_id2

    async def test_update_class_head_teacher(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # Setup teacher 1 & teacher 2
        t1_payload = {
            "email": "head1@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t1_reg = await test_client.post("/api/v1/auth/register", json=t1_payload)
        t1_headers = {"Authorization": f"Bearer {t1_reg.json()['access_token']}"}

        t2_payload = {
            "email": "head2@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t2_reg = await test_client.post("/api/v1/auth/register", json=t2_payload)

        # Create level & class assigned to teacher 1
        lvl_resp = await test_client.post("/api/v1/students/levels", json={"name": "Grade 10"}, headers=t1_headers)
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t1_headers)
        t_objs = teachers_list.json()
        t1_id = next(t["id"] for t in t_objs if t["email"] == "head1@school.com")
        t2_id = next(t["id"] for t in t_objs if t["email"] == "head2@school.com")

        cls_resp = await test_client.post("/api/v1/students/classes", json={"name": "Grade 10A", "level_id": lvl_id, "head_teacher_id": t1_id}, headers=t1_headers)
        assert cls_resp.status_code == 200
        cls_id = cls_resp.json()["id"]
        assert cls_resp.json()["head_teacher_id"] == t1_id

        # Update class head teacher to teacher 2
        update_resp = await test_client.put(f"/api/v1/students/classes/{cls_id}", json={"head_teacher_id": t2_id, "name": "Grade 10-A Updated"}, headers=t1_headers)
        assert update_resp.status_code == 200
        assert update_resp.json()["head_teacher_id"] == t2_id
        assert update_resp.json()["name"] == "Grade 10-A Updated"

    async def test_student_enrollments_and_approvals(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # 1. Setup teacher & class
        t_payload = {
            "email": "teacher@class.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        lvl_resp = await test_client.post("/api/v1/students/levels", json={"name": "Grade 6"}, headers=t_headers)
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_payload = {"name": "Science B", "level_id": lvl_id, "head_teacher_id": t_id}
        cls_resp = await test_client.post("/api/v1/students/classes", json=cls_payload, headers=t_headers)
        class_id = cls_resp.json()["id"]

        # 2. Setup Student
        s_payload = {
            "email": "student@class.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123"
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
            "class_mappings": [{
                "class_id": class_id,
                "ticket_price": 6.0,
            }]
        }
        event_resp = await test_client.post("/api/v1/events", json=event_payload, headers=t_headers)
        if event_resp.status_code != 200:
            print("\nEVENT RESP ERROR DETAILS:", event_resp.text)
        assert event_resp.status_code == 200
        ecm_id = event_resp.json()["class_mappings"][0]["id"]

        # Register Parent & Link Student
        p_payload = {
            "email": "parent_wf_new@class.com",
            "password": "pass",
            "role": "parent",
            "tenant_id": "tenant_a",
            "invite_code": "regester123"
        }
        p_reg = await test_client.post("/api/v1/auth/register", json=p_payload)
        p_headers = {"Authorization": f"Bearer {p_reg.json()['access_token']}"}
        p_me = await test_client.get("/api/v1/auth/me", headers=p_headers)
        parent_id = int(p_me.json()["user_id"])

        link_resp = await test_client.post(
            "/api/v1/students/link-parent",
            json={"student_id": student_id, "parent_id": parent_id},
            headers=t_headers
        )
        assert link_resp.status_code == 200

        # 4. Student requests enrollment
        enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": ecm_id},
            headers=s_headers
        )
        assert enroll_resp.status_code == 200
        assert enroll_resp.json()["state"] == "requested_by_student"
        enrollment_id = enroll_resp.json()["id"]

        # 5. Teacher attempts to approve directly (should fail with 400 because parent hasn't approved yet)
        failed_app_resp = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_teacher"},
            headers=t_headers
        )
        assert failed_app_resp.status_code == 400
        assert "must be approved by a parent" in failed_app_resp.json()["detail"]

        # 6. Parent approves enrollment (state becomes approved_by_parent)
        parent_app_resp = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_parent"},
            headers=p_headers
        )
        assert parent_app_resp.status_code == 200
        assert parent_app_resp.json()["state"] == "approved_by_parent"

        # 7. Teacher approves enrollment (state becomes approved_by_teacher)
        app_resp = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_teacher"},
            headers=t_headers
        )
        assert app_resp.status_code == 200
        assert app_resp.json()["state"] == "approved_by_teacher"

    async def test_parent_direct_enrollment_and_teacher_approval(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # 1. Setup teacher & class
        t_payload = {
            "email": "teacher2@class.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        lvl_resp = await test_client.post("/api/v1/students/levels", json={"name": "Grade 7"}, headers=t_headers)
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_payload = {"name": "Science C", "level_id": lvl_id, "head_teacher_id": t_id}
        cls_resp = await test_client.post("/api/v1/students/classes", json=cls_payload, headers=t_headers)
        class_id = cls_resp.json()["id"]

        # 2. Setup Student
        s_payload = {
            "email": "student2@class.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123"
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
            "invite_code": "regester123"
        }
        p_reg = await test_client.post("/api/v1/auth/register", json=p_payload)
        p_headers = {"Authorization": f"Bearer {p_reg.json()['access_token']}"}
        p_me = await test_client.get("/api/v1/auth/me", headers=p_headers)
        parent_id = int(p_me.json()["user_id"])

        # Link parent and student (via teacher/staff)
        link_resp = await test_client.post(
            "/api/v1/students/link-parent",
            json={"student_id": student_id, "parent_id": parent_id},
            headers=t_headers
        )
        assert link_resp.status_code == 200

        event_payload = {
            "title": "Astronomy Night",
            "description": "Star hunting",
            "address": "Astrodome",
            "school_subsidy": 5.0,
            "date": datetime.now(UTC).isoformat(),
            "class_mappings": [{
                "class_id": class_id,
                "ticket_price": 7.0,
            }]
        }
        event_resp = await test_client.post("/api/v1/events", json=event_payload, headers=t_headers)
        assert event_resp.status_code == 200
        ecm_id = event_resp.json()["class_mappings"][0]["id"]

        # 5. Parent attempts to enroll non-linked student (should fail with 403)
        bad_enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": 9999, "event_class_map_id": ecm_id},
            headers=p_headers
        )
        assert bad_enroll_resp.status_code == 403

        # 6. Parent directly enrolls their child (state becomes approved_by_parent)
        enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": ecm_id},
            headers=p_headers
        )
        assert enroll_resp.status_code == 200
        assert enroll_resp.json()["state"] == "approved_by_parent"
        enrollment_id = enroll_resp.json()["id"]

        # 7. Parent tries to approve another student's enrollment (not linked to them) (should fail with 403)
        bad_approve_resp = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_parent"},
            headers=s_headers  # student doesn't have parent relationship
        )
        assert bad_approve_resp.status_code == 403

        # 8. Teacher approves enrollment (state becomes approved_by_teacher)
        app_resp = await test_client.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            json={"state": "approved_by_teacher"},
            headers=t_headers
        )
        assert app_resp.status_code == 200
        assert app_resp.json()["state"] == "approved_by_teacher"

    async def test_student_class_match_and_one_time_enrollment(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # 1. Setup teacher, level, and Class A
        t_payload = {
            "email": "teacher3@class.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        lvl_resp = await test_client.post("/api/v1/students/levels", json={"name": "Grade 8"}, headers=t_headers)
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_resp1 = await test_client.post("/api/v1/students/classes", json={"name": "Science D", "level_id": lvl_id, "head_teacher_id": t_id}, headers=t_headers)
        class_id_1 = cls_resp1.json()["id"]

        cls_resp2 = await test_client.post("/api/v1/students/classes", json={"name": "Science E", "level_id": lvl_id, "head_teacher_id": t_id}, headers=t_headers)
        class_id_2 = cls_resp2.json()["id"]

        # 2. Setup Student (assigned to Class A/class_id_1)
        s_payload = {
            "email": "student3@class.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123"
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
                }
            ]
        }
        event_resp = await test_client.post("/api/v1/events", json=event_payload, headers=t_headers)
        assert event_resp.status_code == 200
        mappings = event_resp.json()["class_mappings"]
        
        # Identify mapping IDs
        map_id_1 = next(m["id"] for m in mappings if m["class_id"] == class_id_1)
        map_id_2 = next(m["id"] for m in mappings if m["class_id"] == class_id_2)

        # Verify profile includes class details
        prof_resp = await test_client.get("/api/v1/auth/profile", headers=s_headers)
        assert prof_resp.status_code == 200
        assert prof_resp.json()["class_id"] == class_id_1

        # 4. Student attempts to enroll in Class B mapping (should fail with 400)
        bad_enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": map_id_2},
            headers=s_headers
        )
        assert bad_enroll_resp.status_code == 400
        assert "not in the class mapped to this event" in bad_enroll_resp.json()["detail"]

        # 5. Student enrolls in their own Class A mapping (should succeed)
        good_enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": map_id_1},
            headers=s_headers
        )
        assert good_enroll_resp.status_code == 200

        # 6. Student attempts to enroll again in Class A mapping (should return the same ID)
        dup_enroll_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": map_id_1},
            headers=s_headers
        )
        assert dup_enroll_resp.status_code == 200
        assert dup_enroll_resp.json()["id"] == good_enroll_resp.json()["id"]

        # 7. Student attempts to enroll in Class B mapping after already enrolling in Class A mapping (should fail with 400)
        dup_event_resp = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": student_id, "event_class_map_id": map_id_2},
            headers=s_headers
        )
        assert dup_event_resp.status_code == 400
        assert "already enrolled in this event" in dup_event_resp.json()["detail"]

    async def test_linked_profile_details(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # 1. Setup teacher and class
        t_payload = {
            "email": "teacher4@class.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        lvl_resp = await test_client.post("/api/v1/students/levels", json={"name": "Grade 9"}, headers=t_headers)
        lvl_id = lvl_resp.json()["level_id"]

        t_me = await test_client.get("/api/v1/auth/me", headers=t_headers)
        t_id = int(t_me.json()["user_id"])

        cls_resp = await test_client.post("/api/v1/students/classes", json={"name": "Science G", "level_id": lvl_id, "head_teacher_id": t_id}, headers=t_headers)
        class_id = cls_resp.json()["id"]

        # 2. Register parent
        p_payload = {
            "email": "parent4@class.com",
            "password": "pass",
            "role": "parent",
            "tenant_id": "tenant_a",
            "invite_code": "regester123"
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
            "invite_code": "regester123"
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
            headers=t_headers
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

    async def test_parent_two_children_different_classes_only_enrolls_eligible_child(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # 1. Setup staff & 2 classes: Class A and Class B
        t_payload = {
            "email": "teacher_two_kids@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        lvl_resp = await test_client.post("/api/v1/students/levels", json={"name": "Grade 5"}, headers=t_headers)
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls1_resp = await test_client.post("/api/v1/students/classes", json={"name": "Class 5A", "level_id": lvl_id, "head_teacher_id": t_id}, headers=t_headers)
        class_a_id = cls1_resp.json()["id"]

        cls2_resp = await test_client.post("/api/v1/students/classes", json={"name": "Class 5B", "level_id": lvl_id, "head_teacher_id": t_id}, headers=t_headers)
        class_b_id = cls2_resp.json()["id"]

        # 2. Setup 2 Students (Child 1 in Class A, Child 2 in Class B)
        s1_reg = await test_client.post("/api/v1/auth/register", json={"email": "child1@school.com", "password": "pass", "role": "student", "tenant_id": "tenant_a", "invite_code": "regester123"})
        s1_headers = {"Authorization": f"Bearer {s1_reg.json()['access_token']}"}
        s1_me = await test_client.get("/api/v1/auth/me", headers=s1_headers)
        child1_id = int(s1_me.json()["user_id"])

        s2_reg = await test_client.post("/api/v1/auth/register", json={"email": "child2@school.com", "password": "pass", "role": "student", "tenant_id": "tenant_a", "invite_code": "regester123"})
        s2_headers = {"Authorization": f"Bearer {s2_reg.json()['access_token']}"}
        s2_me = await test_client.get("/api/v1/auth/me", headers=s2_headers)
        child2_id = int(s2_me.json()["user_id"])

        repo = TenantRepository(db_pool)
        await repo.create_student(child1_id, "Ahmad (Class A)", class_a_id)
        await repo.create_student(child2_id, "Sami (Class B)", class_b_id)

        # 3. Setup Parent linked to BOTH Child 1 and Child 2
        p_reg = await test_client.post("/api/v1/auth/register", json={"email": "parent_two_kids@school.com", "password": "pass", "role": "parent", "tenant_id": "tenant_a", "invite_code": "regester123"})
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
            "class_mappings": [{"class_id": class_a_id, "ticket_price": 15.0}]
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
            headers=p_headers
        )
        assert enroll_c1.status_code == 200
        assert enroll_c1.json()["state"] == "approved_by_parent"

        # 7. Parent attempts to enroll Child 2 (Class B) into Class A event -> FAILS with 400
        enroll_c2 = await test_client.post(
            "/api/v1/students/enrollments",
            json={"student_id": child2_id, "event_class_map_id": class_a_map_id},
            headers=p_headers
        )
        assert enroll_c2.status_code == 400
        assert "not in the class" in enroll_c2.json()["detail"].lower()





# =============================================================================
# Notifications Tests
# =============================================================================
class TestNotificationsRouter:
    async def test_notification_delivery(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # 1. Setup teacher and class
        t_payload = {
            "email": "teacher@notif.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        lvl_resp = await test_client.post("/api/v1/students/levels", json={"name": "Grade 7"}, headers=t_headers)
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_payload = {"name": "Science C", "level_id": lvl_id, "head_teacher_id": t_id}
        cls_resp = await test_client.post("/api/v1/students/classes", json=cls_payload, headers=t_headers)
        class_id = cls_resp.json()["id"]

        # 2. Setup Student
        s_payload = {
            "email": "student@notif.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123"
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
            "class_mappings": [{
                "class_id": class_id,
                "ticket_price": 0.0,
                "costbudget_id": None
            }]
        }
        await test_client.post("/api/v1/events", json=event_payload, headers=t_headers)

        # 4. Check notification delivery
        notif_resp = await test_client.get("/api/v1/notifications", headers=s_headers)
        assert notif_resp.status_code == 200
        assert len(notif_resp.json()["notifications"]) == 1
        notif_id = notif_resp.json()["notifications"][0]["id"]

        # Mark read
        read_resp = await test_client.post(f"/api/v1/notifications/{notif_id}/read", headers=s_headers)
        assert read_resp.status_code == 200


# =============================================================================
# PII Student Health & Records
# =============================================================================
class TestStudentHealthRouter:
    async def test_health_records_pii(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # Setup teacher
        t_payload = {
            "email": "teacher@health.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Setup student
        s_payload = {
            "email": "student@health.com",
            "password": "pass",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123"
        }
        s_reg = await test_client.post("/api/v1/auth/register", json=s_payload)
        s_me = await test_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {s_reg.json()['access_token']}"})
        student_id = int(s_me.json()["user_id"])

        # Insert health record
        h_payload = {
            "national_id": "NAT-12345",
            "medical_conditions": "Allergy to peanuts",
            "emergency_contact": "+1-202-555-0143"
        }
        h_resp = await test_client.post(f"/api/v1/students/{student_id}/health", json=h_payload, headers=t_headers)
        assert h_resp.status_code == 200

        # Retrieve masked
        get_resp = await test_client.get(f"/api/v1/students/{student_id}/health", headers=t_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["is_masked"] is True
        assert get_resp.json()["national_id"] != "NAT-12345"


class TestEventUpdateRouter:
    async def test_update_event_and_class_mappings(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE
        # Setup teacher
        t_payload = {
            "email": "teacher@updateevent.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=t_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        # Setup Class
        lvl_resp = await test_client.post("/api/v1/students/levels", json={"name": "Grade 8"}, headers=t_headers)
        lvl_id = lvl_resp.json()["level_id"]

        teachers_list = await test_client.get("/api/v1/students/teachers", headers=t_headers)
        t_id = teachers_list.json()[0]["id"]

        cls_payload = {"name": "Science D", "level_id": lvl_id, "head_teacher_id": t_id}
        cls_resp = await test_client.post("/api/v1/students/classes", json=cls_payload, headers=t_headers)
        class_id = cls_resp.json()["id"]

        # Setup school_admin (via a real invitation)
        a_token = await register_school_admin(test_client, "admin@updateevent.com")
        a_headers = {"Authorization": f"Bearer {a_token}"}

        # Create Event
        event_payload = {
            "title": "Old Expedition",
            "description": "Original description",
            "address": "Cave",
            "school_subsidy": 10.0,
            "date": datetime.now(UTC).isoformat(),
            "class_mappings": [{
                "class_id": class_id,
                "ticket_price": 5.0,
            }]
        }
        create_resp = await test_client.post("/api/v1/events", json=event_payload, headers=a_headers)
        assert create_resp.status_code == 200
        event_id = create_resp.json()["id"]

        # Update Event (PUT)
        update_payload = {
            "title": "New Expedition",
            "description": "Updated description",
            "address": "Mountain",
            "school_subsidy": 25.0,
            "date": datetime.now(UTC).isoformat(),
            "class_mappings": [{
                "class_id": class_id,
                "ticket_price": 15.0,
            }]
        }
        update_resp = await test_client.put(f"/api/v1/events/{event_id}", json=update_payload, headers=a_headers)
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
            "invite_code": TEACHER_INVITE_CODE
        }
        t2_reg = await test_client.post("/api/v1/auth/register", json=t2_payload)
        t2_headers = {"Authorization": f"Bearer {t2_reg.json()['access_token']}"}

        t2_me = await test_client.get("/api/v1/auth/me", headers=t2_headers)
        t2_id = int(t2_me.json()["user_id"])
        
        repo = TenantRepository(db_pool)
        await repo.create_teacher(t2_id, "Teacher Two")

        cls2_payload = {"name": "Science E", "level_id": lvl_id, "head_teacher_id": t2_id}
        cls2_resp = await test_client.post("/api/v1/students/classes", json=cls2_payload, headers=t_headers)
        class2_id = cls2_resp.json()["id"]

        # teacher2 (not mapped to event) tries to GET event_id
        get_restricted = await test_client.get(f"/api/v1/events/{event_id}", headers=t2_headers)
        assert get_restricted.status_code == 403

        # teacher2 tries to PUT event_id
        put_restricted = await test_client.put(f"/api/v1/events/{event_id}", json=update_payload, headers=t2_headers)
        assert put_restricted.status_code == 403


class TestAdminStaffCreation:
    async def test_admin_creates_manager(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        # 1. Register a school_admin (via a real invitation)
        admin_token = await register_school_admin(test_client, "school_admin@test.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 2. Register a manager (admin only)
        mgr_resp = await test_client.post(
            "/api/v1/students/managers",
            json={"email": "new_manager@test.com", "password": "pass"},
            headers=admin_headers
        )
        assert mgr_resp.status_code == 200
        assert mgr_resp.json()["role"] == "manager"
        assert mgr_resp.json()["email"] == "new_manager@test.com"

        # 3. finance is retired -- the endpoint no longer exists.
        fin_resp = await test_client.post(
            "/api/v1/students/finance",
            json={"email": "new_finance@test.com", "password": "pass"},
            headers=admin_headers
        )
        assert fin_resp.status_code == 404

        # 4. Teacher tries to create a manager (should fail with 403)
        from app.core.config import TEACHER_INVITE_CODE
        teacher_payload = {
            "email": "teacher_rand@test.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        t_reg = await test_client.post("/api/v1/auth/register", json=teacher_payload)
        t_headers = {"Authorization": f"Bearer {t_reg.json()['access_token']}"}

        fail_resp = await test_client.post(
            "/api/v1/students/managers",
            json={"email": "should_fail_mgr@test.com", "password": "pass"},
            headers=t_headers
        )
        assert fail_resp.status_code == 403
