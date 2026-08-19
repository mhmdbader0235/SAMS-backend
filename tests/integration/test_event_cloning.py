from datetime import datetime, timedelta

import asyncpg
import pytest
from httpx import AsyncClient

from app.domains.tenant.tenant_repository import TenantRepository
from tests.integration._helpers import register_school_admin


@pytest.mark.asyncio
async def test_event_cloning_flow(test_client: AsyncClient, db_pool: asyncpg.Pool):
    # 1. Register teacher
    r = await test_client.post("/api/v1/auth/register", json={
        "email": "cloner_teacher@school.com",
        "password": "password123",
        "tenant_id": "tenant_a",
        "role": "teacher",
        "invite_code": "SCHOOL-STAFF-2026"
    })
    assert r.status_code == 200
    t_token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {t_token}"}

    # 2. Get user id & Create Level & Class
    me_r = await test_client.get("/api/v1/auth/me", headers=headers)
    assert me_r.status_code == 200
    t_uid = int(me_r.json()["user_id"])

    r_lvl = await test_client.post("/api/v1/students/levels", json={"name": "Grade 11"}, headers=headers)
    assert r_lvl.status_code == 200
    level_id = r_lvl.json()["level_id"]

    r_cls = await test_client.post("/api/v1/students/classes", json={
        "name": "11A",
        "level_id": level_id,
        "head_teacher_id": t_uid
    }, headers=headers)
    assert r_cls.status_code == 200
    class_id = r_cls.json()["id"]

    # 2.5. Register school_admin (via a real invitation)
    a_token = await register_school_admin(test_client, "cloner_admin@school.com")
    a_headers = {"Authorization": f"Bearer {a_token}"}

    # 3. Create initial event
    dt_str = (datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
    r = await test_client.post("/api/v1/events", json={
        "title": "Original Museum Trip",
        "description": "Annual museum visit",
        "address": "City Museum",
        "school_subsidy": 50.0,
        "date": dt_str,
        "class_mappings": [{"class_id": class_id, "ticket_price": 10.0, "budgets": []}]
    }, headers=a_headers)
    assert r.status_code == 200
    orig_event = r.json()
    orig_id = orig_event["id"]

    # 4. Get resource types & add a resource request to original event
    r_rt = await test_client.get("/api/v1/events/resource-types", headers=a_headers)
    assert r_rt.status_code == 200
    r_types = r_rt.json()
    assert len(r_types) > 0
    rt_id = r_types[0]["id"]

    r = await test_client.post(f"/api/v1/events/{orig_id}/resources", json=[{
        "resource_type_id": rt_id,
        "description": "Bus for trip",
        "quantity": 2
    }], headers=a_headers)
    assert r.status_code == 200

    # 5. Clone the event
    r = await test_client.post(f"/api/v1/events/{orig_id}/clone", headers=a_headers)
    assert r.status_code == 200
    cloned_event = r.json()

    # 6. Assertions on cloned event
    assert cloned_event["id"] != orig_id
    assert cloned_event["title"] == "Template - Original Museum Trip"
    assert cloned_event["status"] == "draft"
    assert cloned_event["school_subsidy"] == 50.0
    assert len(cloned_event["class_mappings"]) == 1
    assert cloned_event["class_mappings"][0]["class_id"] == class_id

    # 7. Check cloned event resources
    r_res = await test_client.get(f"/api/v1/events/{cloned_event['id']}/resources", headers=a_headers)
    assert r_res.status_code == 200
    resources = r_res.json()["resources"]
    assert len(resources) == 1
    assert resources[0]["resource_type_id"] == rt_id
    assert resources[0]["quantity"] == 2
    assert resources[0]["description"] == "Bus for trip"
