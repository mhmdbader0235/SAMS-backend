"""
TenantService — coordinates business logic for tenant-specific operations.

Implements PII encryption/decryption, masking, and audit logging.
Does not import FastAPI or asyncpg directly.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.fernet import Fernet

from app.core.config import ENCRYPTION_KEY
from app.core.database import get_db_pool
from app.domains.auth.service import AuthService
from app.domains.tenant.tenant_repository import TenantRepository, parse_id
from app.domains.tenant.user_repository import UserRepository

_fernet = Fernet(ENCRYPTION_KEY.encode())


# =============================================================================
# Helper Utilities
# =============================================================================
def _encrypt(val: str) -> str:
    return _fernet.encrypt(val.encode()).decode()


def _decrypt(val: str) -> str:
    return _fernet.decrypt(val.encode()).decode()


def _log_audit(user_id, action: str) -> None:
    """Audit Trail: Log user ID, timestamp, and action without leaking PII."""
    timestamp = datetime.now(UTC).isoformat()
    print(f"[AUDIT] Timestamp: {timestamp} | User: {user_id} | Action: {action}")


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
    async def create_level(tenant_id: str, name: str, user_role: str) -> int:
        if user_role not in ("school_admin", "teacher"):
            raise PermissionError("Only staff can create levels")
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        existing = await repo.get_level_by_name(name)
        if existing:
            return existing["level_id"]
        return await repo.create_level(name)

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
        user_role: str,
    ) -> int:
        if user_role not in ("school_admin", "teacher"):
            raise PermissionError("Only staff can register teachers")

        pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(pool)
        tenant_repo = TenantRepository(pool)

        password_hash = AuthService.hash_password(password)
        user_id = await user_repo.create_user(email, password_hash, "teacher")

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
        user_role: str,
    ) -> int:
        if user_role != "school_admin":
            raise PermissionError("Only school admins can register staff users")

        if role not in ("manager", "finance"):
            raise ValueError("Invalid staff role")

        pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(pool)

        if await user_repo.get_user_by_email(email):
            raise ValueError("Email already registered")

        password_hash = AuthService.hash_password(password)
        user_id = await user_repo.create_user(email, password_hash, role)
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
        user_role: str,
    ) -> int:
        if user_role not in ("school_admin", "teacher"):
            raise PermissionError("Only staff can register students")

        pool = await get_db_pool(tenant_id)
        user_repo = UserRepository(pool)
        tenant_repo = TenantRepository(pool)

        # Create tenant user with 'student' role
        password_hash = AuthService.hash_password(password)
        user_id = await user_repo.create_user(email, password_hash, "student")

        # Create student profile linking to user and class
        return await tenant_repo.create_student(
            user_id=user_id,
            name=name,
            class_id=class_id,
            gender=gender,
            birth_data=birth_data,
        )

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
    async def link_student_parent(tenant_id: str, student_id, parent_id, user_role: str, user_id=None) -> None:
        if user_role not in ("school_admin", "teacher"):
            raise PermissionError("Only school admins and teachers can link students and parents")
            
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        
        if user_role == "teacher":
            # Teacher must be the head teacher of the student's class
            student = await repo.get_student_by_id(student_id)
            if not student:
                raise ValueError("Student not found")
            class_info = await repo.get_class_by_head_teacher(user_id)
            if not class_info or class_info["id"] != student["class_id"]:
                raise PermissionError("You can only link parents to students in your own class")
                
        await repo.add_student_parent_link(student_id, parent_id)

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
        tenant_id: str, name: str, level_id: int, head_teacher_id, user_role: str
    ) -> int:
        if user_role not in ("school_admin", "teacher"):
            raise PermissionError("Only staff can create classes")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        existing = await repo.get_class_by_name_and_level(name, level_id)
        if existing:
            return existing["id"]
        return await repo.create_class(name, level_id, head_teacher_id)

    @staticmethod
    async def get_all_classes(tenant_id: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_all_classes()

    @staticmethod
    async def get_class_by_head_teacher(tenant_id: str, teacher_id) -> dict | None:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        return await repo.get_class_by_head_teacher(teacher_id)



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
        user_role: str,
    ) -> dict:
        if user_role not in ("school_admin", "teacher"):
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
        user_role: str,
        new_title: str | None = None,
        new_date: datetime | None = None,
    ) -> dict:
        if user_role not in ("school_admin", "teacher"):
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
                "ticket_price": float(m["ticket_price"]) if m.get("ticket_price") is not None else 0.0,
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
        user_role: str,
    ) -> dict:
        if user_role not in ("school_admin", "teacher"):
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
        user_role: str,
        user_id: int,
    ) -> dict:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        if user_role == "teacher":
            # Verify teacher is a head teacher of a class
            teacher_class = await repo.get_class_by_head_teacher(user_id)
            if not teacher_class:
                raise PermissionError("Teacher is not a head teacher of any class")
            teacher_class_id = teacher_class["id"]
            
            # Load the existing event to verify if this teacher's class is targeted
            existing_event = await repo.get_event_by_id(event_id)
            if not existing_event:
                raise ValueError("Event not found")
                
            target_class_ids = {int(m["class_id"]) for m in existing_event["class_mappings"]}
            if int(teacher_class_id) not in target_class_ids:
                raise PermissionError("Access denied. Event is not mapped to your class.")

            # Filter class mappings to only allow updating their own class's mapping
            filtered_mappings = []
            for mapping in class_mappings:
                if parse_id(mapping["class_id"]) == parse_id(teacher_class_id):
                    filtered_mappings.append(mapping)
            
            class_mappings = filtered_mappings
            
        elif user_role != "school_admin":
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
    async def get_events_for_user(tenant_id: str, user_id, user_role: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(user_id, user_role)

        if user_role in ("school_admin", "manager", "finance", "event_teacher"):
            events = await repo.get_all_events()
            return [ev for ev in events if TenantService.check_event_permission(actor, ev, "read")]
        elif user_role == "teacher":
            # Teacher can read owned events, published events, and events for classes they head
            teacher_class = await repo.get_class_by_head_teacher(actor.id)
            teacher_class_id = teacher_class["id"] if teacher_class else None
            events = await repo.get_all_events()
            def has_access(ev):
                if TenantService.check_event_permission(actor, ev, "read"):
                    return True
                if teacher_class_id and any(m.get("class_id") == teacher_class_id for m in ev.get("class_mappings", [])):
                    return True
                return False
            return [ev for ev in events if has_access(ev)]
            # removed duplicate lines

        elif user_role == "student":
            events = await repo.get_events_for_student(user_id)
            return [ev for ev in events if TenantService.check_event_permission(actor, ev, "read")]

        elif user_role == "parent":
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
            return sorted(events_dict.values(), key=lambda e: e["date"])

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

        # 2. Fetch student details
        student = await repo.get_student_by_id(student_id)
        if not student:
            raise ValueError("Student not found")

        # 3. Check if student is already enrolled in this event (across any class mappings)
        existing_event_enrollments = await repo.get_enrollments_for_student_and_event(student_id, class_map["event_id"])
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
    async def get_enrollments_for_user(tenant_id: str, user_id, user_role: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        if user_role in ("school_admin", "teacher"):
            return await repo.get_enrollments_for_teacher(user_id)
        elif user_role == "parent":
            return await repo.get_enrollments_for_parent(user_id)
        elif user_role == "student":
            return await repo.get_enrollments_for_student(user_id)
        return []

    @staticmethod
    async def cancel_enrollment(
        tenant_id: str,
        enrollment_id: int,
        user_id: int,
        user_role: str,
    ) -> None:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        enrollment = await repo.get_enrollment_by_id(enrollment_id)
        if not enrollment:
            raise ValueError("Enrollment not found")

        student_id = enrollment["student_id"]

        if user_role == "student":
            if int(student_id) != int(user_id):
                raise PermissionError("Students can only cancel their own enrollments")
        elif user_role == "parent":
            linked = await repo.is_student_linked_to_parent(student_id, user_id)
            if not linked:
                raise PermissionError("Parents can only cancel their linked children's enrollments")
        elif user_role not in ("teacher", "school_admin"):
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
    async def create_event_feedback(tenant_id: str, event_id: int, user_id, rating: int, comments: str | None) -> int:
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
        requesting_user_role: str,
    ) -> UUID:
        if requesting_user_role not in ("school_admin", "teacher"):
            raise PermissionError("Only staff can manage health records")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        nat_enc = _encrypt(national_id)
        med_enc = _encrypt(medical_conditions)
        emg_enc = _encrypt(emergency_contact)

        _log_audit(requesting_user_id, f"WRITE student_health_and_records for student_id: {student_id}")

        return await repo.create_or_update_student_health(
            student_id=student_id,
            national_id_encrypted=nat_enc,
            medical_conditions_encrypted=med_enc,
            emergency_contact_encrypted=emg_enc,
        )

    @staticmethod
    async def get_health_record(
        tenant_id: str,
        student_id,
        requesting_user_id,
        requesting_user_role: str,
        elevated_clearance: bool = False,
    ) -> dict | None:
        if requesting_user_role not in ("school_admin", "teacher"):
            raise PermissionError("Unauthorized to view student health records")

        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        record = await repo.get_student_health_by_student_id(student_id)
        if not record:
            return None

        nat_dec = _decrypt(record["national_id_encrypted"])
        med_dec = _decrypt(record["medical_conditions_encrypted"])
        emg_dec = _decrypt(record["emergency_contact_encrypted"])

        _log_audit(requesting_user_id, f"READ student_health_and_records for student_id: {student_id}")

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
        tenant_id: str, name: str, category: str, is_custom: bool = False, created_by_user_id = None
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
        
        async with repo.pool.acquire() as conn:
            async with conn.transaction():
                # Re-check status is draft or resource_planning
                event = await repo.get_event_by_id(event_id)
                if not event or event.get("status", "draft") not in ("draft", "resource_planning"):
                    raise ValueError("Resources can only be modified on draft or resource planning events")
                
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
            
        # Re-check status is finance_approval
        event = await repo.get_event_by_id(resource["event_id"])
        if not event or event.get("status", "draft") != "finance_approval":
            raise ValueError("Pricing can only be updated for events in finance approval")
            
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
        
        lines = []
        cost_sum = 0.0
        currency = "JOD"
        
        for r in resources:
            cost_info = await repo.get_resource_cost_by_resource_id(r["id"])
            if cost_info:
                unit_price = float(cost_info["unit_price"])
                total_cost = float(cost_info["total_cost"])
                currency = cost_info["currency"]
                set_by_user_id = cost_info["set_by_user_id"]
            else:
                unit_price = 0.0
                total_cost = 0.0
                set_by_user_id = None
                
            lines.append({
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
            })
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
        role = user.role
        status = event.get("status") or "draft"
        is_owner = int(parse_id(event.get("created_by"))) == int(parse_id(user.id))

        if action == "read":
            if role in ("parent", "student"):
                return status == "published"
            if role == "teacher":
                return is_owner or status == "published"  # Teachers can read their own drafts and published events
            if role == "event_teacher":
                return status in ("resource_planning", "proposed", "finance_approval", "final_review", "published")
            if role == "manager":
                return status in ("proposed", "finance_approval", "final_review", "published")
            if role == "finance":
                return status in ("finance_approval", "final_review", "published")
            if role == "school_admin":
                return True
            return False

        elif action == "edit_draft":
            return role == "teacher" and is_owner and status == "draft"

        elif action == "edit_resources":
            return role == "event_teacher" and status == "resource_planning"

        elif action == "manager_decision":
            return role in ("manager", "school_admin") and status == "proposed"

        elif action == "finance_pricing" or action == "finance_submit":
            return role in ("finance", "school_admin") and status == "finance_approval"

        elif action == "final_decision":
            return role in ("manager", "school_admin") and status == "final_review"

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
            ("draft", "submit_to_event_teacher"): ("resource_planning", "teacher"),
            ("resource_planning", "submit_for_approval"): ("proposed", "event_teacher"),
            ("resource_planning", "event_teacher_reject"): ("draft", "event_teacher"),
            ("proposed", "manager_approve"): ("finance_approval", "manager"),
            ("proposed", "manager_reject"): ("draft", "manager"),
            ("finance_approval", "finance_submit"): ("final_review", "finance"),
            ("final_review", "manager_publish"): ("published", "manager"),
            ("final_review", "manager_return_to_finance"): ("finance_approval", "manager"),
        }
        
        key = (current_status, action)
        if key not in TRANSITIONS:
            raise ValueError(f"Action '{action}' is not allowed in status '{current_status}'")
            
        next_status, required_role = TRANSITIONS[key]
        
        # Verify role (allow school_admin override)
        if actor.role != "school_admin" and actor.role != required_role:
            raise PermissionError(f"Role '{actor.role}' is not authorized to perform action '{action}'")
            
        # Verify preconditions
        if action == "submit_to_event_teacher":
            if int(parse_id(event["created_by"])) != int(parse_id(actor.id)):
                raise PermissionError("Only the event creator can submit it for approval")
                
            mappings = await repo.get_event_class_mappings(event_id) if hasattr(repo, "get_event_class_mappings") else None
            # fallback if get_event_class_mappings doesn't exist
            if not mappings:
                mappings = event.get("class_mappings") or []
            if not mappings:
                raise ValueError("At least one class must be selected before submitting")
                
        elif action == "submit_for_approval":
            resources = await repo.get_resources_for_event(event_id)
            if not resources:
                raise ValueError("At least one resource line exists before submitting")
                
        elif action in ("manager_reject", "manager_return_to_finance", "event_teacher_reject"):
            if not reason or not reason.strip():
                raise ValueError(f"A non-empty reason is required for action '{action}'")
                
        elif action == "finance_submit":
            resources = await repo.get_resources_for_event(event_id)
            for r in resources:
                cost = await repo.get_resource_cost_by_resource_id(r["id"])
                if not cost:
                    raise ValueError("Every resource row must have a matching price row")
                    
        # Apply updates and side effects
        update_fields = {"status": next_status}
        now_time = datetime.now(UTC)
        
        if action == "submit_for_approval":
            update_fields["submitted_at"] = now_time
            # Calculate predicted attendance before submitting
            mappings = event.get("class_mappings") or []
            class_ids = [m["class_id"] for m in mappings]
            update_fields["predicted_attendance"] = await TenantService.get_predicted_attendance(tenant_id, class_ids)
            
            # Notify managers
            managers = await repo.get_all_managers()
            for m in managers:
                await repo.create_notification(
                    event_id=event_id,
                    recipient_user_id=m["id"],
                    title_override=f"New event proposal: '{event['title']}' submitted for approval",
                )
                
        elif action == "manager_approve":
            update_fields["manager_approved_at"] = now_time
            update_fields["manager_reviewer_id"] = actor.id
            # Notify finance
            finance_users = await repo.get_all_finance_users()
            for f in finance_users:
                await repo.create_notification(
                    event_id=event_id,
                    recipient_user_id=f["id"],
                    title_override=f"Event '{event['title']}' approved by manager, needs pricing",
                )
                
        elif action == "manager_reject":
            # Notify teacher owner with reason
            await repo.create_notification(
                event_id=event_id,
                recipient_user_id=event["created_by"],
                title_override=f"Event '{event['title']}' rejected by manager. Reason: {reason}",
            )
            
        elif action == "finance_submit":
            update_fields["finance_priced_at"] = now_time
            update_fields["finance_reviewer_id"] = actor.id
            
            # Compute events.total_cost
            resources = await repo.get_resources_for_event(event_id)
            tot_cost = 0.0
            for r in resources:
                cost = await repo.get_resource_cost_by_resource_id(r["id"])
                if cost:
                    tot_cost += float(cost["total_cost"])
            update_fields["total_cost"] = tot_cost
            
            # Notify managers
            managers = await repo.get_all_managers()
            for m in managers:
                await repo.create_notification(
                    event_id=event_id,
                    recipient_user_id=m["id"],
                    title_override=f"Event '{event['title']}' priced by finance, ready for final review",
                )
                
        elif action == "manager_publish":
            update_fields["published_at"] = now_time
            # Notify parents & students of targeted classes
            mappings = event.get("class_mappings") or []
            class_ids = [m["class_id"] for m in mappings]
            if class_ids:
                students = await repo.pool.fetch(
                    "SELECT id FROM students WHERE class_id = ANY($1)",
                    [parse_id(cid) for cid in class_ids]
                )
                for s in students:
                    await repo.create_notification(
                        event_id=event_id,
                        recipient_user_id=s["id"],
                        title_override=f"New Event Published: '{event['title']}'",
                    )
                    parents = await repo.pool.fetch(
                        "SELECT parent_id FROM student_parent_map WHERE student_id = $1",
                        s["id"]
                    )
                    for p in parents:
                        await repo.create_notification(
                            event_id=event_id,
                            recipient_user_id=p["parent_id"],
                            title_override=f"New Child Event: '{event['title']}' has been published!",
                        )
                        
        elif action == "manager_return_to_finance":
            # Revert to finance approval status
            # Notify finance
            finance_users = await repo.get_all_finance_users()
            for f in finance_users:
                await repo.create_notification(
                    event_id=event_id,
                    recipient_user_id=f["id"],
                    title_override=f"Event '{event['title']}' returned to finance for repricing. Reason: {reason}",
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
                published_at = COALESCE($9, published_at)
            WHERE id = $10
            """,
            update_fields.get("status"),
            update_fields.get("predicted_attendance"),
            parse_id(update_fields["manager_reviewer_id"]) if "manager_reviewer_id" in update_fields else None,
            parse_id(update_fields["finance_reviewer_id"]) if "finance_reviewer_id" in update_fields else None,
            update_fields.get("total_cost"),
            update_fields.get("submitted_at"),
            update_fields.get("manager_approved_at"),
            update_fields.get("finance_priced_at"),
            update_fields.get("published_at"),
            parse_id(event_id),
        )
        
        return await repo.get_event_by_id(event_id)

