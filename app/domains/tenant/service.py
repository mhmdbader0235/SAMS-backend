"""
TenantService — coordinates business logic for tenant-specific operations.

Implements PII encryption/decryption, masking, and audit logging.
Does not import FastAPI or asyncpg directly.
"""

import contextlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.fernet import Fernet

from app.core import authz
from app.core.config import ENCRYPTION_KEY
from app.core.database import get_control_plane_pool, get_db_pool
from app.core.keycloak_admin import sync_user_to_keycloak
from app.domains.audit.service import AuditService
from app.domains.auth.service import AuthService
from app.domains.tenant.control_plane_repository import ControlPlaneRepository
from app.domains.tenant.tenant_repository import UNSET, TenantRepository, parse_id
from app.domains.tenant.user_repository import UserRepository

_fernet = Fernet(ENCRYPTION_KEY.encode())


class _AuditActor:
    """Minimal actor shape AuditService.record needs (.id/.role/.email),
    built from whatever a caller already threads through as `user_role`/
    `changed_by` -- most call sites here only have those two, not a full
    CurrentUser, so `email` is None and the resulting audit row's
    actor_email_hmac is NULL. Documented limitation, not an oversight."""

    def __init__(self, user_id, role: str | list[str] | None):
        self.id = user_id
        if isinstance(role, list | tuple | set):
            self.role = next(iter(role), "unknown")
        else:
            self.role = role or "unknown"
        self.email = None


# =============================================================================
# Helper Utilities
# =============================================================================
async def _upsert_user_tenant_mapping(email: str, tenant_id: str, role: str) -> None:
    try:
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)
        await cp_repo.upsert_user_tenant_map(email, tenant_id, role)
    except Exception as e:
        print(f"[WARNING] Failed to upsert user_tenant_map for {email}: {e}")


def _encrypt(val: str) -> str:
    return _fernet.encrypt(val.encode()).decode()


def _decrypt(val: str) -> str:
    return _fernet.decrypt(val.encode()).decode()


def _mask_field(val: str, field_name: str) -> str:
    """Mask sensitive fields for unauthorized roles."""
    if not val:
        return ""
    if field_name == "national_id":
        return f"********{val[-4:]}" if len(val) >= 4 else "********"
    elif field_name == "medical_conditions":
        return f"***{val[-2:]}" if len(val) >= 2 else "********"
    elif field_name == "emergency_contact":
        return f"******{val[-4:]}" if len(val) >= 4 else "********"
    return "********"


class TenantService:
    # =========================================================================
    # Levels
    # =========================================================================
    @staticmethod
    async def create_level(
        tenant_id: str,
        name: str,
        user_role: str | list[str],
        isced_level: int | None = None,
        age_band_min: int | None = None,
        age_band_max: int | None = None,
        ordinal: int | None = None,
        is_active: bool = True,
    ) -> int:
        # Grade levels are Academic Administration Hub territory -- a bare
        # "teacher" or "manager" role must never create/restructure the grade
        # ladder. level:create is a real, cataloged permission (dependencies.py
        # / store.js COMPOSITE_ROLE_PERMISSIONS) an admin can grant a specific
        # user via Manage Permissions, without changing their base role. This
        # mirrors update_level/delete_level below, which already excluded
        # teacher/manager; create_level was the one inconsistent sibling.
        if not authz.require(user_role, {"school_admin", "level:create"}):
            raise PermissionError("Only school admins can create levels")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        # A name collision is idempotent-by-name, not a no-op: apply whatever
        # fields the caller actually specified (None means "not specified",
        # same convention update_level already uses) to the existing row,
        # instead of silently discarding them and handing back stale values
        # under a 200 as if the request had actually been applied.
        # upsert_level_by_name is this same branch, extracted to
        # tenant_repository.py so the bulk import path (TenantService.
        # commit_structure_import) shares this exact logic instead of
        # duplicating it.
        level_id, _action = await repo.upsert_level_by_name(
            name,
            isced_level=isced_level,
            age_band_min=age_band_min,
            age_band_max=age_band_max,
            ordinal=ordinal,
            is_active=is_active,
        )
        return level_id

    @staticmethod
    async def get_all_levels(tenant_id: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_all_levels()

    # =========================================================================
    # Teachers
    # =========================================================================
    @staticmethod
    async def create_teacher(
        tenant_id: str,
        email: str,
        password: str,
        name: str,
        user_role: str | list[str],
    ) -> int:
        # A bare "teacher" role must NOT be able to register other teachers --
        # only school_admin, or a teacher explicitly granted the "teacher:create"
        # permission via Manage Permissions, may do this. See
        # docs/05-traceability/02-invariants.md.
        if not authz.require(user_role, {"school_admin", "teacher:create"}):
            raise PermissionError("Only staff can register teachers")

        pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(pool)
        tenant_repo = TenantRepository(pool)

        password_hash = await AuthService.hash_password_async(password)
        user_id = await user_repo.create_user(email, password_hash, "teacher")
        sync_user_to_keycloak(email, password, "teacher", tenant_id, first_name=name)
        await _upsert_user_tenant_mapping(email, tenant_id, "teacher")

        return await tenant_repo.create_teacher(
            user_id=user_id,
            name=name,
        )

    @staticmethod
    async def create_staff_user(
        tenant_id: str,
        email: str,
        password: str,
        role: str,
        user_role: str | list[str],
    ) -> int:
        if role not in ("manager", "school_admin"):
            raise ValueError("Invalid staff role")

        # The permission escape hatch depends on WHICH role is being created --
        # this function is shared by create_manager and create_school_admin
        # (students/router.py), and their router-level checks are already
        # asymmetric for exactly this reason. Both used to reach this single
        # `{"school_admin"}` check regardless of target role, silently
        # discarding the router's own "manager creation may be delegated via
        # user:create" decision -- a caller who passed the router gate with a
        # granted user:create permission still 403'd here.
        if role == "manager":  # noqa: SIM108 -- kept as if/else, see comment below
            allowed = {"school_admin", "user:create"}
        else:
            # role == "school_admin": deliberately NO user:create escape
            # hatch. Minting a peer-level admin account must stay strictly
            # admin-only -- a delegated grant letting a manager create a
            # school_admin would be a real escalation path, not a
            # convenience. Mirrors create_school_admin's router-level check.
            allowed = {"school_admin"}

        if not authz.require(user_role, allowed):
            raise PermissionError("Only school admins can register staff users")

        pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(pool)

        if await user_repo.get_user_by_email(email):
            raise ValueError("Email already registered")

        password_hash = await AuthService.hash_password_async(password)
        user_id = await user_repo.create_user(email, password_hash, role)
        sync_user_to_keycloak(email, password, role, tenant_id)
        await _upsert_user_tenant_mapping(email, tenant_id, role)
        return user_id

    @staticmethod
    async def get_all_teachers(tenant_id: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_all_teachers()

    # =========================================================================
    # Parents
    # =========================================================================
    @staticmethod
    async def get_all_parents(tenant_id: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_all_parents()

    # =========================================================================
    # Students
    # =========================================================================
    @staticmethod
    async def create_student(
        tenant_id: str,
        email: str,
        password: str,
        name: str,
        class_id: int,
        gender: str | None,
        birth_data: str | None,
        user_role: str | list[str],
        changed_by=None,
    ) -> int:
        # Same rule as create_teacher: a bare "teacher" role does not get to
        # register student accounts. Only school_admin, or a teacher explicitly
        # granted "student:create" via Manage Permissions, may do this.
        if not authz.require(user_role, {"school_admin", "student:create"}):
            raise PermissionError("Only staff can register students")

        pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(pool)
        tenant_repo = TenantRepository(pool)

        # Create tenant user with 'student' role
        password_hash = await AuthService.hash_password_async(password)
        user_id = await user_repo.create_user(email, password_hash, "student")
        sync_user_to_keycloak(email, password, "student", tenant_id, first_name=name)
        await _upsert_user_tenant_mapping(email, tenant_id, "student")

        # Create student profile linking to user and class
        return await tenant_repo.create_student(
            user_id=user_id,
            name=name,
            class_id=class_id,
            gender=gender,
            birth_data=birth_data,
            changed_by=changed_by,
        )

    @staticmethod
    async def get_class_history(
        tenant_id: str, student_id: int, user_role: str | list[str] = ""
    ) -> list[dict]:
        if not authz.require(
            user_role, {"school_admin", "super_admin", "admin", "teacher", "manager"}
        ):
            raise PermissionError("Only staff can view a student's placement history")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_class_history(student_id)

    @staticmethod
    async def get_all_students(tenant_id: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_all_students()

    @staticmethod
    async def get_student_by_id(tenant_id: str, student_id) -> dict | None:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_student_by_id(student_id)

    @staticmethod
    async def link_student_parent(
        tenant_id: str,
        student_id,
        parent_id,
        user_role: str | list[str],
        user_id=None,
        relationship_type: str | None = None,
        is_primary_contact: bool = False,
        can_approve: bool = True,
    ) -> None:
        # user:link is a real, cataloged permission (COMPOSITE_ROLE_PERMISSIONS
        # in dependencies.py / store.js) that an admin can grant a specific
        # user via Manage Permissions -- it was previously listed there but
        # never actually checked anywhere, so granting it did nothing. It
        # grants the same unrestricted linking school_admin has (the
        # own-class-only scoping just below only applies to the literal
        # "teacher" role, not to this permission).
        if not authz.require(user_role, {"school_admin", "teacher", "user:link"}):
            raise PermissionError("Only school admins and teachers can link students and parents")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        if authz.require(user_role, "teacher") and not authz.require(user_role, "school_admin"):
            # Teacher must be the head teacher of the student's class
            student = await repo.get_student_by_id(student_id)
            if not student:
                raise ValueError("Student not found")
            teacher_classes = await repo.get_classes_by_head_teacher(user_id)
            if not any(c["id"] == student["class_id"] for c in teacher_classes):
                raise PermissionError("You can only link parents to students in your own class")

        await repo.add_student_parent_link(
            student_id,
            parent_id,
            relationship_type=relationship_type,
            is_primary_contact=is_primary_contact,
            can_approve=can_approve,
        )

    @staticmethod
    async def get_linked_students_for_parent(tenant_id: str, parent_id) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_linked_students_for_parent(parent_id)

    # =========================================================================
    # Classes
    # =========================================================================
    @staticmethod
    async def create_class(
        tenant_id: str,
        name: str,
        level_id: int,
        head_teacher_id=None,
        capacity: int = 25,
        is_active: bool = True,
        user_role: str | list[str] = "",
    ) -> int:
        # Creating a class section is Academic Administration Hub territory --
        # "add a class to a grade" must never be reachable by a bare "teacher"
        # or "manager" role. super_admin/admin are redundant with
        # authz.require's own super_admin bypass above, kept here for
        # readability; class:create is the real, cataloged permission
        # (replacing the earlier placeholder "class:write", which had no
        # catalog entry) an admin can grant a specific user via Manage
        # Permissions, same shape as teacher:create/student:create elsewhere
        # in this file.
        if not authz.require(user_role, {"school_admin", "super_admin", "admin", "class:create"}):
            raise PermissionError("Only school admins can create classes")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        level = await repo.get_level_by_id(level_id)
        if not level:
            raise ValueError(f"Level {level_id} not found")
        if not level["is_active"]:
            raise ValueError(f"Cannot add a section to a deactivated level: {level['name']}")

        # upsert_class_by_name_and_level: a name collision now UPDATEs the
        # existing row's capacity/head_teacher/is_active instead of silently
        # keeping stale values under a 200 (the old behavior). head_teacher_id
        # not being specified (None, this method's own "not given" default)
        # means UNSET -- don't touch whatever head teacher a colliding row
        # already has -- never an explicit clear; clearing a head teacher is
        # exclusively update_class's job via its own UNSET/None distinction.
        class_id, _action = await repo.upsert_class_by_name_and_level(
            name,
            level_id,
            head_teacher_id=head_teacher_id if head_teacher_id is not None else UNSET,
            capacity=capacity,
            is_active=is_active,
        )
        return class_id

    @staticmethod
    async def get_all_classes(tenant_id: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_all_classes()

    @staticmethod
    async def get_classes_by_head_teacher(tenant_id: str, teacher_id) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_classes_by_head_teacher(teacher_id)

    @staticmethod
    async def update_class(
        tenant_id: str,
        class_id: int,
        name: str | None = None,
        level_id: int | None = None,
        head_teacher_id=UNSET,
        capacity: int | None = None,
        is_active: bool | None = None,
        user_role: str | list[str] = "",
    ) -> dict:
        # This service method had NO permission check of its own -- the only
        # gate was an inline has_any_role() at the router (students/router.py),
        # which also (incorrectly) allowed a bare "teacher". Every sibling
        # mutation here (create_class, delete_class, update_level, delete_level)
        # checks inside the service; this one didn't, so any future caller that
        # invoked it directly would have bypassed authorization entirely.
        # class:update is the real, cataloged permission escape hatch,
        # mirroring the router-level check in students/router.py.
        if not authz.require(user_role, {"school_admin", "super_admin", "admin", "class:update"}):
            raise PermissionError("Only school admins can update classes")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        if level_id is not None:
            level = await repo.get_level_by_id(level_id)
            if not level:
                raise ValueError(f"Level {level_id} not found")
            if not level["is_active"]:
                raise ValueError(f"Cannot move a section to a deactivated level: {level['name']}")

        return await repo.update_class(
            class_id,
            name=name,
            level_id=level_id,
            head_teacher_id=head_teacher_id,
            capacity=capacity,
            is_active=is_active,
        )

    @staticmethod
    async def delete_class(
        tenant_id: str, class_id: int, user_role: str | list[str] = "", changed_by=None
    ) -> bool:
        if not authz.require(user_role, {"school_admin", "super_admin", "admin"}):
            raise PermissionError("Only school admins can delete classes")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        actor = _AuditActor(changed_by, user_role)

        async def _audit_hook(conn) -> None:
            await AuditService.record(
                conn,
                actor=actor,
                action="class.delete",
                entity_type="class",
                entity_id=class_id,
                outcome="allow",
            )

        return await repo.delete_class(class_id, changed_by=changed_by, audit_hook=_audit_hook)

    @staticmethod
    async def delete_level(
        tenant_id: str, level_id: int, user_role: str | list[str] = "", changed_by=None
    ) -> bool:
        if not authz.require(user_role, {"school_admin", "super_admin", "admin"}):
            raise PermissionError("Only school admins can delete levels")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        actor = _AuditActor(changed_by, user_role)

        async def _audit_hook(conn) -> None:
            await AuditService.record(
                conn,
                actor=actor,
                action="level.delete",
                entity_type="level",
                entity_id=level_id,
                outcome="allow",
            )

        return await repo.delete_level(level_id, changed_by=changed_by, audit_hook=_audit_hook)

    @staticmethod
    async def update_level(
        tenant_id: str,
        level_id: int,
        name: str | None = None,
        isced_level: int | None = None,
        age_band_min: int | None = None,
        age_band_max: int | None = None,
        ordinal: int | None = None,
        is_active: bool | None = None,
        user_role: str | list[str] = "",
    ) -> dict | None:
        if not authz.require(user_role, {"school_admin", "super_admin", "admin"}):
            raise PermissionError("Only school admins can update levels")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.update_level(
            level_id=level_id,
            name=name,
            isced_level=isced_level,
            age_band_min=age_band_min,
            age_band_max=age_band_max,
            ordinal=ordinal,
            is_active=is_active,
        )

    @staticmethod
    async def get_students_for_class(tenant_id: str, class_id: int) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_students_for_class(class_id)

    @staticmethod
    async def reassign_student_class(
        tenant_id: str,
        student_id,
        new_class_id: int | None,
        user_role: str | list[str] = "",
        changed_by=None,
    ) -> bool:
        # Single-student reassignment is the Student Placement tab of the
        # Academic Administration Hub -- apiReassignStudentClass is called from
        # nowhere else in the frontend, so tightening this has no effect
        # outside that page. A bare "teacher" is deliberately excluded.
        if not authz.require(user_role, {"school_admin", "super_admin", "admin", "student:manage"}):
            raise PermissionError("Only school admins can reassign student classes")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.reassign_student_class(student_id, new_class_id, changed_by=changed_by)

    @staticmethod
    async def bulk_reassign_students(
        tenant_id: str,
        student_ids: list[int],
        new_class_id: int | None,
        user_role: str | list[str] = "",
        changed_by=None,
    ) -> dict:
        # Same as reassign_student_class above -- bulk reassignment is Student
        # Placement tab territory only, "teacher" excluded on purpose.
        if not authz.require(user_role, {"school_admin", "super_admin", "admin", "student:manage"}):
            raise PermissionError("Only school admins can reassign student classes")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.bulk_reassign_students(student_ids, new_class_id, changed_by=changed_by)

    # =========================================================================
    # Events & Targets
    # =========================================================================
    @staticmethod
    async def create_event(
        tenant_id: str,
        title: str,
        description: str,
        address: str | None,
        school_subsidy: float,
        date_val: datetime,
        created_by,
        class_mappings: list[dict],
        user_role: str | list[str],
    ) -> dict:
        if not authz.require(user_role, {"school_admin", "teacher"}):
            raise PermissionError("Only staff can create events")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        event = await repo.create_event(
            title=title,
            description=description,
            address=address,
            school_subsidy=school_subsidy,
            date_val=date_val,
            created_by=created_by,
            class_mappings=class_mappings,
        )

        # Notify students in mapped classes
        user_ids_to_notify = set()
        for mapping in class_mappings:
            class_id = mapping["class_id"]
            # Find students in class
            students = await repo.get_all_students()
            for s in students:
                if s["class_id"] == class_id:
                    user_ids_to_notify.add(s["id"])

        for u_id in user_ids_to_notify:
            await repo.create_notification(event["id"], u_id)

        return event

    @staticmethod
    async def clone_event(
        tenant_id: str,
        event_id: int,
        created_by_user_id: int,
        user_role: str | list[str],
        new_title: str | None = None,
        new_date: datetime | None = None,
    ) -> dict:
        if not authz.require(user_role, {"school_admin", "teacher"}):
            raise PermissionError("Only staff can clone events")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        original = await repo.get_event_by_id(event_id)
        if not original:
            raise ValueError("Event not found")

        title = new_title or f"Template - {original['title']}"
        date_val = new_date or (datetime.utcnow() + timedelta(days=30))

        class_mappings = [
            {
                "class_id": m["class_id"],
                "ticket_price": (
                    float(m["ticket_price"]) if m.get("ticket_price") is not None else 0.0
                ),
                "budgets": [],
            }
            for m in original.get("class_mappings", [])
        ]

        # 1. Create the new draft event
        new_event = await TenantService.create_event(
            tenant_id=tenant_id,
            title=title,
            description=original.get("description", ""),
            address=original.get("address"),
            school_subsidy=float(original.get("school_subsidy", 0.0)),
            date_val=date_val,
            created_by=created_by_user_id,
            class_mappings=class_mappings,
            user_role=user_role,
        )

        # 2. Duplicate requested resources
        original_resources = await repo.get_resources_for_event(event_id)
        if original_resources:
            resources_list = [
                {
                    "resource_type_id": r["resource_type_id"],
                    "description": r.get("description"),
                    "quantity": r.get("quantity", 1),
                }
                for r in original_resources
            ]
            await TenantService.add_resources_to_event(
                tenant_id=tenant_id,
                event_id=new_event["id"],
                resources_list=resources_list,
                added_by_user_id=created_by_user_id,
            )

        return await repo.get_event_by_id(new_event["id"])

    @staticmethod
    async def update_event(
        tenant_id: str,
        event_id: int,
        title: str,
        description: str,
        address: str | None,
        school_subsidy: float,
        date_val: datetime,
        user_role: str | list[str],
    ) -> dict:
        if not authz.require(user_role, {"school_admin", "teacher", "event_teacher", "manager"}):
            raise PermissionError("Only staff can update events")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        event = await repo.update_event(
            event_id=event_id,
            title=title,
            description=description,
            address=address,
            school_subsidy=school_subsidy,
            date_val=date_val,
        )
        if not event:
            raise ValueError("Event not found")
        return event

    @staticmethod
    async def update_event_full(
        tenant_id: str,
        event_id: int,
        title: str,
        description: str,
        address: str | None,
        school_subsidy: float,
        date_val: datetime,
        class_mappings: list[dict],
        user_role: str | list[str],
        user_id: int,
    ) -> dict:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        roles = {user_role} if isinstance(user_role, str) else set(user_role)
        if "school_admin" in roles or "event_teacher" in roles or "manager" in roles:
            pass
        elif "teacher" in roles:
            # Verify teacher is a head teacher of a class. A teacher can
            # legitimately head more than one class (no uniqueness
            # constraint), so this must check membership across all of
            # them, not assume a single "the" class.
            teacher_classes = await repo.get_classes_by_head_teacher(user_id)
            if not teacher_classes:
                raise PermissionError("Teacher is not a head teacher of any class")
            teacher_class_ids = {parse_id(c["id"]) for c in teacher_classes}

            # Load the existing event to verify if any of this teacher's
            # classes is targeted
            existing_event = await repo.get_event_by_id(event_id)
            if not existing_event:
                raise ValueError("Event not found")

            target_class_ids = {int(m["class_id"]) for m in existing_event["class_mappings"]}
            if not (teacher_class_ids & target_class_ids):
                raise PermissionError("Access denied. Event is not mapped to your class.")

            # Filter class mappings to only allow updating this teacher's own
            # classes' mappings (all of them, not just one).
            filtered_mappings = []
            for mapping in class_mappings:
                if parse_id(mapping["class_id"]) in teacher_class_ids:
                    filtered_mappings.append(mapping)

            class_mappings = filtered_mappings

        else:
            raise PermissionError("Only staff can update events")

        event = await repo.update_event_full(
            event_id=event_id,
            title=title,
            description=description,
            address=address,
            school_subsidy=school_subsidy,
            date_val=date_val,
            class_mappings=class_mappings,
        )
        if not event:
            raise ValueError("Event not found")
        return event

    @staticmethod
    async def get_events_for_user(
        tenant_id: str, user_id, user_role: str | list[str]
    ) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        class Actor:
            def __init__(self, id, role, roles=None):
                self.id = id
                self.role = role
                self.roles = roles or [role]

        actor = Actor(
            user_id,
            user_role[0] if isinstance(user_role, list) and user_role else (user_role or "student"),
            user_role if isinstance(user_role, list) else [user_role or "student"],
        )

        roles = {user_role} if isinstance(user_role, str) else set(user_role)

        if roles.intersection({"super_admin", "school_admin", "manager", "event_teacher"}):
            events = await repo.get_all_events()
            return [ev for ev in events if TenantService.check_event_permission(actor, ev, "read")]

        filtered_events = []
        already_added = set()

        if "teacher" in roles:
            teacher_classes = await repo.get_classes_by_head_teacher(actor.id)
            teacher_class_ids = {c["id"] for c in teacher_classes}
            events = await repo.get_all_events()
            for ev in events:
                if ev["id"] not in already_added and (
                    TenantService.check_event_permission(actor, ev, "read")
                    or any(
                        m.get("class_id") in teacher_class_ids for m in ev.get("class_mappings", [])
                    )
                ):
                    filtered_events.append(ev)
                    already_added.add(ev["id"])

        if "student" in roles:
            events = await repo.get_events_for_student(user_id)
            for ev in events:
                if ev["id"] not in already_added and TenantService.check_event_permission(
                    actor, ev, "read"
                ):
                    filtered_events.append(ev)
                    already_added.add(ev["id"])

        if "parent" in roles:
            children = await repo.get_linked_students_for_parent(user_id)
            events_dict = {}
            for child in children:
                child_events = await repo.get_events_for_student(child["id"])
                for ev in child_events:
                    if TenantService.check_event_permission(actor, ev, "read"):
                        ev_id = ev["id"]
                        if ev_id not in events_dict:
                            events_dict[ev_id] = dict(ev)
                            events_dict[ev_id]["class_mappings"] = list(ev["class_mappings"])
                        else:
                            existing_mappings = events_dict[ev_id]["class_mappings"]
                            existing_ids = {m["id"] for m in existing_mappings}
                            for m in ev["class_mappings"]:
                                if m["id"] not in existing_ids:
                                    existing_mappings.append(m)
            for ev in events_dict.values():
                if ev["id"] not in already_added:
                    filtered_events.append(ev)
                    already_added.add(ev["id"])

        if roles.intersection(
            {
                "super_admin",
                "school_admin",
                "manager",
                "event_teacher",
                "teacher",
                "student",
                "parent",
            }
        ):
            return (
                sorted(filtered_events, key=lambda e: e["date"])
                if filtered_events
                else filtered_events
            )

        return []

    # =========================================================================
    # Enrollments & Payments
    # =========================================================================
    @staticmethod
    async def enroll_student(
        tenant_id: str,
        student_id,
        event_class_map_id: int,
        state: str,
        teacher_id=None,
        parent_id=None,
    ) -> int:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        # 1. Fetch class mapping details
        class_map = await repo.get_class_map_by_id(event_class_map_id)
        if not class_map:
            raise ValueError("Event class mapping not found")

        # 1b. An event's audience is only real once it's published -- a
        # draft/proposed/approved trip hasn't been announced to any class
        # yet, so there is nothing a student, parent, or teacher could be
        # legitimately requesting/approving enrollment into.
        event = await repo.get_event_by_id(class_map["event_id"])
        if not event or event.get("status") != "published":
            raise ValueError("Enrollment is only allowed for published events")

        # 2. Fetch student details
        student = await repo.get_student_by_id(student_id)
        if not student:
            raise ValueError("Student not found")

        # 3. Check if student is already enrolled in this event (across any class mappings)
        existing_event_enrollments = await repo.get_enrollments_for_student_and_event(
            student_id, class_map["event_id"]
        )
        if existing_event_enrollments:
            # If already enrolled in this exact class map, return it
            for e in existing_event_enrollments:
                if e["event_class_map_id"] == event_class_map_id:
                    return e["id"]
            raise ValueError("Student is already enrolled in this event")

        # 4. Check student class matches class mapping class
        if student["class_id"] != class_map["class_id"]:
            raise ValueError("Student is not in the class mapped to this event")

        enrollment_id = await repo.create_enrollment(
            student_id=student_id,
            event_class_map_id=event_class_map_id,
            state=state,
            teacher_id=teacher_id,
            parent_id=parent_id,
        )

        # Proactively check event subsidy details to verify if parent payment is required
        # If ticket price > 0, we can create a pending payment
        enrollment_details = await repo.get_enrollment_by_id(enrollment_id)
        if enrollment_details and enrollment_details.get("ticket_price", 0) > 0:
            await repo.create_payment(
                enrollment_id=enrollment_id,
                amount=float(enrollment_details["ticket_price"]),
                status="pending",
            )

        return enrollment_id

    @staticmethod
    async def update_enrollment_state(
        tenant_id: str,
        enrollment_id: int,
        state: str,
        teacher_id=None,
        parent_id=None,
    ) -> bool:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.update_enrollment_state(enrollment_id, state, teacher_id, parent_id)

    @staticmethod
    async def get_enrollments_for_user(
        tenant_id: str, user_id, user_role: str | list[str]
    ) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        roles = {user_role} if isinstance(user_role, str) else set(user_role)

        results = []
        enroll_ids = set()

        # school_admin (and super_admin acting in-tenant) leads no single
        # class, so routing them through get_enrollments_for_teacher() the
        # same way as a teacher always returned an empty list -- the whole
        # school's roster, not "my class's roster".
        if roles.intersection({"school_admin", "super_admin"}):
            enrolls = await repo.get_all_enrollments()
            for e in enrolls:
                if e["id"] not in enroll_ids:
                    results.append(e)
                    enroll_ids.add(e["id"])
        elif "teacher" in roles:
            enrolls = await repo.get_enrollments_for_teacher(user_id)
            for e in enrolls:
                if e["id"] not in enroll_ids:
                    results.append(e)
                    enroll_ids.add(e["id"])
        if "parent" in roles:
            enrolls = await repo.get_enrollments_for_parent(user_id)
            for e in enrolls:
                if e["id"] not in enroll_ids:
                    results.append(e)
                    enroll_ids.add(e["id"])
        if "student" in roles:
            enrolls = await repo.get_enrollments_for_student(user_id)
            for e in enrolls:
                if e["id"] not in enroll_ids:
                    results.append(e)
                    enroll_ids.add(e["id"])
        return results

    @staticmethod
    async def cancel_enrollment(
        tenant_id: str,
        enrollment_id: int,
        user_id: int,
        user_role: str | list[str],
    ) -> None:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        enrollment = await repo.get_enrollment_by_id(enrollment_id)
        if not enrollment:
            raise ValueError("Enrollment not found")

        student_id = enrollment["student_id"]
        roles = {user_role} if isinstance(user_role, str) else set(user_role)

        if "school_admin" in roles or "teacher" in roles:
            pass
        elif "parent" in roles:
            # Cancelling is the same authority as approving -- a linked
            # contact without can_approve must not be able to pull a trip a
            # custodial parent already committed to, any more than they
            # could have approved it in the first place.
            can_approve = await repo.can_parent_approve_for_student(student_id, user_id)
            if not can_approve:
                raise PermissionError(
                    "Only a parent authorized to approve for this student can cancel their enrollments"
                )
        elif "student" in roles:
            if int(student_id) != int(user_id):
                raise PermissionError("Students can only cancel their own enrollments")
        else:
            raise PermissionError("Unauthorized role to cancel enrollment")

        payment = await repo.get_payment_by_enrollment(enrollment_id)
        if payment and payment["status"] == "paid":
            raise ValueError("Cannot cancel enrollment after payment has been completed")

        class_map = await repo.get_class_map_by_id(enrollment["event_class_map_id"])
        if class_map:
            class_info = await repo.get_class_by_id(class_map["class_id"])
            if class_info:
                head_teacher_id = class_info["head_teacher_id"]
                student_name = enrollment.get("student_name") or f"Student #{student_id}"
                event_title = enrollment.get("event_title") or "the event"

                title_override = f"❌ Cancelled: {student_name} un-enrolled from '{event_title}'"

                await repo.create_notification(
                    event_id=class_map["event_id"],
                    recipient_user_id=head_teacher_id,
                    title_override=title_override,
                )

        await repo.delete_enrollment(enrollment_id)

    # =========================================================================
    # Payments
    # =========================================================================
    @staticmethod
    async def get_payment_for_enrollment(tenant_id: str, enrollment_id: int) -> dict | None:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_payment_by_enrollment(enrollment_id)

    @staticmethod
    async def pay_enrollment(tenant_id: str, enrollment_id: int) -> bool:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        payment = await repo.get_payment_by_enrollment(enrollment_id)
        if payment:
            # Update status to paid
            await repo.pool.execute(
                "UPDATE payments SET status = 'paid' WHERE enrollment_id = $1",
                parse_id(enrollment_id),
            )
            return True
        return False

    # =========================================================================
    # Feedback
    # =========================================================================
    @staticmethod
    async def create_event_feedback(
        tenant_id: str, event_id: int, user_id, rating: int, comments: str | None
    ) -> int:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.create_event_feedback(event_id, user_id, rating, comments)

    @staticmethod
    async def get_feedback_for_event(tenant_id: str, event_id: int) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_feedback_for_event(event_id)

    # =========================================================================
    # Student Health & Records (PII)
    # =========================================================================
    @staticmethod
    async def create_or_update_health_record(
        tenant_id: str,
        student_id,
        national_id: str,
        medical_conditions: str,
        emergency_contact: str,
        requesting_user_id,
        requesting_user_role: str | list[str],
    ) -> UUID:
        if not authz.require(requesting_user_role, {"school_admin", "teacher"}):
            raise PermissionError("Only staff can manage health records")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        nat_enc = _encrypt(national_id)
        med_enc = _encrypt(medical_conditions)
        emg_enc = _encrypt(emergency_contact)

        actor = _AuditActor(requesting_user_id, requesting_user_role)

        async def _audit_hook(conn) -> None:
            await AuditService.record(
                conn,
                actor=actor,
                action="student.health.write",
                entity_type="student",
                entity_id=student_id,
                outcome="allow",
                metadata={
                    "fields_written": ["national_id", "medical_conditions", "emergency_contact"]
                },
            )

        return await repo.create_or_update_student_health(
            student_id=student_id,
            national_id_encrypted=nat_enc,
            medical_conditions_encrypted=med_enc,
            emergency_contact_encrypted=emg_enc,
            audit_hook=_audit_hook,
        )

    @staticmethod
    async def get_health_record(
        tenant_id: str,
        student_id,
        requesting_user_id,
        requesting_user_role: str | list[str],
        elevated_clearance: bool = False,
    ) -> dict | None:
        # Authorization is gated at the router via require_permission("health:view")
        # (OPA) — this is the sole caller of this method, so no check is repeated here.
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        record = await repo.get_student_health_by_student_id(student_id)
        if not record:
            return None

        nat_dec = _decrypt(record["national_id_encrypted"])
        med_dec = _decrypt(record["medical_conditions_encrypted"])
        emg_dec = _decrypt(record["emergency_contact_encrypted"])

        # Non-fatal: an audit outage must not 500 a health-record view. Uses
        # its own connection (not the read's, which already finished) since
        # there is nothing to roll back a read against.
        try:
            async with (await get_db_pool(tenant_id)).acquire() as audit_conn:
                await AuditService.record(
                    audit_conn,
                    actor=_AuditActor(requesting_user_id, requesting_user_role),
                    action="student.health.read",
                    entity_type="student",
                    entity_id=student_id,
                    outcome="allow",
                )
        except Exception:
            pass

        if requesting_user_role == "school_admin" and elevated_clearance:
            return {
                "id": record["id"],
                "student_id": record["student_id"],
                "national_id": nat_dec,
                "medical_conditions": med_dec,
                "emergency_contact": emg_dec,
                "is_masked": False,
            }
        else:
            return {
                "id": record["id"],
                "student_id": record["student_id"],
                "national_id": _mask_field(nat_dec, "national_id"),
                "medical_conditions": _mask_field(med_dec, "medical_conditions"),
                "emergency_contact": _mask_field(emg_dec, "emergency_contact"),
                "is_masked": True,
            }

    # =========================================================================
    # Notifications
    # =========================================================================
    @staticmethod
    async def get_notifications_for_user(tenant_id: str, user_id, user_role: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_notifications_for_user(user_id)

    @staticmethod
    async def save_academic_structure(
        tenant_id: str, payload: dict, user_role: str | list[str], changed_by=None
    ) -> None:
        if not authz.require(
            user_role,
            {
                "school_admin",
                "super_admin",
                "admin",
                "level:manage",
                "level:create",
                "school:write",
            },
        ):
            raise PermissionError("Insufficient permissions to manage structure.")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        # Curriculum system is locked permanently once the school activates
        # (see domains/school/) — grades and class sections stay fully
        # editable, only the UK/International/Custom choice itself is frozen.
        from app.domains.school.repository import SchoolRepository

        school_profile = await SchoolRepository(pool).get_profile_row()
        if school_profile and school_profile.get("curriculum_locked_at"):
            current_structure = await repo.get_academic_structure()
            incoming_system = payload.get("system")
            if incoming_system and incoming_system != current_structure.get("system"):
                raise PermissionError(
                    "The curriculum system is locked after school activation and cannot be changed. "
                    "Grades and class sections can still be edited."
                )

        await repo.save_academic_structure(payload, changed_by=changed_by)

    # Import writes both levels and classes in one call, so it's gated like
    # save_academic_structure above (also a bulk academic-structure write) --
    # an OR of the relevant permissions, not requiring all of them at once,
    # consistent with authz.require's existing semantics. Adds
    # class:create, which save_academic_structure's own set is missing
    # despite writing classes too -- not perpetuating that gap here.
    _IMPORT_PERMISSIONS = {
        "school_admin",
        "super_admin",
        "admin",
        "level:create",
        "level:manage",
        "class:create",
        "school:write",
    }

    @staticmethod
    async def preview_structure_import(
        tenant_id: str,
        filename: str,
        content_type: str | None,
        raw_bytes: bytes,
        user_role: str | list[str],
    ) -> dict:
        """Validate an uploaded grades+classes file and report what would
        happen, without writing anything. Stateless by design: the caller
        re-uploads the same file for commit_structure_import below rather
        than this holding any server-side session -- nothing like that
        exists elsewhere in this codebase, and re-parsing a capped-size file
        twice is cheap."""
        if not authz.require(user_role, TenantService._IMPORT_PERMISSIONS):
            raise PermissionError("Insufficient permissions to import academic structure.")

        from app.domains.students.import_parser import detect_file_kind, parse_import_file

        kind = detect_file_kind(filename, content_type)
        rows = parse_import_file(raw_bytes, kind)

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        result = await repo.preview_import_rows(rows)
        result["filename"] = filename
        return result

    @staticmethod
    async def commit_structure_import(
        tenant_id: str,
        filename: str,
        content_type: str | None,
        raw_bytes: bytes,
        user_role: str | list[str],
    ) -> dict:
        """Apply an uploaded grades+classes file. Partial success: rows that
        validate are applied, rows that don't are skipped and reported --
        not an all-or-nothing transaction, matching bulk_reassign_students'
        existing found/missing convention elsewhere in this file. Additive
        only: never deletes a class or grade the file doesn't mention --
        deliberately does NOT reuse save_academic_structure, which deletes
        any class section not present in the payload it's given."""
        if not authz.require(user_role, TenantService._IMPORT_PERMISSIONS):
            raise PermissionError("Insufficient permissions to import academic structure.")

        from app.domains.students.import_parser import detect_file_kind, parse_import_file

        kind = detect_file_kind(filename, content_type)
        rows = parse_import_file(raw_bytes, kind)

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        result = await repo.import_academic_rows(rows)
        result["filename"] = filename
        return result

    @staticmethod
    async def get_academic_structure(tenant_id: str, user_role: str | list[str]) -> dict:
        if not authz.require(
            user_role,
            {
                "school_admin",
                "super_admin",
                "admin",
                "manager",
                "teacher",
                "level:read",
                "level:manage",
                "school:read",
            },
        ):
            raise PermissionError("Insufficient permissions to view structure.")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_academic_structure()

    @staticmethod
    async def mark_notification_read(tenant_id: str, notif_id: UUID) -> bool:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.mark_notification_read(notif_id)

    @staticmethod
    async def check_and_send_reminders() -> None:
        pass

    # =========================================================================
    # Resources Service Layer (workflow & resource schema)
    # =========================================================================
    @staticmethod
    async def create_resource_type(
        tenant_id: str, name: str, category: str, is_custom: bool = False, created_by_user_id=None
    ) -> int:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.create_resource_type(name, category, is_custom, created_by_user_id)

    @staticmethod
    async def add_resources_to_event(
        tenant_id: str, event_id: int, resources_list: list[dict], added_by_user_id: int
    ) -> None:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        async with repo.pool.acquire() as conn, conn.transaction():
            # Re-check status is draft or resource_planning
            event = await repo.get_event_by_id(event_id)
            if not event or event.get("status", "draft") not in ("draft", "resource_planning"):
                raise ValueError(
                    "Resources can only be modified on draft or resource planning events"
                )

            # Delete existing resources for event
            await repo.delete_resources_for_event(event_id)

            # Insert new resources
            for r in resources_list:
                await repo.create_resource(
                    event_id=event_id,
                    resource_type_id=r["resource_type_id"],
                    description=r.get("description"),
                    quantity=r["quantity"],
                    added_by_user_id=added_by_user_id,
                )

    @staticmethod
    async def set_resource_cost(
        tenant_id: str, resource_id: int, unit_price: float, currency: str, set_by_user_id: int
    ) -> int:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")

        # Allow pricing for planning/draft/proposed/approved events
        event = await repo.get_event_by_id(resource["event_id"])
        if not event or event.get("status", "draft") in ("published", "cancelled"):
            raise ValueError("Pricing cannot be updated for published or cancelled events")

        quantity = resource["quantity"]
        total_cost = float(unit_price) * int(quantity)

        return await repo.set_resource_cost(
            resource_id=resource_id,
            unit_price=unit_price,
            total_cost=total_cost,
            currency=currency,
            set_by_user_id=set_by_user_id,
        )

    @staticmethod
    async def get_resource_summary(tenant_id: str, event_id: int) -> dict:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        resources = await repo.get_resources_for_event(event_id)

        from app.domains.school.repository import SchoolRepository

        school_profile = await SchoolRepository(pool).get_profile_row()
        # The summary reports one currency for the whole event: the school's
        # own. A resource_cost row's own `currency` column is per-line
        # bookkeeping (whatever was set when that line was priced) and must
        # never leak into this summary-level value -- doing so used to make
        # the reported currency whichever resource happened to be priced
        # last, not the school's actual currency.
        currency = (school_profile or {}).get("currency")

        lines = []
        cost_sum = 0.0

        for r in resources:
            cost_info = await repo.get_resource_cost_by_resource_id(r["id"])
            if cost_info:
                unit_price = float(cost_info["unit_price"])
                total_cost = float(cost_info["total_cost"])
                set_by_user_id = cost_info["set_by_user_id"]
            else:
                unit_price = 0.0
                total_cost = 0.0
                set_by_user_id = None

            lines.append(
                {
                    "id": r["id"],
                    "resource_type_id": r["resource_type_id"],
                    "resource_type_name": r["resource_type_name"],
                    "resource_type_category": r["resource_type_category"],
                    "description": r["description"],
                    "quantity": r["quantity"],
                    "added_by_user_id": r["added_by_user_id"],
                    "updated_by_user_id": r["updated_by_user_id"],
                    "unit_price": unit_price,
                    "total_cost": total_cost,
                    "set_by_user_id": set_by_user_id,
                }
            )
            cost_sum += total_cost

        return {
            "event_id": event_id,
            "resources": lines,
            "total_cost": cost_sum,
            "currency": currency,
        }

    @staticmethod
    async def get_predicted_attendance(tenant_id: str, class_ids: list[int]) -> int:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        total_students = await repo.get_student_count_for_classes(class_ids)
        return int(round(0.8 * total_students))

    # =========================================================================
    # Permissions and State Machine (workflow & resource schema)
    # =========================================================================
    @staticmethod
    def check_event_permission(user, event: dict, action: str) -> bool:
        user_roles = getattr(user, "roles", None) or [getattr(user, "role", "student")]
        is_owner = int(parse_id(event.get("created_by") or 0)) == int(
            parse_id(getattr(user, "id", -1) or -1)
        )
        status = event.get("status") or "draft"

        # Super admin and school admin override
        if any(r in user_roles for r in ("super_admin", "school_admin", "admin", "*")):
            return True

        if action == "read":
            if status == "draft":
                mapped_teacher_ids = [
                    parse_id(m.get("head_teacher_id"))
                    for m in (event.get("class_mappings") or [])
                    if m.get("head_teacher_id") is not None
                ]
                return bool(is_owner or parse_id(getattr(user, "id", None)) in mapped_teacher_ids)
            for role in user_roles:
                if role in ("parent", "student") and status == "published":
                    return True
                if role in ("teacher", "event_teacher") and status in (
                    "published",
                    "approved",
                    "proposed",
                    "pricing_review",
                    "final_review",
                    "ready_to_publish",
                ):
                    return True
                if role == "manager" and status != "draft":
                    return True
                if role in ("school_admin", "super_admin", "admin"):
                    return True
            return bool("event:read" in user_roles and status != "draft")
        elif action == "edit_draft":
            if status != "draft":
                return False
            for role in user_roles:
                if role in (
                    "teacher",
                    "event_teacher",
                    "school_admin",
                    "super_admin",
                    "admin",
                ) and (is_owner or role in ("school_admin", "super_admin", "admin")):
                    return True
            return bool("event:edit" in user_roles and is_owner)
        elif action in ("manager_decision", "approve", "review"):
            if (
                "event:review" in user_roles
                or any(r in user_roles for r in ("manager", "school_admin", "super_admin", "admin"))
            ) and status == "proposed":
                return True
        elif action in ("publish", "teacher_publish"):
            if (
                "event:publish" in user_roles
                or (any(r in user_roles for r in ("teacher", "event_teacher")) and is_owner)
                or any(r in user_roles for r in ("school_admin", "super_admin", "admin"))
            ) and status in ("approved", "ready_to_publish"):
                return True

        return False

    @staticmethod
    async def transition_event(
        tenant_id: str,
        event_id: int,
        action: str,
        actor,
        reason: str | None = None,
    ) -> dict:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        event = await repo.get_event_by_id(event_id)
        if not event:
            raise ValueError("Event not found")

        current_status = event.get("status") or "draft"

        TRANSITIONS = {
            # (current_status, action) -> (next_status, required_role)
            # Step 1: Teacher submits draft for Manager approval (draft -> proposed)
            ("draft", "submit_for_approval"): ("proposed", "teacher"),
            ("draft", "propose"): ("proposed", "teacher"),
            ("draft", "submit_to_manager"): ("proposed", "teacher"),
            ("draft", "submit_to_event_teacher"): ("proposed", "teacher"),
            # Step 2: Manager accepts or rejects the proposal (proposed -> approved | draft)
            # NOTE: there is deliberately NO proposed -> published shortcut. Publishing
            # must go through 'approved' so that published_at is stamped and the
            # student/parent notification fan-out actually runs (both live in the
            # approved -> published branch below).
            ("proposed", "manager_approve"): ("approved", "manager"),
            ("proposed", "approve"): ("approved", "manager"),
            ("proposed", "manager_reject"): ("draft", "manager"),
            ("proposed", "reject"): ("draft", "manager"),
            # Step 3: Teacher publishes approved event to students & parents (approved -> published)
            # Manager retains a publish override on an already-approved event.
            ("approved", "teacher_publish"): ("published", "teacher"),
            ("approved", "publish"): ("published", "teacher"),
            ("approved", "submit"): ("published", "teacher"),
            ("approved", "manager_publish"): ("published", "manager"),
        }

        key = (current_status, action)
        if key not in TRANSITIONS:
            raise ValueError(f"Action '{action}' is not allowed in status '{current_status}'")

        next_status, required_role = TRANSITIONS[key]

        # The granular permission that authorizes each action on its own, for an
        # actor who does not hold the composite role the transition names. This
        # is how a non-teacher who has been granted event:submit (a student, say)
        # submits their own draft. It does not widen who may act on whose event:
        # the creator-only precondition below still applies to every submit, and
        # the manager/publish actions keep their own status guards.
        ACTION_PERMISSIONS = {
            "submit_for_approval": ("event:submit", "event:propose"),
            "propose": ("event:submit", "event:propose"),
            "submit_to_manager": ("event:submit", "event:propose"),
            "submit_to_event_teacher": ("event:submit", "event:propose"),
            "manager_approve": ("event:review",),
            "approve": ("event:review",),
            "manager_reject": ("event:review",),
            "reject": ("event:review",),
            "teacher_publish": ("event:publish",),
            "publish": ("event:publish",),
            "submit": ("event:publish",),
            "manager_publish": ("event:publish",),
        }

        # Verify role (allow school_admin & super_admin override)
        actor_roles = getattr(actor, "roles", None) or [getattr(actor, "role", "student")]
        allowed = {"school_admin", "super_admin", required_role}
        if required_role == "teacher":
            allowed.add("event_teacher")
        allowed.update(ACTION_PERMISSIONS.get(action, ()))
        if not any(r in actor_roles for r in allowed):
            raise PermissionError(
                f"Role '{actor.role}' is not authorized to perform action '{action}'"
            )

        # Verify preconditions
        if action in ("submit_for_approval", "propose", "submit_to_event_teacher"):
            if int(parse_id(event["created_by"])) != int(parse_id(actor.id)) and not any(
                r in actor_roles for r in ("school_admin", "super_admin")
            ):
                raise PermissionError("Only the event creator can submit it for approval")

            mappings = (
                await repo.get_event_class_mappings(event_id)
                if hasattr(repo, "get_event_class_mappings")
                else None
            )
            if not mappings:
                mappings = event.get("class_mappings") or []
            if not mappings:
                raise ValueError("At least one class must be selected before submitting")

        elif action in ("manager_reject", "reject"):
            if not reason or not reason.strip():
                raise ValueError(f"A non-empty reason is required for action '{action}'")

        # Apply updates and side effects
        update_fields = {"status": next_status}
        now_time = datetime.now(UTC)

        if action in (
            "submit_for_approval",
            "propose",
            "submit_to_manager",
            "submit_to_event_teacher",
        ):
            update_fields["submitted_at"] = now_time
            update_fields["rejection_reason"] = None
            # Calculate predicted attendance before submitting
            mappings = event.get("class_mappings") or []
            class_ids = [m["class_id"] for m in mappings]
            if class_ids:
                update_fields["predicted_attendance"] = (
                    await TenantService.get_predicted_attendance(tenant_id, class_ids)
                )

            # Notify managers
            managers = await repo.get_all_managers()
            for m in managers:
                await repo.create_notification(
                    event_id=event_id,
                    recipient_user_id=m["id"],
                    title_override=f"New event proposal: '{event['title']}' submitted for approval",
                )

        elif action in ("manager_approve", "approve"):
            update_fields["manager_approved_at"] = now_time
            update_fields["manager_reviewer_id"] = actor.id
            update_fields["rejection_reason"] = None
            # Notify teacher (event creator) that event has been approved and is ready to publish
            await repo.create_notification(
                event_id=event_id,
                recipient_user_id=event["created_by"],
                title_override=f"Event proposal '{event['title']}' has been approved by Manager! You can now publish it.",
            )

        elif action in ("manager_reject", "reject"):
            update_fields["rejection_reason"] = reason.strip() if reason else "No reason provided"
            # Notify teacher owner with reason
            await repo.create_notification(
                event_id=event_id,
                recipient_user_id=event["created_by"],
                title_override=f"Event '{event['title']}' rejected by manager. Reason: {reason}",
            )

        elif action in ("teacher_publish", "publish") and current_status == "approved":
            update_fields["published_at"] = now_time
            # Notify all students in the mapped classes + their linked parents
            mappings = event.get("class_mappings") or []
            class_ids = [parse_id(m["class_id"]) for m in mappings]
            if class_ids:
                students = await repo.pool.fetch(
                    "SELECT s.id AS student_id FROM students s WHERE s.class_id = ANY($1)",
                    class_ids,
                )
                for s in students:
                    # Notify student
                    await repo.create_notification(
                        event_id=event_id,
                        recipient_user_id=s["student_id"],
                        title_override=f"New school trip published: '{event['title']}' – enroll now!",
                    )
                    # Notify linked parents (via student_parent_map)
                    parent_rows = await repo.pool.fetch(
                        "SELECT parent_id FROM student_parent_map WHERE student_id = $1",
                        s["student_id"],
                    )
                    for p in parent_rows:
                        await repo.create_notification(
                            event_id=event_id,
                            recipient_user_id=p["parent_id"],
                            title_override=f"Your child's school trip '{event['title']}' is now open for enrollment!",
                        )

        elif action == "manager_publish":
            update_fields["published_at"] = now_time
            # Notify parents & students of targeted classes
            mappings = event.get("class_mappings") or []
            class_ids = [m["class_id"] for m in mappings]
            if class_ids:
                students = await repo.pool.fetch(
                    "SELECT id FROM students WHERE class_id = ANY($1)",
                    [parse_id(cid) for cid in class_ids],
                )
                for s in students:
                    await repo.create_notification(
                        event_id=event_id,
                        recipient_user_id=s["id"],
                        title_override=f"New Event Published: '{event['title']}'",
                    )
                    parents = await repo.pool.fetch(
                        "SELECT parent_id FROM student_parent_map WHERE student_id = $1", s["id"]
                    )
                    for p in parents:
                        await repo.create_notification(
                            event_id=event_id,
                            recipient_user_id=p["parent_id"],
                            title_override=f"New Child Event: '{event['title']}' has been published!",
                        )

        # Persist event status updates
        await repo.pool.execute(
            """
            UPDATE event
            SET status = $1,
                predicted_attendance = COALESCE($2, predicted_attendance),
                manager_reviewer_id = COALESCE($3, manager_reviewer_id),
                finance_reviewer_id = COALESCE($4, finance_reviewer_id),
                total_cost = COALESCE($5, total_cost),
                submitted_at = COALESCE($6, submitted_at),
                manager_approved_at = COALESCE($7, manager_approved_at),
                finance_priced_at = COALESCE($8, finance_priced_at),
                published_at = COALESCE($9, published_at),
                rejection_reason = $10
            WHERE id = $11
            """,
            update_fields.get("status"),
            update_fields.get("predicted_attendance"),
            (
                parse_id(update_fields["manager_reviewer_id"])
                if "manager_reviewer_id" in update_fields
                else None
            ),
            (
                parse_id(update_fields["finance_reviewer_id"])
                if "finance_reviewer_id" in update_fields
                else None
            ),
            update_fields.get("total_cost"),
            update_fields.get("submitted_at"),
            update_fields.get("manager_approved_at"),
            update_fields.get("finance_priced_at"),
            update_fields.get("published_at"),
            update_fields.get("rejection_reason"),
            parse_id(event_id),
        )

        return await repo.get_event_by_id(event_id)

    # =========================================================================
    # User Roles & Dynamic Permissions Matrix
    # =========================================================================
    @staticmethod
    async def get_tenant_users_permissions(
        tenant_id: str, user_role: str | list[str]
    ) -> list[dict]:
        """Fetch all users in the tenant with their assigned roles and permissions (admin only)."""
        if not authz.require(user_role, {"school_admin", "super_admin", "admin"}):
            raise PermissionError(
                "Only school administrators can access the user permissions matrix"
            )
        pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(pool)
        return await user_repo.get_all_tenant_users()

    @staticmethod
    async def update_tenant_user_permissions(
        tenant_id: str,
        user_id: int | str,
        primary_role: str,
        roles: list[str],
        permissions: list[str],
        user_role: str | list[str],
        requesting_user_id=None,
    ) -> dict:
        """Update a tenant user's primary role, multiple composite roles, and custom permissions."""
        if not authz.require(user_role, {"school_admin", "super_admin", "admin", "user:invite"}):
            raise PermissionError(
                "Only school administrators can update user roles and permissions"
            )
        if (primary_role == "super_admin" or "super_admin" in (roles or [])) and not authz.require(
            user_role, {"super_admin"}
        ):
            raise PermissionError("Only a super_admin can grant the super_admin role")
        pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(pool)

        # Snapshot the pre-update roles so a demotion away from super_admin
        # can be detected below -- update_user_roles_and_permissions is a
        # full overwrite and returns only the NEW row, so this is the only
        # place that still knows what the user was before this call.
        existing = await user_repo.get_user_by_id(user_id)
        was_super_admin = bool(existing) and (
            existing.get("role") == "super_admin" or "super_admin" in (existing.get("roles") or [])
        )

        updated = await user_repo.update_user_roles_and_permissions(
            user_id=user_id,
            primary_role=primary_role,
            roles=roles,
            permissions=permissions,
        )
        if not updated:
            raise ValueError("User not found in tenant")

        user_email = updated.get("email")
        target_id = updated.get("id")

        # Demoting a super_admin through this screen must revoke control-plane
        # authority too -- public.super_admins is checked BEFORE any tenant
        # table on login (AuthService.login_user / get_current_user), so
        # leaving that row behind after clearing the tenant-local role would
        # let the user keep logging in as super_admin regardless of what this
        # update just set. Mirrors the same cleanup delete_tenant_user does
        # when a super_admin is deleted outright.
        is_still_super_admin = primary_role == "super_admin" or "super_admin" in (roles or [])
        if was_super_admin and not is_still_super_admin and user_email:
            try:
                cp_pool = await get_control_plane_pool()
                cp_repo = ControlPlaneRepository(cp_pool)
                await cp_repo.remove_super_admin(user_email)
            except Exception as _e:
                print(f"[update_tenant_user_permissions] Warning super_admins removal: {_e}")

        # 1. Update user_tenant_map in control plane
        if user_email:
            await _upsert_user_tenant_mapping(user_email, tenant_id, primary_role)

            # 2. If promoted to super_admin, persist in control-plane super_admins table
            if primary_role == "super_admin":
                try:
                    cp_pool = await get_control_plane_pool()
                    async with cp_pool.acquire() as conn_cp:
                        await conn_cp.execute(
                            "INSERT INTO super_admins (email, password_hash) VALUES ($1, 'managed') ON CONFLICT DO NOTHING",
                            user_email,
                        )
                except Exception as _e:
                    print(f"[update_tenant_user_permissions] Warning super_admins insert: {_e}")

            # 3. Ensure role-specific profile records in tenant DB
            try:
                async with pool.acquire() as conn_t:
                    user_display_name = user_email.split("@")[0].replace(".", " ").title()
                    if primary_role in (
                        "teacher",
                        "event_teacher",
                        "school_admin",
                        "manager",
                        "admin",
                    ):
                        await conn_t.execute(
                            "INSERT INTO teachers (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                            target_id,
                            user_display_name,
                        )
                    elif primary_role == "student":
                        c_id = await conn_t.fetchval("SELECT id FROM class LIMIT 1")
                        if c_id is None:
                            l_id = await conn_t.fetchval("SELECT level_id FROM levels LIMIT 1")
                            if l_id is None:
                                l_id = await conn_t.fetchval(
                                    "INSERT INTO levels (name) VALUES ('Grade 1') RETURNING level_id"
                                )
                            t_id = await conn_t.fetchval("SELECT id FROM teachers LIMIT 1")
                            if t_id is None:
                                t_u = await conn_t.fetchval(
                                    "INSERT INTO users (email, role, password_hash) VALUES ($1, 'teacher', 'managed') RETURNING id",
                                    f"head_teacher_{tenant_id}@school.com",
                                )
                                t_id = await conn_t.fetchval(
                                    "INSERT INTO teachers (id, name) VALUES ($1, 'Head Teacher') RETURNING id",
                                    t_u,
                                )
                            c_id = await conn_t.fetchval(
                                "INSERT INTO class (name, level_id, head_teacher_id) VALUES ('Default Class', $1, $2) RETURNING id",
                                l_id,
                                t_id,
                            )
                        await conn_t.execute(
                            "INSERT INTO students (id, name, class_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                            target_id,
                            user_display_name,
                            c_id,
                        )
                    elif primary_role == "parent":
                        with contextlib.suppress(Exception):
                            await conn_t.execute(
                                "INSERT INTO parenets (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                                target_id,
                                user_display_name,
                            )
            except Exception as _e:
                print(f"[update_tenant_user_permissions] Warning profile records sync: {_e}")

            # 4. Synchronize role with Keycloak identity provider
            try:
                from app.core.keycloak_admin import update_user_role_in_keycloak

                update_user_role_in_keycloak(user_email, primary_role, tenant_id)
            except Exception as _e:
                print(
                    f"[update_tenant_user_permissions] Warning Keycloak update for {user_email}: {_e}"
                )

        try:
            async with (await get_db_pool(tenant_id)).acquire() as audit_conn:
                await AuditService.record(
                    audit_conn,
                    actor=_AuditActor(requesting_user_id, user_role),
                    action="user.permissions.update",
                    entity_type="user",
                    entity_id=user_id,
                    outcome="allow",
                    changed_fields=["role", "roles", "permissions"],
                    metadata={
                        "new_primary_role": primary_role,
                        "role_count": len(roles or []),
                        "permission_count": len(permissions or []),
                    },
                )
        except Exception:
            pass
        return updated

    @staticmethod
    async def delete_tenant_user(
        tenant_id: str,
        target_user_id: int | str,
        requesting_user_id,
        user_role: str | list[str],
    ) -> dict:
        """Hard-delete a user from a tenant's users table.

        school_admin may delete any user in their OWN tenant except one whose
        role is super_admin. super_admin may delete anyone, in any tenant
        they're scoped to (see require_tenant_live / the X-Tenant-ID guard,
        which is itself restricted to super_admin). Deleting your own account
        through this endpoint is always blocked.
        """
        roles = (
            set(user_role)
            if isinstance(user_role, list | tuple | set)
            else ({user_role} if user_role else set())
        )
        is_super = "super_admin" in roles
        is_school_admin = bool(roles.intersection({"school_admin", "admin"}))

        if not (is_super or is_school_admin):
            raise PermissionError("Only school_admin or super_admin can delete users")

        pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(pool)

        target = await user_repo.get_user_by_id(target_user_id)
        if not target:
            raise ValueError("User not found")

        if str(target.get("id")) == str(requesting_user_id):
            raise PermissionError("You cannot delete your own account")

        target_roles = set(target.get("roles") or [])
        if target.get("role"):
            target_roles.add(target["role"])

        if not is_super and "super_admin" in target_roles:
            raise PermissionError("school_admin cannot delete a super_admin account")

        deleted = await user_repo.delete_user(target_user_id)
        if not deleted:
            raise ValueError("User not found")

        # Best-effort cleanup so the account can't silently reappear via
        # Keycloak SSO's JIT re-provisioning or a stale tenant mapping.
        user_email = deleted.get("email")
        if user_email:
            try:
                cp_pool = await get_control_plane_pool()
                cp_repo = ControlPlaneRepository(cp_pool)
                await cp_repo.remove_user_tenant_map(user_email, tenant_id)
            except Exception as _e:
                print(
                    f"[delete_tenant_user] Warning user_tenant_map cleanup for {user_email}: {_e}"
                )
            # A super_admin target also has a row in the control-plane
            # `super_admins` table -- AuthService.login_user and
            # get_current_user's super_admin check consult THAT table first,
            # before any tenant `users` row is read. Deleting only the
            # tenant-local mirror above left a "deleted" super_admin fully
            # able to log in as super_admin exactly as before.
            if "super_admin" in target_roles:
                try:
                    cp_pool = await get_control_plane_pool()
                    cp_repo = ControlPlaneRepository(cp_pool)
                    removed = await cp_repo.remove_super_admin(user_email)
                    if not removed:
                        print(
                            f"[delete_tenant_user] {user_email} had role super_admin "
                            f"but no matching public.super_admins row was found to remove"
                        )
                except Exception as _e:
                    print(
                        f"[delete_tenant_user] Warning super_admins cleanup for {user_email}: {_e}"
                    )
            try:
                from app.core.keycloak_admin import delete_user_from_keycloak

                if not delete_user_from_keycloak(user_email):
                    print(
                        f"[delete_tenant_user] Keycloak cleanup did not remove {user_email} "
                        f"from Keycloak — see keycloak_admin warnings above for the reason"
                    )
            except Exception as _e:
                print(f"[delete_tenant_user] Warning Keycloak cleanup for {user_email}: {_e}")

        try:
            async with (await get_db_pool(tenant_id)).acquire() as audit_conn:
                await AuditService.record(
                    audit_conn,
                    actor=_AuditActor(requesting_user_id, user_role),
                    action="user.delete",
                    entity_type="user",
                    entity_id=target_user_id,
                    outcome="allow",
                    metadata={
                        "deleted_role": deleted.get("role"),
                        "was_super_admin": "super_admin" in target_roles,
                    },
                )
        except Exception:
            pass
        return deleted

    # =========================================================================
    # Academic Years & Year Rollover (ADR 0002 / ADR 0003)
    # =========================================================================
    @staticmethod
    async def create_academic_year(
        tenant_id: str,
        name: str,
        start_date=None,
        end_date=None,
        user_role: str | list[str] = "",
    ) -> dict:
        if not authz.require(user_role, {"school_admin", "super_admin"}):
            raise PermissionError("Only a school admin can define an academic year")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        year_id = await repo.create_academic_year(name, start_date, end_date)
        return await repo.get_academic_year_by_id(year_id)

    @staticmethod
    async def list_academic_years(tenant_id: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_all_academic_years()

    @staticmethod
    async def generate_rollover_plan(
        tenant_id: str,
        from_year_id: int,
        to_year_id: int,
        created_by=None,
        user_role: str | list[str] = "",
    ) -> dict:
        if not authz.require(user_role, {"school_admin", "super_admin"}):
            raise PermissionError("Only a school admin can run year rollover")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        from_year = await repo.get_academic_year_by_id(from_year_id)
        to_year = await repo.get_academic_year_by_id(to_year_id)
        if not from_year:
            raise ValueError(f"Academic year {from_year_id} not found")
        if not to_year:
            raise ValueError(f"Academic year {to_year_id} not found")
        if from_year["status"] != "active":
            raise ValueError("Rollover can only be planned from the currently active academic year")
        if to_year["status"] != "planned":
            raise ValueError(
                "The target academic year must be 'planned' (not yet active or closed)"
            )

        rollover = await repo.create_year_rollover(from_year_id, to_year_id, created_by)
        await TenantService._recompute_rollover_lines(repo, rollover["id"], from_year_id)
        return await TenantService.get_rollover(tenant_id, rollover["id"], user_role=user_role)

    @staticmethod
    async def _recompute_rollover_lines(
        repo: TenantRepository, rollover_id: int, from_year_id: int
    ) -> None:
        """Shared by generate + preview: recompute proposed_action/exception_code
        from scratch, but upsert (never blind-replace) so any override_action an
        admin already set on a line survives the recompute untouched.

        Clone rule (ADR 0003): a class clones forward to the next level only if
        it is `is_active` and has a parseable `section_label`; a student whose
        class doesn't clone forward holds rather than being guessed at.
        """
        facts = await repo.get_rollover_plan_facts(from_year_id)
        # Existing lines carry whatever override an admin already set -- needed
        # below to group capacity by each line's EFFECTIVE target, not just the
        # engine's raw proposal (an override can move a student to a different
        # class than the one the engine would have picked).
        existing_lines = {
            line["student_id"]: line for line in await repo.get_rollover_lines(rollover_id)
        }

        lines = []
        for fact in facts:
            if fact["to_level_id"] is None:
                proposed_action, exception_code = "graduate", "no_next_level"
            elif not fact["from_class_active"] or not fact["section_label"]:
                proposed_action, exception_code = "hold", "no_placement"
            else:
                proposed_action, exception_code = "promote", None
            lines.append(
                {
                    "student_id": fact["student_id"],
                    "from_class_id": fact["from_class_id"],
                    "to_level_id": fact["to_level_id"] if proposed_action == "promote" else None,
                    "to_section_label": (
                        fact["section_label"] if proposed_action == "promote" else None
                    ),
                    "proposed_action": proposed_action,
                    "exception_code": exception_code,
                    "_capacity": fact["from_class_capacity"],
                }
            )

        # Non-blocking over_capacity flag: group by each line's EFFECTIVE
        # target (an existing override's frozen to_level_id/to_section_label --
        # see upsert_rollover_lines -- if one is set, else the engine's fresh
        # proposal), compare the projected headcount against the capacity the
        # target class will have. That capacity is the source class's capacity
        # for any line whose OWN natural proposal targets that cell (what the
        # new class inherits at commit); a target invented purely by an
        # override, with no line naturally mapping there, falls back to the
        # same default (25) class creation uses elsewhere in this codebase.
        groups: dict[tuple, list[dict]] = {}
        capacity_by_key: dict[tuple, int] = {}
        for line in lines:
            existing = existing_lines.get(line["student_id"])
            override_action = existing["override_action"] if existing else None
            effective_action = override_action or line["proposed_action"]
            if effective_action != "promote":
                continue
            key = (
                (existing["to_level_id"], existing["to_section_label"])
                if override_action
                else (line["to_level_id"], line["to_section_label"])
            )
            groups.setdefault(key, []).append(line)
            if line["proposed_action"] == "promote":
                capacity_by_key.setdefault(key, line["_capacity"] or 25)

        for key, group_lines in groups.items():
            capacity = capacity_by_key.get(key, 25)
            if len(group_lines) > capacity:
                for line in group_lines:
                    line["exception_code"] = "over_capacity"

        for line in lines:
            line.pop("_capacity", None)

        await repo.upsert_rollover_lines(rollover_id, lines)

    @staticmethod
    async def get_rollover(
        tenant_id: str, rollover_id: int, user_role: str | list[str] = ""
    ) -> dict:
        if not authz.require(user_role, {"school_admin", "super_admin"}):
            raise PermissionError("Only a school admin can view a rollover plan")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        rollover = await repo.get_year_rollover_by_id(rollover_id)
        if not rollover:
            raise ValueError(f"Rollover {rollover_id} not found")
        lines = await repo.get_rollover_lines(rollover_id)
        rollover["lines"] = lines
        rollover["blocking"] = [
            line["id"]
            for line in lines
            if (line["override_action"] or line["proposed_action"]) == "hold"
        ]
        return rollover

    @staticmethod
    async def override_rollover_line(
        tenant_id: str,
        line_id: int,
        override_action: str | None,
        to_level_id: int | None = None,
        to_section_label: str | None = None,
        user_role: str | list[str] = "",
    ) -> dict:
        if not authz.require(user_role, {"school_admin", "super_admin"}):
            raise PermissionError("Only a school admin can override a rollover line")
        if override_action is not None and override_action not in (
            "promote",
            "graduate",
            "hold",
            "withdraw",
        ):
            raise ValueError(f"Invalid override action: {override_action}")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        line = await repo.get_rollover_line_by_id(line_id)
        if not line:
            raise ValueError(f"Rollover line {line_id} not found")

        effective_level = to_level_id if to_level_id is not None else line["to_level_id"]
        effective_section = (
            to_section_label if to_section_label is not None else line["to_section_label"]
        )
        if override_action == "promote" and not (effective_level and effective_section):
            raise ValueError(
                "Cannot override to 'promote' without a resolved target -- "
                "pass both to_level_id and to_section_label to resolve the placement first"
            )
        return await repo.set_rollover_line_override(
            line_id, override_action, to_level_id, to_section_label
        )

    @staticmethod
    async def preview_rollover(
        tenant_id: str, rollover_id: int, user_role: str | list[str] = ""
    ) -> dict:
        if not authz.require(user_role, {"school_admin", "super_admin"}):
            raise PermissionError("Only a school admin can preview a rollover plan")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        rollover = await repo.get_year_rollover_by_id(rollover_id)
        if not rollover:
            raise ValueError(f"Rollover {rollover_id} not found")
        if rollover["state"] == "committed":
            raise ValueError("This rollover has already been committed")

        await TenantService._recompute_rollover_lines(repo, rollover_id, rollover["from_year_id"])

        lines = await repo.get_rollover_lines(rollover_id)
        blocking = [
            line for line in lines if (line["override_action"] or line["proposed_action"]) == "hold"
        ]
        if blocking:
            raise ValueError(
                f"{len(blocking)} student(s) have no resolved placement and must be overridden "
                f"before this rollover can advance to preview"
            )

        await repo.update_rollover_state(rollover_id, "previewed")
        return await TenantService.get_rollover(tenant_id, rollover_id, user_role=user_role)

    @staticmethod
    async def commit_rollover(
        tenant_id: str, rollover_id: int, changed_by=None, user_role: str | list[str] = ""
    ) -> dict:
        if not authz.require(user_role, {"school_admin", "super_admin"}):
            raise PermissionError("Only a school admin can commit a rollover")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.commit_year_rollover(rollover_id, changed_by)
