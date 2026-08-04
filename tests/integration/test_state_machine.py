from datetime import UTC, datetime

import asyncpg
import pytest

from app.domains.tenant.service import TenantService
from app.domains.tenant.tenant_repository import TenantRepository
from app.domains.tenant.user_repository import UserRepository


class MockUser:
    def __init__(self, id: int, role: str, email: str = ""):
        self.id = id
        self.role = role
        self.email = email

@pytest.mark.asyncio
async def test_state_machine_workflow(db_pool: asyncpg.Pool, monkeypatch):
    import app.core.database as db_module
    async def _mock_get_pool(_tenant_id: str) -> asyncpg.Pool:
        return db_pool
    monkeypatch.setattr(db_module.db_manager, "get_pool", _mock_get_pool)

    user_repo = UserRepository(db_pool)
    repo = TenantRepository(db_pool)
    
    t_uid = await user_repo.create_user("teacher@school.com", "hash", "teacher")
    await repo.create_teacher(t_uid, "Teacher John")
    teacher = MockUser(t_uid, "teacher", "teacher@school.com")
    
    m_uid = await user_repo.create_user("manager@school.com", "hash", "manager")
    manager = MockUser(m_uid, "manager", "manager@school.com")
    
    p_uid = await user_repo.create_user("parent@school.com", "hash", "parent")
    await repo.create_parent(p_uid, "Parent Sarah")
    parent = MockUser(p_uid, "parent", "parent@school.com")
    
    level_id = await repo.create_level("Grade 10")
    class_id = await repo.create_class("Class 10A", level_id, t_uid)
    
    s_uid = await user_repo.create_user("student@school.com", "hash", "student")
    await repo.create_student(s_uid, "Student Alex", class_id)
    await repo.add_student_parent_link(s_uid, p_uid)
    
    event_dict = await repo.create_event(
        title="Science Fair",
        description="Annual Science Fair",
        address="School Lab",
        school_subsidy=50.00,
        date_val=datetime.now(UTC),
        created_by=t_uid,
        class_mappings=[{"class_id": class_id, "ticket_price": 5.0, "budgets": []}],
    )
    event_id = event_dict["id"]
    
    event = await repo.get_event_by_id(event_id)
    assert event["status"] == "draft"
    
    assert TenantService.check_event_permission(teacher, event, "read") is True
    assert TenantService.check_event_permission(teacher, event, "edit_draft") is True
    assert TenantService.check_event_permission(manager, event, "read") is False
    assert TenantService.check_event_permission(parent, event, "read") is False
    
    r_types = await repo.get_all_resource_types()
    bus_type = [r for r in r_types if "Bus" in r["name"]][0]
    
    await TenantService.add_resources_to_event(
        tenant_id="tenant_a",
        event_id=event_id,
        resources_list=[{"resource_type_id": bus_type["id"], "description": "Transport", "quantity": 2}],
        added_by_user_id=t_uid,
    )
    
    # Transition to proposed so manager can price it
    await TenantService.transition_event("tenant_a", event_id, "submit_to_manager", teacher)
    
    await TenantService.set_resource_cost(
        tenant_id="tenant_a",
        resource_id=(await TenantService.get_resource_summary("tenant_a", event_id))["resources"][0]["id"],
        unit_price=100.0,
        currency="JOD",
        set_by_user_id=t_uid,
    )
    
    event = await repo.get_event_by_id(event_id)
    assert event["status"] == "proposed"
    
    assert TenantService.check_event_permission(teacher, event, "edit_draft") is False
    assert TenantService.check_event_permission(manager, event, "manager_decision") is True
    
    notifs = await repo.get_notifications_for_user(m_uid)
    assert len(notifs) >= 1
    
    with pytest.raises(ValueError):
        await TenantService.transition_event("tenant_a", event_id, "manager_reject", manager, reason="")
        
    event = await TenantService.transition_event("tenant_a", event_id, "manager_reject", manager, reason="Needs more details")
    assert event["status"] == "draft"
    
    notifs = await repo.get_notifications_for_user(t_uid)
    assert len(notifs) >= 1
    assert "rejected by manager" in notifs[-1]["title"]
    
    event = await TenantService.transition_event("tenant_a", event_id, "submit_to_manager", teacher)
    assert event["status"] == "proposed"
    
    event = await TenantService.transition_event("tenant_a", event_id, "manager_approve", manager)
    assert event["status"] == "ready_to_publish"
    assert event["manager_approved_at"] is not None
    assert event["manager_reviewer_id"] == m_uid
    
    event = await TenantService.transition_event("tenant_a", event_id, "teacher_publish", teacher)
    assert event["status"] == "published"
    assert event["published_at"] is not None
    
    s_notifs = await repo.get_notifications_for_user(s_uid)
    assert len(s_notifs) >= 1
    assert "New Event Published" in s_notifs[0]["title"]
    
    p_notifs = await repo.get_notifications_for_user(p_uid)
    assert len(p_notifs) >= 1
    assert "New Child Event" in p_notifs[0]["title"]
    
    assert TenantService.check_event_permission(parent, event, "read") is True
    assert TenantService.check_event_permission(teacher, event, "read") is True
    assert TenantService.check_event_permission(teacher, event, "edit_draft") is False
