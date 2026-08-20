from datetime import UTC, datetime

import asyncpg
import pytest
from httpx import AsyncClient

from app.domains.tenant.tenant_repository import TenantRepository


@pytest.mark.asyncio
async def test_workflow_routes(test_client: AsyncClient, db_pool: asyncpg.Pool):
    # Register manager & teacher users
    # Password invite code is SCHOOL-STAFF-2026
    r = await test_client.post("/api/v1/auth/register", json={
        "email": "teacher_wf@school.com",
        "password": "password",
        "tenant_id": "tenant_a",
        "role": "teacher",
        "invite_code": "SCHOOL-STAFF-2026"
    })
    assert r.status_code == 200
    t_token = r.json()["access_token"]
    
    r = await test_client.post("/api/v1/auth/register", json={
        "email": "manager_wf@school.com",
        "password": "password",
        "tenant_id": "tenant_a",
        "role": "manager",
        "invite_code": "SCHOOL-STAFF-2026"
    })
    assert r.status_code == 200
    m_token = r.json()["access_token"]
    
    r = await test_client.post("/api/v1/auth/register", json={
        "email": "et_wf@school.com",
        "password": "password",
        "tenant_id": "tenant_a",
        "role": "event_teacher",
        "invite_code": "SCHOOL-STAFF-2026"
    })
    assert r.status_code == 200
    et_token = r.json()["access_token"]

    # Retrieve headers
    t_headers = {"Authorization": f"Bearer {t_token}"}
    m_headers = {"Authorization": f"Bearer {m_token}"}
    et_headers = {"Authorization": f"Bearer {et_token}"}

    # Fetch resource types
    r = await test_client.get("/api/v1/events/resource-types", headers=t_headers)
    assert r.status_code == 200
    res_types = r.json()
    assert len(res_types) >= 6
    
    # Create custom resource type
    r = await test_client.post("/api/v1/events/resource-types", json={
        "name": "Custom Projector",
        "category": "other"
    }, headers=t_headers)
    assert r.status_code == 201
    custom_type_id = r.json()["id"]

    # Create event draft
    # First, create class
    # We must have level first
    lvl_r = await test_client.post("/api/v1/students/levels", json={"name": "Grade 11"}, headers=t_headers)
    assert lvl_r.status_code == 200
    lvl_id = lvl_r.json()["level_id"]
    
    # We need teacher user id
    me_r = await test_client.get("/api/v1/auth/me", headers=t_headers)
    t_uid = int(me_r.json()["user_id"])
    
    cls_r = await test_client.post("/api/v1/students/classes", json={
        "name": "11A",
        "level_id": lvl_id,
        "head_teacher_id": t_uid
    }, headers=t_headers)
    assert cls_r.status_code == 200
    class_id = cls_r.json()["id"]

    # Post event
    event_payload = {
        "title": "Historical Museum Trip",
        "description": "Visit to national museum",
        "address": "National Museum",
        "school_subsidy": 10.0,
        "date": datetime.now(UTC).isoformat(),
        "class_mappings": [{"class_id": class_id, "ticket_price": 5.0, "budgets": []}]
    }
    r = await test_client.post("/api/v1/events", json=event_payload, headers=t_headers)
    assert r.status_code == 200
    event_id = r.json()["id"]

    # Select audience (POST /events/{id}/audience)
    r = await test_client.post(f"/api/v1/events/{event_id}/audience", json={
        "class_ids": [class_id]
    }, headers=t_headers)
    assert r.status_code == 200
    assert r.json()["predicted_attendance"] == 0 # no students in class yet

    # Get audience prediction (GET /events/{id}/audience/prediction)
    r = await test_client.get(f"/api/v1/events/{event_id}/audience/prediction?class_ids={class_id}", headers=t_headers)
    assert r.status_code == 200
    assert r.json()["predicted_attendance"] == 0

    # Add resources
    r = await test_client.post(f"/api/v1/events/{event_id}/resources", json=[{
        "resource_type_id": custom_type_id,
        "description": "Custom resource line description",
        "quantity": 3
    }], headers=t_headers)
    assert r.status_code == 200
    
    # Get resources summary
    r = await test_client.get(f"/api/v1/events/{event_id}/resources", headers=t_headers)
    assert r.status_code == 200
    summary = r.json()
    assert len(summary["resources"]) == 1
    assert summary["resources"][0]["quantity"] == 3
    assert summary["total_cost"] == 0.0
    resource_id = summary["resources"][0]["id"]

    # Submit event (Teacher submits to manager)
    r = await test_client.post(f"/api/v1/events/{event_id}/submit", headers=t_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "proposed"

    # Verify manager queue
    r = await test_client.get("/api/v1/events/manager-queue", headers=m_headers)
    assert r.status_code == 200
    assert len(r.json()["events"]) >= 1

    # Manager approves
    r = await test_client.post(f"/api/v1/events/{event_id}/manager-decision", json={
        "decision": "approve"
    }, headers=m_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("approved", "ready_to_publish")

    # Teacher publishes
    r = await test_client.post(f"/api/v1/events/{event_id}/submit", headers=t_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"

    # Get published events (GET /events/published)
    # Register student
    st_r = await test_client.post("/api/v1/auth/register", json={
        "email": "student_wf@student.com",
        "password": "password",
        "tenant_id": "tenant_a",
        "role": "student",
        "invite_code": "regester123"
    })
    assert st_r.status_code == 200
    st_token = st_r.json()["access_token"]
    st_headers = {"Authorization": f"Bearer {st_token}"}
    
    # Get student user_id
    me_st = await test_client.get("/api/v1/auth/me", headers=st_headers)
    st_uid = int(me_st.json()["user_id"])
    
    # Add student to database class mapping
    repo = TenantRepository(db_pool)
    await repo.create_student(st_uid, "Student Alex", class_id)
    
    # Register parent
    pr_r = await test_client.post("/api/v1/auth/register", json={
        "email": "parent_wf@parent.com",
        "password": "password",
        "tenant_id": "tenant_a",
        "role": "parent",
        "invite_code": "regester123"
    })
    assert pr_r.status_code == 200
    p_headers = {"Authorization": f"Bearer {pr_r.json()['access_token']}"}
    
    # Get parent user_id
    me_p = await test_client.get("/api/v1/auth/me", headers=p_headers)
    p_uid = int(me_p.json()["user_id"])
    
    # Link parent and student
    await repo.add_student_parent_link(st_uid, p_uid)

    r = await test_client.get("/api/v1/events/published", headers=p_headers)
    assert r.status_code == 200
    pub_events = r.json()
    assert len(pub_events) >= 1
    assert "total_cost" not in pub_events[0]
    assert len(pub_events[0]["class_mappings"]) >= 1
    assert pub_events[0]["class_mappings"][0]["ticket_price"] == 0.0

    # 9. Student requests enrollment
    class_map_id = pub_events[0]["class_mappings"][0]["id"]
    enroll_resp = await test_client.post(
        "/api/v1/students/enrollments",
        json={"student_id": st_uid, "event_class_map_id": class_map_id},
        headers=st_headers
    )
    assert enroll_resp.status_code == 200
    assert enroll_resp.json()["state"] == "requested_by_student"
    enrollment_id = enroll_resp.json()["id"]

    # 10. Teacher tries to approve directly (should fail with HTTP 400 because parent has not approved yet)
    fail_app_resp = await test_client.post(
        f"/api/v1/students/enrollments/{enrollment_id}/approve",
        json={"state": "approved_by_teacher"},
        headers=t_headers
    )
    assert fail_app_resp.status_code == 400

    # 11. Parent approves student enrollment
    parent_app_resp = await test_client.post(
        f"/api/v1/students/enrollments/{enrollment_id}/approve",
        json={"state": "approved_by_parent"},
        headers=p_headers
    )
    assert parent_app_resp.status_code == 200
    assert parent_app_resp.json()["state"] == "approved_by_parent"

    # 12. Teacher approves enrollment
    teacher_app_resp = await test_client.post(
        f"/api/v1/students/enrollments/{enrollment_id}/approve",
        json={"state": "approved_by_teacher"},
        headers=t_headers
    )
    assert teacher_app_resp.status_code == 200
    assert teacher_app_resp.json()["state"] == "approved_by_teacher"
