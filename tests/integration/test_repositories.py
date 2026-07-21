"""Integration tests for repositories."""

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.repositories.control_plane_repository import ControlPlaneRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService


# =============================================================================
# ControlPlaneRepository Tests
# =============================================================================
class TestControlPlaneRepository:
    async def test_create_and_get_parent(self, db_pool: asyncpg.Pool, clean_db):
        repo = ControlPlaneRepository(db_pool)
        hashed = AuthService.hash_password("password123")
        parent_id = await repo.create_parent("parent@school.com", hashed)
        assert parent_id is not None

        parent = await repo.get_parent_by_email("parent@school.com")
        assert parent is not None
        assert parent["id"] == parent_id
        assert parent["email"] == "parent@school.com"

        parent_by_id = await repo.get_parent_by_id(parent_id)
        assert parent_by_id is not None
        assert parent_by_id["email"] == "parent@school.com"

    async def test_create_and_get_super_admin(self, db_pool: asyncpg.Pool, clean_db):
        repo = ControlPlaneRepository(db_pool)
        hashed = AuthService.hash_password("admin123")
        sa_id = await repo.create_super_admin("admin@desk.com", hashed)
        assert sa_id is not None

        sa = await repo.get_super_admin_by_email("admin@desk.com")
        assert sa is not None
        assert sa["id"] == sa_id

    async def test_parent_child_links(self, db_pool: asyncpg.Pool, clean_db):
        repo = ControlPlaneRepository(db_pool)
        # Setup tenant
        await db_pool.execute(
            """
            INSERT INTO tenants (tenant_id, name, db_host, db_port, db_user, db_password, db_name)
            VALUES ('tenant_test', 'Test Tenant', '127.0.0.1', 5433, 'admin', 'pass', 'db')
            """
        )

        parent_id = await repo.create_parent("parent@link.com", "hash")
        student_id = uuid4()

        link_id = await repo.create_parent_child_link(parent_id, "tenant_test", student_id)
        assert link_id is not None

        links = await repo.get_links_for_parent(parent_id)
        assert len(links) == 1
        assert links[0]["student_id"] == student_id
        assert links[0]["tenant_id"] == "tenant_test"

        # Delete link
        deleted = await repo.delete_parent_child_link(parent_id, "tenant_test", student_id)
        assert deleted is True

        links_after = await repo.get_links_for_parent(parent_id)
        assert len(links_after) == 0


# =============================================================================
# UserRepository Tests
# =============================================================================
class TestUserRepository:
    async def test_create_tenant_user_succeeds(self, db_pool: asyncpg.Pool, clean_db):
        repo = UserRepository(db_pool)
        uid = await repo.create_user("teacher@school.com", "hash", "teacher")
        assert uid is not None

        user = await repo.get_user_by_id(uid)
        assert user is not None
        assert user["email"] == "teacher@school.com"
        assert user["role"] == "teacher"

    async def test_create_tenant_user_parent_role_succeeds(self, db_pool: asyncpg.Pool, clean_db):
        repo = UserRepository(db_pool)
        uid = await repo.create_user("parent@school.com", "hash", "parent")
        assert uid is not None


# =============================================================================
# TenantRepository Tests
# =============================================================================
class TestTenantRepository:
    async def test_levels_classes_and_students(self, db_pool: asyncpg.Pool, clean_db):
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        # Create level
        lvl_id = await repo.create_level("Year 5")
        assert lvl_id is not None

        all_lvl = await repo.get_all_levels()
        assert len(all_lvl) == 1
        assert all_lvl[0]["name"] == "Year 5"

        # Create Teacher
        t_uid = await user_repo.create_user("teacher@school.com", "hash", "teacher")
        t_id = await repo.create_teacher(t_uid, "Mr. Higgins")

        # Create Class
        class_id = await repo.create_class("Mathematics", lvl_id, t_id)
        assert class_id is not None

        # Create Student
        s_uid = await user_repo.create_user("student@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Alice Smith", class_id)
        assert student_id is not None

        student = await repo.get_student_by_id(student_id)
        assert student is not None
        assert student["name"] == "Alice Smith"
        assert student["class_id"] == class_id

    async def test_events_budgets_and_enrollments(self, db_pool: asyncpg.Pool, clean_db):
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Year 6")
        t_uid = await user_repo.create_user("teacher@class.com", "hash", "teacher")
        t_id = await repo.create_teacher(t_uid, "Mrs. Green")
        class_id = await repo.create_class("Science", lvl_id, t_id)

        s_uid = await user_repo.create_user("student@class.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Bob Johnson", class_id)

        # Create event map target
        event = await repo.create_event(
            title="Planetarium Tour",
            description="A trip to the cosmos",
            address="Planetarium Center",
            school_subsidy=5.00,
            date_val=datetime.now(timezone.utc),
            created_by=t_uid,
            class_mappings=[{
                "class_id": class_id,
                "ticket_price": 7.50,
            }]
        )
        assert event["id"] is not None
        assert len(event["class_mappings"]) == 1
        ecm_id = event["class_mappings"][0]["id"]

        # Enroll student in event class map
        enroll_id = await repo.create_enrollment(
            student_id=student_id,
            event_class_map_id=ecm_id,
            state="requested_by_student"
        )
        assert enroll_id is not None

        # Create payment record
        pay_id = await repo.create_payment(enrollment_id=enroll_id, amount=7.50, status="pending")
        assert pay_id is not None

        payment = await repo.get_payment_by_enrollment(enroll_id)
        assert payment is not None
        assert payment["status"] == "pending"
        assert float(payment["amount"]) == 7.50
