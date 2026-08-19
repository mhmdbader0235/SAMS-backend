"""SchoolService — Day-1 onboarding business logic.

Owns the tenant setup state machine: School Information (Stage 1) -> Academic
Structure / Curriculum Wizard (Stage 2, see domains/tenant) -> Activate
(Stage 3), which permanently locks the curriculum system.
"""

from app.core.database import get_db_pool
from app.domains.school.repository import SchoolRepository
from app.domains.tenant.tenant_repository import TenantRepository

_ADMIN_ROLES = {"school_admin", "super_admin", "admin"}

_REQUIRED_PROFILE_FIELDS = ("legal_name", "display_name", "school_code", "country", "timezone", "currency")


class SchoolService:
    @staticmethod
    def _require_admin(user_role: str | list[str]) -> None:
        # Deliberately NOT TenantService._has_intersection: that helper expands
        # each of the caller's granular permissions back to every role that
        # could plausibly hold it (see PERMISSION_TO_HIGH_LEVEL_ROLE_MAP), which
        # makes "school_admin" match almost any role that shares a read
        # permission with school_admin — far too loose for an admin-only gate
        # on tenant-wide setup data. Use a direct, strict role-membership check
        # instead (same semantics as CurrentUser.has_role() elsewhere).
        if isinstance(user_role, str):
            roles = {user_role} if user_role else set()
        elif isinstance(user_role, (list, tuple, set)):
            roles = set(user_role)
        else:
            roles = set()
        if not roles.intersection(_ADMIN_ROLES):
            raise PermissionError("Only school_admin or super_admin can manage school setup.")

    # =========================================================================
    # Setup state
    # =========================================================================
    @staticmethod
    async def get_setup_state(tenant_id: str) -> dict:
        pool = await get_db_pool(tenant_id)
        school_repo = SchoolRepository(pool)
        profile = await school_repo.ensure_profile_row()

        structure = await TenantRepository(pool).get_academic_structure()
        has_structure = bool(structure.get("has_structure"))

        # Legacy-tenant grandfathering: a tenant that already had a real
        # academic structure before this feature existed must never be
        # dropped into the onboarding wizard.
        if profile.get("activated_at") is None and has_structure:
            await school_repo.grandfather_activate_if_missing()
            profile = await school_repo.get_profile_row()

        has_emergency = await school_repo.has_emergency_contact()
        campuses = await school_repo.list_campuses()
        has_primary_campus = any(c.get("is_primary") for c in campuses)

        is_live = bool(profile.get("activated_at"))

        blocking: list[str] = []
        if not is_live:
            if not profile.get("profile_committed_at"):
                blocking.append("Complete School Information (Stage 1).")
            if not has_primary_campus:
                blocking.append("Add the primary campus address.")
            if not has_emergency:
                blocking.append("Add at least one emergency contact with a phone number.")
            if not has_structure:
                blocking.append("Configure the curriculum system, grades, and at least one class section.")

        return {
            "status": "live" if is_live else "setup",
            "steps": {
                "profile_committed": bool(profile.get("profile_committed_at")),
                "has_primary_campus": has_primary_campus,
                "has_emergency_contact": has_emergency,
                "structure_committed": has_structure,
            },
            "blocking": blocking,
            "warnings": [],
            "activated_at": profile.get("activated_at"),
        }

    # =========================================================================
    # Profile
    # =========================================================================
    @staticmethod
    async def get_profile_bundle(tenant_id: str) -> dict:
        pool = await get_db_pool(tenant_id)
        repo = SchoolRepository(pool)
        profile = await repo.ensure_profile_row()
        campuses = await repo.list_campuses()
        contacts = await repo.list_contacts()
        return {**profile, "campuses": campuses, "contacts": contacts}

    @staticmethod
    async def update_profile(tenant_id: str, fields: dict, user_role: str | list[str]) -> dict:
        SchoolService._require_admin(user_role)
        pool = await get_db_pool(tenant_id)
        repo = SchoolRepository(pool)
        current = await repo.ensure_profile_row()

        clean = {k: v for k, v in fields.items() if v is not None}

        if current.get("activated_at"):
            for locked_field in ("school_code", "currency"):
                if locked_field in clean and str(clean[locked_field]) != str(current.get(locked_field)):
                    raise PermissionError(
                        f"'{locked_field}' is immutable after the school has been activated."
                    )

        if not clean:
            return current
        return await repo.update_profile(clean)

    # =========================================================================
    # Campus
    # =========================================================================
    @staticmethod
    async def list_campuses(tenant_id: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        return await SchoolRepository(pool).list_campuses()

    @staticmethod
    async def upsert_campus(tenant_id: str, fields: dict, user_role: str | list[str]) -> dict:
        SchoolService._require_admin(user_role)
        pool = await get_db_pool(tenant_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        return await SchoolRepository(pool).upsert_primary_campus(clean)

    # =========================================================================
    # Contacts
    # =========================================================================
    @staticmethod
    async def list_contacts(tenant_id: str) -> list[dict]:
        pool = await get_db_pool(tenant_id)
        return await SchoolRepository(pool).list_contacts()

    @staticmethod
    async def create_contact(tenant_id: str, fields: dict, user_role: str | list[str]) -> dict:
        SchoolService._require_admin(user_role)
        pool = await get_db_pool(tenant_id)
        return await SchoolRepository(pool).create_contact(fields)

    @staticmethod
    async def update_contact(tenant_id: str, contact_id: int, fields: dict, user_role: str | list[str]) -> dict:
        SchoolService._require_admin(user_role)
        pool = await get_db_pool(tenant_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        updated = await SchoolRepository(pool).update_contact(contact_id, clean)
        if not updated:
            raise ValueError(f"Contact {contact_id} not found")
        return updated

    @staticmethod
    async def delete_contact(tenant_id: str, contact_id: int, user_role: str | list[str]) -> None:
        SchoolService._require_admin(user_role)
        pool = await get_db_pool(tenant_id)
        await SchoolRepository(pool).delete_contact(contact_id)

    # =========================================================================
    # Setup lifecycle transitions
    # =========================================================================
    @staticmethod
    async def commit_profile(tenant_id: str, user_role: str | list[str]) -> dict:
        """Validate and lock in Stage 1 (School Information)."""
        SchoolService._require_admin(user_role)
        pool = await get_db_pool(tenant_id)
        repo = SchoolRepository(pool)
        profile = await repo.ensure_profile_row()

        missing = [f for f in _REQUIRED_PROFILE_FIELDS if not profile.get(f)]
        if missing:
            raise ValueError(f"Missing required school information: {', '.join(missing)}")

        if not await repo.list_campuses():
            raise ValueError("Add at least one campus address before continuing.")

        if not await repo.has_emergency_contact():
            raise ValueError("Add at least one emergency contact with a phone number before continuing.")

        await repo.stamp_profile_committed()
        return await repo.get_profile_row()

    @staticmethod
    async def activate(tenant_id: str, user_role: str | list[str]) -> dict:
        """Validate Stage 1 + Stage 2 and activate — permanently locks the
        curriculum system while grades/sections stay editable."""
        SchoolService._require_admin(user_role)
        pool = await get_db_pool(tenant_id)
        repo = SchoolRepository(pool)
        profile = await repo.ensure_profile_row()

        if not profile.get("profile_committed_at"):
            raise ValueError("Complete School Information before activating.")

        structure = await TenantRepository(pool).get_academic_structure()
        if not structure.get("has_structure"):
            raise ValueError(
                "Configure the curriculum system, grades, and at least one class section before activating."
            )

        await repo.activate()
        return await repo.get_profile_row()
