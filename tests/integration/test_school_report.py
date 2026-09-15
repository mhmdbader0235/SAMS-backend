"""GET /api/v1/analytics/school-report and its CSV export.

Previously missing entirely: the frontend (ReportsView.vue, api.js's
apiGetSchoolReport/apiExportSchoolReportCsv) and the `report:view` capability
gate existed, but back/app/domains/analytics/router.py had no endpoint for
it at all -- a "five places" gap (router/service/repository missing, view/
store/api.js present). These pin the real behaviour now that all five exist.
"""

from tests.integration._helpers import register_school_admin


async def test_school_admin_gets_a_well_shaped_empty_report(test_client):
    """A brand-new tenant with no classes/events/payments yet must return
    empty lists, not a 500 -- the report page renders "No classes in the
    active academic year." etc. for exactly this response shape."""
    token = await register_school_admin(test_client, "admin@tenant-report.com", "tenant_a")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await test_client.get("/api/v1/analytics/school-report", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enrollment_by_class"] == []
    assert body["event_participation"] == []
    assert body["payment_status"] == []


async def test_school_admin_report_reflects_real_class_enrollment(test_client):
    token = await register_school_admin(test_client, "admin2@tenant-report.com", "tenant_a")
    headers = {"Authorization": f"Bearer {token}"}

    level_resp = await test_client.post(
        "/api/v1/students/levels", json={"name": "Grade 1"}, headers=headers
    )
    assert level_resp.status_code == 200, level_resp.text
    level_id = level_resp.json()["level_id"]

    class_resp = await test_client.post(
        "/api/v1/students/classes",
        json={"name": "1A", "level_id": level_id},
        headers=headers,
    )
    assert class_resp.status_code == 200, class_resp.text
    class_id = class_resp.json()["id"]

    student_resp = await test_client.post(
        "/api/v1/students",
        json={
            "email": "student-report@tenant-report.com",
            "password": "studentpass123",
            "name": "Report Student",
            "class_id": class_id,
        },
        headers=headers,
    )
    assert student_resp.status_code == 200, student_resp.text

    resp = await test_client.get("/api/v1/analytics/school-report", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["enrollment_by_class"]
    assert len(rows) == 1
    assert rows[0]["class_id"] == class_id
    assert rows[0]["level_name"] == "Grade 1"
    assert rows[0]["class_name"] == "1A"
    assert rows[0]["student_count"] == 1
    assert rows[0]["capacity"] == 25


async def test_csv_export_matches_the_enrollment_report(test_client):
    token = await register_school_admin(test_client, "admin3@tenant-report.com", "tenant_a")
    headers = {"Authorization": f"Bearer {token}"}

    level_resp = await test_client.post(
        "/api/v1/students/levels", json={"name": "Grade 2"}, headers=headers
    )
    level_id = level_resp.json()["level_id"]
    class_resp = await test_client.post(
        "/api/v1/students/classes",
        json={"name": "2B", "level_id": level_id},
        headers=headers,
    )
    assert class_resp.status_code == 200, class_resp.text

    resp = await test_client.get("/api/v1/analytics/school-report/export.csv", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.text
    assert "level_name,class_name,student_count,capacity" in body
    assert "Grade 2,2B,0,25" in body


async def test_teacher_cannot_view_the_school_report(test_client):
    """report:view is school_admin-scoped; a bare teacher must not be able
    to pull the whole school's payment/enrollment numbers."""
    from tests.integration._helpers import get_super_admin_token

    sa_token = await get_super_admin_token(test_client)
    invite_resp = await test_client.post(
        "/api/v1/auth/invitations",
        json={"tenant_id": "tenant_a", "role": "teacher", "target_email": "teacher-report@x.com"},
        headers={"Authorization": f"Bearer {sa_token}"},
    )
    invite_code = invite_resp.json()["code"]
    reg_resp = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "teacher-report@x.com",
            "password": "teacherpass123",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": invite_code,
        },
    )
    assert reg_resp.status_code == 200, reg_resp.text
    teacher_token = reg_resp.json()["access_token"]

    resp = await test_client.get(
        "/api/v1/analytics/school-report",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403
