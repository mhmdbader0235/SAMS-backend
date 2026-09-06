"""Integration tests for repositories."""

from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from app.domains.auth.service import AuthService
from app.domains.tenant.control_plane_repository import ControlPlaneRepository
from app.domains.tenant.tenant_repository import TenantRepository
from app.domains.tenant.user_repository import UserRepository


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
            date_val=datetime.now(UTC),
            created_by=t_uid,
            class_mappings=[
                {
                    "class_id": class_id,
                    "ticket_price": 7.50,
                }
            ],
        )
        assert event["id"] is not None
        assert len(event["class_mappings"]) == 1
        ecm_id = event["class_mappings"][0]["id"]

        # Enroll student in event class map
        enroll_id = await repo.create_enrollment(
            student_id=student_id, event_class_map_id=ecm_id, state="requested_by_student"
        )
        assert enroll_id is not None

        # Create payment record
        pay_id = await repo.create_payment(enrollment_id=enroll_id, amount=7.50, status="pending")
        assert pay_id is not None

        payment = await repo.get_payment_by_enrollment(enroll_id)
        assert payment is not None
        assert payment["status"] == "pending"
        assert float(payment["amount"]) == 7.50

    # =========================================================================
    # delete_class / delete_level must not destroy enrollment or payment
    # history. event_class_map -> enrollment -> payments are all ON DELETE
    # CASCADE from class, so an unguarded delete silently wipes a class's
    # entire paid-trip financial record.
    # =========================================================================
    async def test_delete_class_blocks_and_preserves_enrollment_and_payment_history(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Year 7")
        t_uid = await user_repo.create_user("teacher@history.com", "hash", "teacher")
        t_id = await repo.create_teacher(t_uid, "Mr. Rivera")
        class_id = await repo.create_class("History", lvl_id, t_id)

        s_uid = await user_repo.create_user("student@history.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Carla Diaz", class_id)

        event = await repo.create_event(
            title="Museum Trip",
            description="History museum",
            address="City Museum",
            school_subsidy=0.00,
            date_val=datetime.now(UTC),
            created_by=t_uid,
            class_mappings=[{"class_id": class_id, "ticket_price": 10.00}],
        )
        ecm_id = event["class_mappings"][0]["id"]
        enroll_id = await repo.create_enrollment(
            student_id=student_id, event_class_map_id=ecm_id, state="approved_by_parent"
        )
        await repo.create_payment(enrollment_id=enroll_id, amount=10.00, status="paid")

        with pytest.raises(ValueError):
            await repo.delete_class(class_id)

        # A blocked attempt must leave everything exactly as it was.
        assert await repo.get_class_by_id(class_id) is not None
        assert await repo.get_payment_by_enrollment(enroll_id) is not None
        student = await repo.get_student_by_id(student_id)
        assert student["class_id"] == class_id

    async def test_delete_class_succeeds_and_unassigns_students_when_no_history(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Year 8")
        class_id = await repo.create_class("Geography", lvl_id, None)
        s_uid = await user_repo.create_user("student@geo.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Deon Marsh", class_id)

        result = await repo.delete_class(class_id)
        assert result is True
        assert await repo.get_class_by_id(class_id) is None

        student = await repo.get_student_by_id(student_id)
        assert student["class_id"] is None

    async def test_delete_class_raises_for_nonexistent_class(self, db_pool: asyncpg.Pool, clean_db):
        repo = TenantRepository(db_pool)
        with pytest.raises(ValueError):
            await repo.delete_class(999999)

    async def test_delete_level_blocks_entirely_if_any_of_its_classes_has_history(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """A level-wide delete must be all-or-nothing: it must not delete the
        clean sections while blocking on the one section with history."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Year 9")
        t_uid = await user_repo.create_user("teacher@level.com", "hash", "teacher")
        t_id = await repo.create_teacher(t_uid, "Ms. Okafor")
        clean_class_id = await repo.create_class("9A", lvl_id, t_id)
        paid_class_id = await repo.create_class("9B", lvl_id, t_id)

        s_uid = await user_repo.create_user("student@level.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Ellis Wong", paid_class_id)

        event = await repo.create_event(
            title="Ski Trip",
            description="Winter trip",
            address="Resort",
            school_subsidy=0.00,
            date_val=datetime.now(UTC),
            created_by=t_uid,
            class_mappings=[{"class_id": paid_class_id, "ticket_price": 50.00}],
        )
        ecm_id = event["class_mappings"][0]["id"]
        enroll_id = await repo.create_enrollment(
            student_id=student_id, event_class_map_id=ecm_id, state="approved_by_parent"
        )
        await repo.create_payment(enrollment_id=enroll_id, amount=50.00, status="paid")

        with pytest.raises(ValueError):
            await repo.delete_level(lvl_id)

        assert await repo.get_class_by_id(clean_class_id) is not None
        assert await repo.get_class_by_id(paid_class_id) is not None
        assert await repo.get_level_by_id(lvl_id) is not None

    async def test_delete_level_raises_for_nonexistent_level(self, db_pool: asyncpg.Pool, clean_db):
        repo = TenantRepository(db_pool)
        with pytest.raises(ValueError):
            await repo.delete_level(999999)

    async def test_get_classes_by_head_teacher_returns_all_classes_deterministically(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: a teacher can legitimately head more than one class (no
        uniqueness constraint on head_teacher_id) -- anything that gates
        access on "is this teacher the head of class X" must see every class
        they head, in a stable order, not a single non-deterministic pick."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Year 7 Multi")
        t_uid = await user_repo.create_user("teacher@multiclass.com", "hash", "teacher")
        t_id = await repo.create_teacher(t_uid, "Ms. Osei")

        class_a = await repo.create_class("7A Multi", lvl_id, t_id)
        class_b = await repo.create_class("7B Multi", lvl_id, t_id)

        classes = await repo.get_classes_by_head_teacher(t_id)
        assert [c["id"] for c in classes] == sorted([class_a, class_b])

        # Stable across repeated calls -- not just "happens to include both
        # once", but the same result every time.
        classes_again = await repo.get_classes_by_head_teacher(t_id)
        assert [c["id"] for c in classes_again] == [c["id"] for c in classes]

    async def test_create_class_rejects_non_positive_capacity(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: the class_capacity_positive CHECK constraint must
        actually reject bad data reaching the repository directly, not just
        the Pydantic layer above it."""
        repo = TenantRepository(db_pool)
        lvl_id = await repo.create_level("Year 8 Capacity")
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await repo.create_class("8A Capacity", lvl_id, capacity=-5)

    # =========================================================================
    # Student placement: students.class_id is the only record of academic
    # placement, mutated in four places with no history -- "who moved from
    # 7A to 7B, and when" was structurally unanswerable. student_class_history
    # now logs every transition; bulk_reassign_students must also stop
    # reporting a fake success count for ids that don't exist.
    # =========================================================================
    async def test_reassign_student_class_raises_for_nonexistent_student(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: reassigning a student that doesn't exist must fail
        loudly -- the prior code ran an UPDATE that matched zero rows and
        still unconditionally returned True."""
        repo = TenantRepository(db_pool)
        with pytest.raises(ValueError):
            await repo.reassign_student_class(999999, None)

    async def test_bulk_reassign_students_reports_real_count_and_skips_missing_ids(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: bulk_reassign_students must report how many students it
        actually touched, not len(student_ids) -- the prior code returned
        the size of the request, so N bogus ids reported "N enrolled" having
        changed nothing."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Year 10 Bulk")
        class_id = await repo.create_class("10A Bulk", lvl_id, None)

        s1_uid = await user_repo.create_user("bulk1@school.com", "hash", "student")
        s1_id = await repo.create_student(s1_uid, "Bulk One", None)
        s2_uid = await user_repo.create_user("bulk2@school.com", "hash", "student")
        s2_id = await repo.create_student(s2_uid, "Bulk Two", None)
        bogus_id = 999999

        result = await repo.bulk_reassign_students([s1_id, s2_id, bogus_id], class_id)
        assert result == {"updated_count": 2, "missing_student_ids": [bogus_id]}

        assert (await repo.get_student_by_id(s1_id))["class_id"] == class_id
        assert (await repo.get_student_by_id(s2_id))["class_id"] == class_id

    async def test_bulk_reassign_students_all_bogus_ids_updates_nothing(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        repo = TenantRepository(db_pool)
        result = await repo.bulk_reassign_students([999997, 999998], None)
        assert result == {"updated_count": 0, "missing_student_ids": [999997, 999998]}

    async def test_class_history_records_creation_reassignment_and_unassignment(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: every class_id transition -- initial placement,
        reassignment, and unassignment -- must be reconstructable from
        student_class_history, with who made each change."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)
        admin_uid = await user_repo.create_user("history_admin@school.com", "hash", "school_admin")

        lvl_id = await repo.create_level("Year 11 History")
        class_a = await repo.create_class("11A History", lvl_id, None)
        class_b = await repo.create_class("11B History", lvl_id, None)

        s_uid = await user_repo.create_user("history_student@school.com", "hash", "student")
        student_id = await repo.create_student(
            s_uid, "History Student", class_a, changed_by=admin_uid
        )

        await repo.reassign_student_class(student_id, class_b, changed_by=admin_uid)
        await repo.reassign_student_class(student_id, None, changed_by=admin_uid)

        history = await repo.get_class_history(student_id)
        assert len(history) == 3

        assert history[0]["old_class_id"] is None
        assert history[0]["new_class_id"] == class_a
        assert history[0]["changed_by"] == admin_uid

        assert history[1]["old_class_id"] == class_a
        assert history[1]["new_class_id"] == class_b

        assert history[2]["old_class_id"] == class_b
        assert history[2]["new_class_id"] is None

        assert history[0]["changed_at"] <= history[1]["changed_at"] <= history[2]["changed_at"]

    async def test_delete_class_records_history_for_unlinked_students(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: a class-delete's forced unassignment is still a
        placement change and must be logged, or a student's history would
        show an unexplained gap instead of "left because the class closed"."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Year 12 History")
        class_id = await repo.create_class("12A History", lvl_id, None)
        s_uid = await user_repo.create_user("delclass_history@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Del Class Student", class_id)

        await repo.delete_class(class_id)

        history = await repo.get_class_history(student_id)
        assert len(history) == 2
        assert history[-1]["old_class_id"] == class_id
        assert history[-1]["new_class_id"] is None

    async def test_bulk_reassign_students_no_op_does_not_spam_history(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: reassigning a student to the class they're already in
        is not a placement change and must not create a phantom history
        entry."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Year 13 Noop")
        class_id = await repo.create_class("13A Noop", lvl_id, None)
        s_uid = await user_repo.create_user("noop_student@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Noop Student", class_id)

        result = await repo.bulk_reassign_students([student_id], class_id)
        assert result == {"updated_count": 1, "missing_student_ids": []}

        history = await repo.get_class_history(student_id)
        assert len(history) == 1


# =============================================================================
# save_academic_structure -- the wizard's bulk save had six distinct merge
# defects: a destructive full-wipe of blackout dates, levels/sections that
# could never be removed once created, ordinal-first level matching that
# re-parented grades on an ordinal swap, name-based section matching that
# duplicated a row on rename (orphaning its roster), a non-deterministic
# default head teacher with a fallback that could FK-violate the whole save,
# and a writer/reader mismatch on which academic_settings row is "current".
# =============================================================================
class TestSaveAcademicStructure:
    def _payload(self, levels=None, blackout_dates=None, start_month=9):
        return {
            "system": "UK",
            "levels": levels or [],
            "calendar": {
                "academic_year": "2026-2027",
                "start_month": start_month,
                "weekend_days": ["Saturday", "Sunday"],
            },
            "blackout_dates": blackout_dates or [],
        }

    async def test_blackout_dates_are_diffed_not_blindly_wiped(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: saving must not DELETE-then-reinsert every blackout
        date on every call -- a date untouched by this save keeps its
        identity (its row survives), and a date genuinely removed from the
        payload is the only thing that disappears."""
        repo = TenantRepository(db_pool)

        await repo.save_academic_structure(
            self._payload(
                blackout_dates=[
                    {"date": "2026-12-25", "title": "Christmas", "tags": []},
                    {"date": "2027-01-01", "title": "New Year", "tags": []},
                ]
            )
        )
        row = await db_pool.fetchrow("SELECT id FROM blackout_dates WHERE date = '2026-12-25'")
        original_id = row["id"]
        assert (await db_pool.fetchval("SELECT COUNT(*) FROM blackout_dates")) == 2

        # Second save: New Year's title is updated, Christmas is untouched,
        # nothing is removed.
        await repo.save_academic_structure(
            self._payload(
                blackout_dates=[
                    {"date": "2026-12-25", "title": "Christmas", "tags": []},
                    {"date": "2027-01-01", "title": "New Year's Day", "tags": ["updated"]},
                ]
            )
        )
        christmas_row = await db_pool.fetchrow(
            "SELECT id FROM blackout_dates WHERE date = '2026-12-25'"
        )
        assert (
            christmas_row["id"] == original_id
        ), "an untouched date must keep the same row, not get deleted and recreated"
        new_year_row = await db_pool.fetchrow(
            "SELECT title, tags FROM blackout_dates WHERE date = '2027-01-01'"
        )
        assert new_year_row["title"] == "New Year's Day"
        assert new_year_row["tags"] == ["updated"]

        # Third save: New Year's is genuinely removed from the payload --
        # this, and only this, must disappear.
        await repo.save_academic_structure(
            self._payload(blackout_dates=[{"date": "2026-12-25", "title": "Christmas", "tags": []}])
        )
        remaining = await db_pool.fetch("SELECT date FROM blackout_dates")
        assert [str(r["date"]) for r in remaining] == ["2026-12-25"]

    async def test_level_ordinal_swap_does_not_reparent_the_wrong_row(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: swapping two grades' ordinals must swap the ordinal
        values on the two correct rows, never rename whichever row happens
        to hold the target ordinal already."""
        repo = TenantRepository(db_pool)

        await repo.save_academic_structure(
            self._payload(
                levels=[
                    {"name": "Grade 2", "ordinal": 2, "is_active": True, "sections": []},
                    {"name": "Grade 3", "ordinal": 3, "is_active": True, "sections": []},
                ]
            )
        )
        structure = await repo.get_academic_structure()
        grade_2 = next(lvl for lvl in structure["levels"] if lvl["name"] == "Grade 2")
        grade_3 = next(lvl for lvl in structure["levels"] if lvl["name"] == "Grade 3")

        # Swap ordinals, identified by their stable level_id -- exactly what
        # the fixed frontend now sends.
        await repo.save_academic_structure(
            self._payload(
                levels=[
                    {
                        "level_id": grade_2["level_id"],
                        "name": "Grade 2",
                        "ordinal": 3,
                        "is_active": True,
                        "sections": [],
                    },
                    {
                        "level_id": grade_3["level_id"],
                        "name": "Grade 3",
                        "ordinal": 2,
                        "is_active": True,
                        "sections": [],
                    },
                ]
            )
        )
        after = await repo.get_academic_structure()
        after_by_id = {lvl["level_id"]: lvl for lvl in after["levels"]}
        assert after_by_id[grade_2["level_id"]]["name"] == "Grade 2"
        assert after_by_id[grade_2["level_id"]]["ordinal"] == 3
        assert after_by_id[grade_3["level_id"]]["name"] == "Grade 3"
        assert after_by_id[grade_3["level_id"]]["ordinal"] == 2

    async def test_section_rename_updates_in_place_and_preserves_roster(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: renaming a section must update the existing row, not
        create a second empty one and orphan the original along with every
        student in it."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        await repo.save_academic_structure(
            self._payload(
                levels=[
                    {
                        "name": "Grade 7",
                        "ordinal": 7,
                        "is_active": True,
                        "sections": [{"name": "7A", "capacity": 25}],
                    },
                ]
            )
        )
        structure = await repo.get_academic_structure()
        grade_7 = next(lvl for lvl in structure["levels"] if lvl["name"] == "Grade 7")
        section = grade_7["sections"][0]

        s_uid = await user_repo.create_user("roster_rename@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Roster Student", section["id"])

        await repo.save_academic_structure(
            self._payload(
                levels=[
                    {
                        "level_id": grade_7["level_id"],
                        "name": "Grade 7",
                        "ordinal": 7,
                        "is_active": True,
                        "sections": [{"id": section["id"], "name": "7A West", "capacity": 25}],
                    },
                ]
            )
        )

        classes = await db_pool.fetch(
            "SELECT id, name FROM class WHERE level_id = $1", grade_7["level_id"]
        )
        assert len(classes) == 1, "renaming must not create a second, duplicate section"
        assert classes[0]["name"] == "7A West"
        assert (
            classes[0]["id"] == section["id"]
        ), "the original row's identity must survive the rename"

        student = await repo.get_student_by_id(student_id)
        assert (
            student["class_id"] == section["id"]
        ), "the student's roster placement must not be split off onto an invisible old row"

    async def test_removed_section_is_deleted_when_no_history_exists(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: dropping a section from its level's list is the
        wizard's only way to represent deleting it -- it must actually be
        removed server-side, not silently kept forever."""
        repo = TenantRepository(db_pool)

        await repo.save_academic_structure(
            self._payload(
                levels=[
                    {
                        "name": "Grade 8",
                        "ordinal": 8,
                        "is_active": True,
                        "sections": [
                            {"name": "8A", "capacity": 25},
                            {"name": "8B", "capacity": 25},
                        ],
                    },
                ]
            )
        )
        structure = await repo.get_academic_structure()
        grade_8 = next(lvl for lvl in structure["levels"] if lvl["name"] == "Grade 8")
        section_a = next(s for s in grade_8["sections"] if s["name"] == "8A")

        await repo.save_academic_structure(
            self._payload(
                levels=[
                    {
                        "level_id": grade_8["level_id"],
                        "name": "Grade 8",
                        "ordinal": 8,
                        "is_active": True,
                        "sections": [{"id": section_a["id"], "name": "8A", "capacity": 25}],
                    },
                ]
            )
        )
        remaining = await db_pool.fetch(
            "SELECT name FROM class WHERE level_id = $1", grade_8["level_id"]
        )
        assert [r["name"] for r in remaining] == ["8A"]

    async def test_removing_a_section_with_enrollment_history_is_blocked(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: the same enrollment/payment-history guard that
        protects the standalone delete endpoints must protect a section
        removed implicitly via this bulk save."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        await repo.save_academic_structure(
            self._payload(
                levels=[
                    {
                        "name": "Grade 9",
                        "ordinal": 9,
                        "is_active": True,
                        "sections": [
                            {"name": "9A", "capacity": 25},
                            {"name": "9B", "capacity": 25},
                        ],
                    },
                ]
            )
        )
        structure = await repo.get_academic_structure()
        grade_9 = next(lvl for lvl in structure["levels"] if lvl["name"] == "Grade 9")
        section_a = next(s for s in grade_9["sections"] if s["name"] == "9A")
        section_b = next(s for s in grade_9["sections"] if s["name"] == "9B")

        t_uid = await user_repo.create_user("teacher_g9@school.com", "hash", "teacher")
        await repo.create_teacher(t_uid, "Ms. Novak")
        s_uid = await user_repo.create_user("student_g9@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "History Student", section_b["id"])

        event = await repo.create_event(
            title="Field Trip",
            description="",
            address=None,
            school_subsidy=0.0,
            date_val=datetime.now(UTC),
            created_by=t_uid,
            class_mappings=[{"class_id": section_b["id"], "ticket_price": 10.0}],
        )
        ecm_id = event["class_mappings"][0]["id"]
        enroll_id = await repo.create_enrollment(
            student_id=student_id, event_class_map_id=ecm_id, state="approved_by_parent"
        )
        await repo.create_payment(enrollment_id=enroll_id, amount=10.0, status="paid")

        with pytest.raises(ValueError):
            await repo.save_academic_structure(
                self._payload(
                    levels=[
                        {
                            "level_id": grade_9["level_id"],
                            "name": "Grade 9",
                            "ordinal": 9,
                            "is_active": True,
                            "sections": [{"id": section_a["id"], "name": "9A", "capacity": 25}],
                        },
                    ]
                )
            )

        # Blocked save must leave everything exactly as it was.
        remaining = await db_pool.fetch(
            "SELECT name FROM class WHERE level_id = $1", grade_9["level_id"]
        )
        assert {r["name"] for r in remaining} == {"9A", "9B"}
        assert await repo.get_payment_by_enrollment(enroll_id) is not None
        student = await repo.get_student_by_id(student_id)
        assert student["class_id"] == section_b["id"]

    async def test_omitted_level_is_deactivated_not_hard_deleted(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: levels.is_active is the level lifecycle mechanism --
        this bulk save must never hard-delete a level just because a given
        call happened not to mention it, since that is indistinguishable
        from a client that simply hasn't loaded it yet."""
        repo = TenantRepository(db_pool)

        await repo.save_academic_structure(
            self._payload(
                levels=[
                    {"name": "Grade 10", "ordinal": 10, "is_active": True, "sections": []},
                    {"name": "Grade 11", "ordinal": 11, "is_active": True, "sections": []},
                ]
            )
        )
        structure = await repo.get_academic_structure()
        grade_11 = next(lvl for lvl in structure["levels"] if lvl["name"] == "Grade 11")

        # Grade 11 explicitly deactivated and still sent (the fixed
        # frontend no longer drops inactive levels from the payload).
        await repo.save_academic_structure(
            self._payload(
                levels=[
                    {"name": "Grade 10", "ordinal": 10, "is_active": True, "sections": []},
                    {
                        "level_id": grade_11["level_id"],
                        "name": "Grade 11",
                        "ordinal": 11,
                        "is_active": False,
                        "sections": [],
                    },
                ]
            )
        )
        after = await repo.get_academic_structure()
        assert any(
            lvl["level_id"] == grade_11["level_id"] and lvl["is_active"] is False
            for lvl in after["levels"]
        ), "the level row must still exist, just marked inactive"

    async def test_default_head_teacher_is_null_not_a_foreign_key_violation(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: with no teachers table row at all, a new section must
        get head_teacher_id = NULL, not abort the whole save by trying to
        use a users.id that head_teacher_id (which REFERENCES teachers(id))
        can't actually accept."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        # A user with role='teacher' but deliberately NO row in `teachers`
        # -- the exact edge case the old `users` fallback could not survive.
        await user_repo.create_user("orphan_teacher@school.com", "hash", "teacher")
        assert (await db_pool.fetchval("SELECT COUNT(*) FROM teachers")) == 0

        await repo.save_academic_structure(
            self._payload(
                levels=[
                    {
                        "name": "Grade 12",
                        "ordinal": 12,
                        "is_active": True,
                        "sections": [{"name": "12A", "capacity": 25}],
                    },
                ]
            )
        )
        row = await db_pool.fetchrow("SELECT head_teacher_id FROM class WHERE name = '12A'")
        assert row["head_teacher_id"] is None

    async def test_writer_and_reader_agree_on_the_current_settings_row(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: if a tenant somehow ends up with more than one
        academic_settings row, the save must update the same row
        get_academic_structure() reads (ORDER BY id DESC), not a different,
        invisible one."""
        repo = TenantRepository(db_pool)

        # Simulate the duplicate-row state a race could produce.
        await db_pool.execute(
            "INSERT INTO academic_settings (system, academic_year, start_month, weekend_days) "
            "VALUES ('UK', '2025-2026', 9, ARRAY['Saturday','Sunday'])"
        )
        await db_pool.execute(
            "INSERT INTO academic_settings (system, academic_year, start_month, weekend_days) "
            "VALUES ('UK', '2025-2026', 9, ARRAY['Saturday','Sunday'])"
        )
        assert (await db_pool.fetchval("SELECT COUNT(*) FROM academic_settings")) == 2

        await repo.save_academic_structure(self._payload(start_month=1))

        structure = await repo.get_academic_structure()
        assert (
            structure["calendar"]["start_month"] == 1
        ), "the reader's row must reflect what was just saved"


# =============================================================================
# Guardians: student_parent_map used to be (student_id, parent_id) and
# nothing else -- no relationship type, no primary-contact flag, no
# approval-authority flag, no timestamps -- so every linked parent was
# treated as equally authoritative, and get_parent_for_student picked
# "the" parent with LIMIT 1 and no ORDER BY (non-deterministic across plan
# changes). Separately, Keycloak SSO parent provisioning wrote to a table
# (`parents`) that doesn't exist in any tenant schema -- only the
# control-plane does -- so it silently resolved there, failed, and got
# swallowed, leaving a `users` row with no matching `parenets` row.
# =============================================================================
class TestGuardianAuthority:
    async def _link_parent(self, repo, user_repo, email, name, student_id, **kwargs):
        p_uid = await user_repo.create_user(email, "hash", "parent")
        await repo.create_parent(p_uid, name)
        await repo.add_student_parent_link(student_id, p_uid, **kwargs)
        return p_uid

    async def test_add_student_parent_link_stores_and_updates_metadata(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: relationship_type/is_primary_contact/can_approve must
        actually persist, and re-linking the same pair must update them
        rather than silently no-op (ON CONFLICT DO NOTHING would make these
        fields write-once)."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Guardian Year")
        class_id = await repo.create_class("Guardian Class", lvl_id, None)
        s_uid = await user_repo.create_user("guardian_student@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Guardian Student", class_id)

        p_uid = await self._link_parent(
            repo,
            user_repo,
            "mother@school.com",
            "Mother",
            student_id,
            relationship_type="mother",
            is_primary_contact=True,
            can_approve=True,
        )
        row = await db_pool.fetchrow(
            "SELECT relationship_type, is_primary_contact, can_approve FROM student_parent_map "
            "WHERE student_id = $1 AND parent_id = $2",
            student_id,
            p_uid,
        )
        assert row["relationship_type"] == "mother"
        assert row["is_primary_contact"] is True
        assert row["can_approve"] is True

        # Re-link the same pair with different metadata -- must update, not no-op.
        await repo.add_student_parent_link(
            student_id,
            p_uid,
            relationship_type="mother",
            is_primary_contact=True,
            can_approve=False,
        )
        row_after = await db_pool.fetchrow(
            "SELECT can_approve FROM student_parent_map WHERE student_id = $1 AND parent_id = $2",
            student_id,
            p_uid,
        )
        assert row_after["can_approve"] is False

    async def test_can_parent_approve_for_student_respects_can_approve_flag(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Approve Year")
        class_id = await repo.create_class("Approve Class", lvl_id, None)
        s_uid = await user_repo.create_user("approve_student@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Approve Student", class_id)

        custodial = await self._link_parent(
            repo,
            user_repo,
            "custodial@school.com",
            "Custodial",
            student_id,
            can_approve=True,
        )
        non_custodial = await self._link_parent(
            repo,
            user_repo,
            "noncustodial@school.com",
            "Non-Custodial",
            student_id,
            can_approve=False,
        )
        stranger_uid = await user_repo.create_user("stranger@school.com", "hash", "parent")

        assert await repo.can_parent_approve_for_student(student_id, custodial) is True
        assert await repo.can_parent_approve_for_student(student_id, non_custodial) is False
        assert (
            await repo.can_parent_approve_for_student(student_id, stranger_uid) is False
        ), "no link at all must not be treated as approval authority"

        # is_student_linked_to_parent is the bare membership check -- it must
        # stay True for the non-custodial parent (they ARE linked, just not
        # authorized to approve), not silently start tracking can_approve too.
        assert await repo.is_student_linked_to_parent(student_id, non_custodial) is True

    async def test_get_parent_for_student_prefers_primary_contact_deterministically(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: with more than one linked parent, the single-slot
        profile lookup must deterministically prefer the flagged primary
        contact, not whichever row LIMIT 1 happened to return that day."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("Primary Year")
        class_id = await repo.create_class("Primary Class", lvl_id, None)
        s_uid = await user_repo.create_user("primary_student@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "Primary Student", class_id)

        # Linked first (would win under the old LIMIT-1-no-ORDER-BY code on
        # most query plans) but NOT the primary contact.
        await self._link_parent(
            repo,
            user_repo,
            "first_linked@school.com",
            "First Linked",
            student_id,
            is_primary_contact=False,
        )
        # Linked second, but flagged primary -- must win regardless of link order.
        primary_uid = await self._link_parent(
            repo,
            user_repo,
            "primary_contact@school.com",
            "Primary Contact",
            student_id,
            is_primary_contact=True,
        )

        for _ in range(3):
            result = await repo.get_parent_for_student(student_id)
            assert result["id"] == primary_uid
            assert result["name"] == "Primary Contact"

    async def test_get_parent_for_student_is_deterministic_with_no_primary_contact(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: even with no primary contact flagged, repeated calls
        must return the same parent every time."""
        repo = TenantRepository(db_pool)
        user_repo = UserRepository(db_pool)

        lvl_id = await repo.create_level("NoPrimary Year")
        class_id = await repo.create_class("NoPrimary Class", lvl_id, None)
        s_uid = await user_repo.create_user("noprimary_student@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "NoPrimary Student", class_id)

        await self._link_parent(repo, user_repo, "parent_x@school.com", "Parent X", student_id)
        await self._link_parent(repo, user_repo, "parent_y@school.com", "Parent Y", student_id)

        results = [await repo.get_parent_for_student(student_id) for _ in range(5)]
        assert (
            len({r["id"] for r in results}) == 1
        ), "the same parent must be returned on every call, not whichever the planner favors"

    async def test_sso_parent_provisioning_writes_to_parenets_not_parents(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: the tenant-schema table is `parenets` (typo preserved
        from the original schema) -- `parents` only exists in the
        control-plane schema, which search_path still resolves to, so
        writing there used to fail (wrong id type, no `name` column), get
        swallowed by the caller's try/except, and leave a `users` row with
        no matching `parenets` row: invisible to GET /parents, and a
        foreign key violation waiting for the first link-parent call.

        This exercises the exact statement sequence
        app/core/dependencies.py's Keycloak JIT-provisioning block now runs,
        directly against the tenant pool (bypassing Keycloak token
        verification, which is out of scope for a repository-level test)."""
        async with db_pool.acquire() as conn, conn.transaction():
            local_id = await conn.fetchval(
                "INSERT INTO users (email, role, password_hash) VALUES ($1, 'parent', 'keycloak_managed') RETURNING id",
                "sso_parent@school.com",
            )
            await conn.execute(
                "INSERT INTO parenets (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                local_id,
                "Sso_parent",
            )

        repo = TenantRepository(db_pool)
        parents = await repo.get_all_parents()
        assert any(
            p["id"] == local_id for p in parents
        ), "the JIT-provisioned parent must be visible to GET /parents"

        # link-parent must not 500 with a foreign key violation now that the
        # parent has a real parenets row to reference.
        user_repo = UserRepository(db_pool)
        lvl_id = await repo.create_level("SSO Year")
        class_id = await repo.create_class("SSO Class", lvl_id, None)
        s_uid = await user_repo.create_user("sso_child@school.com", "hash", "student")
        student_id = await repo.create_student(s_uid, "SSO Child", class_id)

        await repo.add_student_parent_link(student_id, local_id)
        assert await repo.is_student_linked_to_parent(student_id, local_id) is True


# =============================================================================
# Cross-cutting: get_academic_structure's per-level class query (N+1 on a
# pool capped at 5 connections), create_level silently discarding a
# duplicate-name request's fields, and school_contact deletion never
# checking whether the row existed.
# =============================================================================
class TestCrossCuttingFixes:
    async def test_get_academic_structure_groups_sections_under_the_correct_level(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: replacing the one-query-per-level loop with a single
        grouped query must not cross-contaminate sections between levels --
        each level's sections list must contain exactly its own classes."""
        repo = TenantRepository(db_pool)

        lvl_a = await repo.create_level("Grouping Grade A")
        lvl_b = await repo.create_level("Grouping Grade B")
        await repo.create_class("A-1", lvl_a, None)
        await repo.create_class("A-2", lvl_a, None)
        await repo.create_class("B-1", lvl_b, None)

        structure = await repo.get_academic_structure()
        by_id = {lvl["level_id"]: lvl for lvl in structure["levels"]}

        assert {s["name"] for s in by_id[lvl_a]["sections"]} == {"A-1", "A-2"}
        assert {s["name"] for s in by_id[lvl_b]["sections"]} == {"B-1"}

    async def test_get_academic_structure_handles_a_level_with_no_sections(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """The grouped query must not drop levels that have zero classes --
        a plain JOIN (rather than a dict lookup with a default) would."""
        repo = TenantRepository(db_pool)
        lvl_empty = await repo.create_level("Empty Grade")

        structure = await repo.get_academic_structure()
        by_id = {lvl["level_id"]: lvl for lvl in structure["levels"]}
        assert by_id[lvl_empty]["sections"] == []

    async def test_delete_school_contact_raises_for_nonexistent_contact(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        """invariant: deleting a contact id that doesn't exist must fail
        loudly -- the prior code ran an unconditional DELETE that matched
        zero rows and returned success regardless."""
        from app.domains.school.repository import SchoolRepository

        repo = SchoolRepository(db_pool)
        with pytest.raises(ValueError):
            await repo.delete_contact(999999)

    async def test_delete_school_contact_removes_an_existing_one(
        self, db_pool: asyncpg.Pool, clean_db
    ):
        from app.domains.school.repository import SchoolRepository

        repo = SchoolRepository(db_pool)
        created = await repo.create_contact(
            {
                "role_title": "Principal",
                "name": "Dr. Test",
                "phone": "+15551234",
                "email": None,
                "is_emergency_contact": False,
                "escalation_order": None,
                "visible_to": ["staff"],
            }
        )
        await repo.delete_contact(created["id"])
        remaining = await repo.list_contacts()
        assert all(c["id"] != created["id"] for c in remaining)
