"""Regression test for the fixed unguarded class-roster endpoint.

invariant: GET /api/v1/students/classes/{class_id}/students must reject any
caller who is not staff (school_admin/super_admin/manager/teacher). This
endpoint used to have no role check at all, leaking every pupil's name,
gender, birth_data and aggregated parent_names to any authenticated user —
see SAMS_Academic_Model_Next_Steps.md §0.1 (now banner-marked fixed).
"""

import asyncpg
from httpx import AsyncClient

from app.core.config import TEACHER_INVITE_CODE
from tests.integration._helpers import register_school_admin


async def _make_class(test_client: AsyncClient, admin_headers: dict) -> int:
    level_resp = await test_client.post(
        "/api/v1/students/levels",
        json={
            "name": "Grade 5",
            "isced_level": 1,
            "age_band_min": 10,
            "age_band_max": 11,
            "ordinal": 5,
        },
        headers=admin_headers,
    )
    assert level_resp.status_code == 200, level_resp.text
    level_id = level_resp.json()["level_id"]

    class_resp = await test_client.post(
        "/api/v1/students/classes",
        json={"name": "5A", "level_id": level_id},
        headers=admin_headers,
    )
    assert class_resp.status_code == 200, class_resp.text
    return class_resp.json()["id"]


class TestClassRosterAuthz:
    async def test_student_cannot_read_class_roster(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        admin_token = await register_school_admin(test_client, "admin_roster_authz@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        class_id = await _make_class(test_client, admin_headers)

        student_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "student_roster_authz@school.com",
                "password": "studentpass",
                "role": "student",
                "tenant_id": "tenant_a",
                "invite_code": "regester123",
            },
        )
        assert student_reg.status_code == 200, student_reg.text
        student_token = student_reg.json()["access_token"]

        resp = await test_client.get(
            f"/api/v1/students/classes/{class_id}/students",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert resp.status_code == 403
        assert "Forbidden" in resp.json()["detail"]

    async def test_teacher_can_read_class_roster(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        admin_token = await register_school_admin(test_client, "admin_roster_authz2@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        class_id = await _make_class(test_client, admin_headers)

        teacher_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "teacher_roster_authz@school.com",
                "password": "teacherpass",
                "role": "teacher",
                "tenant_id": "tenant_a",
                "invite_code": TEACHER_INVITE_CODE,
            },
        )
        assert teacher_reg.status_code == 200, teacher_reg.text
        teacher_token = teacher_reg.json()["access_token"]

        resp = await test_client.get(
            f"/api/v1/students/classes/{class_id}/students",
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
