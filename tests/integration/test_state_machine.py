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

    # Setup roles and users
    user_repo = UserRepository(db_pool)
    repo = TenantRepository(db_pool)
    
    # 1. Create Teacher
    t_uid = await user_repo.create_user("teacher@school.com", "hash", "teacher")
    await repo.create_teacher(t_uid, "Teacher John")
    teacher = MockUser(t_uid, "teacher", "teacher@school.com")
    
    # 2. Create Manager
    m_uid = await user_repo.create_user("manager@school.com", "hash", "manager")
    manager = MockUser(m_uid, "manager", "manager@school.com")
    
    # 3. Create Finance
    f_uid = await user_repo.create_user("finance@school.com", "hash", "finance")
    finance = MockUser(f_uid, "finance", "finance@school.com")
    
    # Create Event Teacher
    et_uid = await user_repo.create_user("event_teacher@school.com", "hash", "event_teacher")
    event_teacher = MockUser(et_uid, "event_teacher", "event_teacher@school.com")
    
    # 4. Create Parent
    p_uid = await user_repo.create_user("parent@school.com", "hash", "parent")
    await repo.create_parent(p_uid, "Parent Sarah")
    parent = MockUser(p_uid, "parent", "parent@school.com")
    
    # Setup level & class
    level_id = await repo.create_level("Grade 10")
    class_id = await repo.create_class("Class 10A", level_id, t_uid)
    
    # Add a student to the class
    s_uid = await user_repo.create_user("student@school.com", "hash", "student")
    await repo.create_student(s_uid, "Student Alex", class_id)
    # Link parent
    await repo.add_student_parent_link(s_uid, p_uid)
    
    # Create Event in draft
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
    
    # Get initial status
    event = await repo.get_event_by_id(event_id)
    assert event["status"] == "draft"
    
    # Check permissions
    assert TenantService.check_event_permission(teacher, event, "read") is True
    assert TenantService.check_event_permission(teacher, event, "edit_draft") is True
    assert TenantService.check_event_permission(manager, event, "read") is False
    assert TenantService.check_event_permission(parent, event, "read") is False
    
    # 5. Submit to Event Teacher
    event = await TenantService.transition_event("tenant_a", event_id, "submit_to_event_teacher", teacher)
    assert event["status"] == "resource_planning"
    
    # 6. Add resource to the event (by event_teacher)
    # Fetch resource types and select a default bus type
    r_types = await repo.get_all_resource_types()
    bus_type = [r for r in r_types if "Bus" in r["name"]][0]
    
    await TenantService.add_resources_to_event(
        tenant_id="tenant_a",
        event_id=event_id,
        resources_list=[{"resource_type_id": bus_type["id"], "description": "Transport", "quantity": 2}],
        added_by_user_id=et_uid,
    )
    
    # Verify resources added
    res_summary = await TenantService.get_resource_summary("tenant_a", event_id)
    assert len(res_summary["resources"]) == 1
    assert res_summary["resources"][0]["quantity"] == 2
    
    # 7. Submit event for approval (illegal transitions checks)
    with pytest.raises(PermissionError):
        # Manager cannot submit for approval
        await TenantService.transition_event("tenant_a", event_id, "submit_for_approval", manager)
        
    students_in_db = await db_pool.fetch("SELECT id, name, class_id FROM students")
    print("\nDEBUG STUDENTS IN DB:", [dict(s) for s in students_in_db])

    # Legal transition: Event Teacher submits
    event = await TenantService.transition_event("tenant_a", event_id, "submit_for_approval", event_teacher)
    assert event["status"] == "proposed"
    assert event["submitted_at"] is not None
    assert event["predicted_attendance"] == 1 # 80% of 1 student is 1
    
    # Check updated permissions
    assert TenantService.check_event_permission(teacher, event, "edit_draft") is False
    assert TenantService.check_event_permission(manager, event, "manager_decision") is True
    assert TenantService.check_event_permission(finance, event, "read") is False
    
    # Check notifications sent to manager
    notifs = await repo.get_notifications_for_user(m_uid)
    assert len(notifs) >= 1
    assert "submitted for approval" in notifs[0]["title"]
    
    # 7. Manager rejects proposed event (requires reason)
    with pytest.raises(ValueError):
        await TenantService.transition_event("tenant_a", event_id, "manager_reject", manager, reason="")
        
    event = await TenantService.transition_event("tenant_a", event_id, "manager_reject", manager, reason="Needs more details")
    assert event["status"] == "draft"
    
    # Check notifications sent to teacher
    notifs = await repo.get_notifications_for_user(t_uid)
    assert len(notifs) >= 1
    assert "rejected by manager" in notifs[0]["title"]
    
    # Transition back to resource_planning then proposed
    event = await TenantService.transition_event("tenant_a", event_id, "submit_to_event_teacher", teacher)
    assert event["status"] == "resource_planning"
    event = await TenantService.transition_event("tenant_a", event_id, "submit_for_approval", event_teacher)
    assert event["status"] == "proposed"
    
    # 8. Manager approves proposed event
    event = await TenantService.transition_event("tenant_a", event_id, "manager_approve", manager)
    assert event["status"] == "finance_approval"
    assert event["manager_approved_at"] is not None
    assert event["manager_reviewer_id"] == m_uid
    
    # Check notifications sent to finance
    notifs = await repo.get_notifications_for_user(f_uid)
    assert len(notifs) >= 1
    assert "approved by manager, needs pricing" in notifs[0]["title"]
    
    # Check pricing permissions
    assert TenantService.check_event_permission(finance, event, "finance_pricing") is True
    
    # 9. Pricing block
    res_summary = await TenantService.get_resource_summary("tenant_a", event_id)
    resource_line = res_summary["resources"][0]
    
    # Finance sets cost
    await TenantService.set_resource_cost(
        tenant_id="tenant_a",
        resource_id=resource_line["id"],
        unit_price=150.0,
        currency="JOD",
        set_by_user_id=f_uid,
    )
    
    # Submit priced event (succeeds here because we only have 1 line and priced it)
    event = await TenantService.transition_event("tenant_a", event_id, "finance_submit", finance)
    assert event["status"] == "final_review"
    assert event["finance_priced_at"] is not None
    assert event["finance_reviewer_id"] == f_uid
    assert float(event["total_cost"]) == 300.0 # 150 * 2 = 300
    
    # 10. Manager return to finance
    event = await TenantService.transition_event("tenant_a", event_id, "manager_return_to_finance", manager, reason="Too expensive")
    assert event["status"] == "finance_approval"
    
    # Finance reprices to 100
    await TenantService.set_resource_cost(
        tenant_id="tenant_a",
        resource_id=resource_line["id"],
        unit_price=100.0,
        currency="JOD",
        set_by_user_id=f_uid,
    )
    
    event = await TenantService.transition_event("tenant_a", event_id, "finance_submit", finance)
    assert event["status"] == "final_review"
    assert float(event["total_cost"]) == 200.0 # 100 * 2 = 200
    
    # 11. Manager publishes
    event = await TenantService.transition_event("tenant_a", event_id, "manager_publish", manager)
    assert event["status"] == "published"
    assert event["published_at"] is not None
    
    # Check parent/student notifications
    s_notifs = await repo.get_notifications_for_user(s_uid)
    assert len(s_notifs) >= 1
    assert "New Event Published" in s_notifs[0]["title"]
    
    p_notifs = await repo.get_notifications_for_user(p_uid)
    assert len(p_notifs) >= 1
    assert "New Child Event" in p_notifs[0]["title"]
    
    # Verify final permissions
    assert TenantService.check_event_permission(parent, event, "read") is True
    assert TenantService.check_event_permission(teacher, event, "read") is True
