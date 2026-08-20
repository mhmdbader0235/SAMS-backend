import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from pydantic import BaseModel

from app.core.database import get_control_plane_pool, get_db_pool
from app.core.dependencies import (
    CurrentUser,
    get_current_user,
    require_permission,
    require_tenant_live,
)
from app.core.schemas import TokenResponse, UserLoginRequest, UserRegisterRequest
from app.domains.auth.service import AuthService
from app.domains.tenant.control_plane_repository import ControlPlaneRepository
from app.domains.tenant.tenant_repository import TenantRepository
from app.domains.tenant.user_repository import UserRepository

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
# Tenant-mutating admin actions (invitations, role/permission changes, tenant
# creation) are off-limits until Day-1 setup is complete. Login, register,
# /me, the public tenant list, invitation-code verification, and
# self-service profile reads stay reachable so auth itself never breaks.
router_gated = APIRouter(
    prefix="/api/v1/auth", tags=["auth"], dependencies=[Depends(require_tenant_live)]
)
_security = HTTPBearer(auto_error=False)


class CreateTenantRequest(BaseModel):
    tenant_id: str
    name: str


class CreateInvitationRequest(BaseModel):
    tenant_id: str
    role: str
    target_email: str | None = None
    max_uses: int = 1
    valid_days: int = 7


@router.get("/tenants", summary="List available tenant IDs")
async def list_tenants() -> dict:
    """Return the list of registered tenant (school) identifiers."""
    try:
        tenants = await AuthService.list_tenants()
        return {"tenants": [t["tenant_id"] for t in tenants]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.post("/tenants", summary="Create a new tenant and generate schema (Super Admin)")
async def create_tenant(
    payload: CreateTenantRequest,
    current_user: CurrentUser = Depends(require_permission("tenant:manage")),
) -> dict:
    """Register a new tenant in control plane and generate its PostgreSQL schema & tables."""
    try:
        res = await AuthService.create_tenant(payload.tenant_id, payload.name)
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.post("/invitations", summary="Create a role- & tenant-scoped invitation token")
async def create_invitation(
    payload: CreateInvitationRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Generate a locked invitation code for a specific tenant and role."""
    allowed_roles = {"school_admin", "super_admin"}
    if (
        current_user.role not in allowed_roles
        and "school_admin" not in current_user.roles
        and "super_admin" not in current_user.roles
    ):
        raise HTTPException(
            status_code=403, detail="Only school_admin or super_admin can create invitations"
        )

    # Strict Tenant Scoping: non-super_admin can only create invitations for their own tenant
    is_super = current_user.role == "super_admin" or "super_admin" in (current_user.roles or [])
    target_tenant = payload.tenant_id
    if not is_super and current_user.tenant_id:
        if (
            payload.tenant_id
            and payload.tenant_id.strip().lower() != current_user.tenant_id.strip().lower()
        ):
            raise HTTPException(
                status_code=403,
                detail=f"Admins of tenant '{current_user.tenant_id}' cannot create invitations for tenant '{payload.tenant_id}'",
            )
        target_tenant = current_user.tenant_id

    # Safely resolve created_by — only pass a UUID; plain integer local-DB IDs are set to None
    from uuid import UUID as _UUID

    created_by_uuid = None
    try:
        if current_user.id and not str(current_user.id).isdigit():
            created_by_uuid = _UUID(str(current_user.id))
    except (ValueError, AttributeError):
        created_by_uuid = None

    try:
        inv = await AuthService.create_invitation(
            tenant_id=target_tenant,
            role=payload.role,
            target_email=payload.target_email,
            max_uses=payload.max_uses,
            valid_days=payload.valid_days,
            created_by=created_by_uuid,
        )
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        target_em = inv.get("target_email") or ""
        # No auto_google=true here: this Keycloak realm has no Google/external
        # IdP configured, so forcing that redirect sends a brand-new invitee
        # to a dead-end Keycloak login screen instead of the app's own
        # invite-aware registration form (which is what actually works).
        reg_link = f"{frontend_url}/auth?invite_code={inv['code']}&email={target_em}"
        return {
            "code": inv["code"],
            "tenant_id": inv["tenant_id"],
            "role": inv["role"],
            "target_email": inv["target_email"],
            "max_uses": inv["max_uses"],
            "expires_at": inv["expires_at"].isoformat() if inv["expires_at"] else None,
            "registration_link": reg_link,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/invitations/{code}", summary="Verify invitation token for frontend pre-check")
async def get_invitation(code: str) -> dict:
    """Pre-check invitation token validity and return locked tenant_id and role."""
    try:
        inv = await AuthService.get_invitation(code)
        return {
            "valid": True,
            "code": inv["code"],
            "tenant_id": inv["tenant_id"],
            "role": inv["role"],
            "target_email": inv["target_email"],
            "max_uses": inv["max_uses"],
            "uses_count": inv["uses_count"],
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/register", response_model=TokenResponse, summary="Register a new user")
async def register(payload: UserRegisterRequest) -> TokenResponse:
    tenant_id = payload.tenant_id or "tenant_a"
    role = payload.role or "student"

    try:
        token = await AuthService.register_user(
            email=str(payload.email),
            password=payload.password,
            role=role,
            tenant_id=tenant_id if role != "super_admin" else None,
            invite_code=payload.invite_code,
            name=payload.name,
            first_name=payload.first_name,
            last_name=payload.last_name,
        )
        return TokenResponse(access_token=token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/login", response_model=TokenResponse, summary="Login and receive a JWT")
async def login(payload: UserLoginRequest) -> TokenResponse:
    tenant_id = payload.tenant_id or "tenant_a"

    try:
        token = await AuthService.login_user(
            email=str(payload.email),
            password=payload.password,
            tenant_id=tenant_id,
        )
        return TokenResponse(access_token=token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/me", summary="Return current user info from JWT")
async def me(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    return {
        "user_id": str(current_user.id),
        "tenant_id": current_user.tenant_id,
        "role": current_user.role,
        "roles": current_user.roles,
        "email": current_user.email,
    }


class ProfileStudentInfo(BaseModel):
    name: str
    email: str


class ProfileResponse(BaseModel):
    email: str
    phone: str | None = None
    address: str | None = None
    class_id: int | None = None
    class_name: str | None = None
    parent_name: str | None = None
    parent_email: str | None = None
    students: list[ProfileStudentInfo] | None = None


class ProfileUpdateRequest(BaseModel):
    phone: str | None = None
    address: str | None = None


@router.get("/profile", response_model=ProfileResponse, summary="Get user profile")
async def get_profile(current_user: CurrentUser = Depends(get_current_user)) -> ProfileResponse:
    try:
        class_id = None
        class_name = None
        parent_name = None
        parent_email = None
        students = None
        profile = None

        if current_user.role == "parent":
            cp_pool = await get_control_plane_pool()
            cp_repo = ControlPlaneRepository(cp_pool)
            if current_user.email:
                profile = await cp_repo.get_parent_by_email(current_user.email)
            if profile and current_user.tenant_id:
                pool = await get_db_pool(current_user.tenant_id)
                user_repo = UserRepository(pool)
                local_user = await user_repo.get_user_by_email(current_user.email)
                if local_user:
                    tenant_repo = TenantRepository(pool)
                    linked_students = await tenant_repo.get_linked_students_for_parent(
                        local_user["id"]
                    )
                    students = [
                        ProfileStudentInfo(name=s["name"], email=s["email"])
                        for s in linked_students
                    ]
                else:
                    students = []
        else:
            if current_user.tenant_id:
                pool = await get_db_pool(current_user.tenant_id)
                user_repo = UserRepository(pool)
                if current_user.email:
                    profile = await user_repo.get_user_by_email(current_user.email)
                if not profile and str(current_user.id).isdigit():
                    profile = await user_repo.get_user_profile(int(current_user.id))

                if profile:
                    db_user_id = profile["id"]
                    if current_user.role == "student":
                        tenant_repo = TenantRepository(pool)
                        student_info = await tenant_repo.get_student_by_id(db_user_id)
                        if student_info:
                            class_id = student_info.get("class_id")
                            class_name = student_info.get("class_name")
                        parent_info = await tenant_repo.get_parent_for_student(db_user_id)
                        if parent_info:
                            parent_name = parent_info.get("name")
                            parent_email = parent_info.get("email")
                    elif current_user.role == "teacher":
                        from app.domains.tenant.service import TenantService

                        class_info = await TenantService.get_class_by_head_teacher(
                            current_user.tenant_id, db_user_id
                        )
                        if class_info:
                            class_id = class_info.get("id")
                            class_name = (
                                f"{class_info.get('name')} ({class_info.get('level_name')})"
                            )

        email = (
            profile["email"]
            if (profile and "email" in profile)
            else (current_user.email or "user@school.com")
        )
        phone = profile.get("phone") if profile else None
        address = profile.get("address") if profile else None

        return ProfileResponse(
            email=email,
            phone=phone,
            address=address,
            class_id=class_id,
            class_name=class_name,
            parent_name=parent_name,
            parent_email=parent_email,
            students=students,
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[get_profile ERROR] {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.post("/profile", summary="Update user profile")
async def update_profile(
    payload: ProfileUpdateRequest, current_user: CurrentUser = Depends(get_current_user)
) -> dict:
    try:
        # Update local tenant DB profile for all authenticated users
        pool = await get_db_pool(current_user.tenant_id)
        user_repo = UserRepository(pool)
        await user_repo.update_user_profile(current_user.id, payload.phone, payload.address)

        # For parents, also update global control-plane profile
        if current_user.role == "parent":
            cp_pool = await get_control_plane_pool()
            cp_repo = ControlPlaneRepository(cp_pool)
            parent = await cp_repo.get_parent_by_email(current_user.email)
            if not parent:
                raise HTTPException(status_code=404, detail="Parent not found")
            await cp_repo.update_parent_profile(parent["id"], payload.phone, payload.address)

        return {"status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get(
    "/parent-profile", response_model=ProfileResponse, summary="Get parent profile by email"
)
async def get_parent_profile_by_email(
    email: str, current_user: CurrentUser = Depends(get_current_user)
) -> ProfileResponse:
    if current_user.role not in ("teacher", "school_admin"):
        raise HTTPException(status_code=403, detail="Only staff can view parent details")

    try:
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)

        parent = await cp_repo.get_parent_by_email(email)
        if not parent:
            raise HTTPException(status_code=404, detail="Parent not found")

        profile = await cp_repo.get_parent_profile(parent["id"])
        if not profile:
            raise HTTPException(status_code=404, detail="Parent profile not found")

        return ProfileResponse(
            email=profile["email"], phone=profile["phone"], address=profile["address"]
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# User Permissions & Dynamic Roles Management (School Admin only)
# =============================================================================
class UserPermissionsResponse(BaseModel):
    id: int | str
    email: str
    role: str
    roles: list[str] = []
    permissions: list[str] = []
    phone: str | None = None
    address: str | None = None
    created_at: datetime | None = None


class UserPermissionsUpdateRequest(BaseModel):
    role: str
    roles: list[str] = []
    permissions: list[str] = []


@router_gated.get(
    "/users-permissions",
    response_model=list[UserPermissionsResponse],
    summary="List all users and permissions (admin only)",
)
async def list_users_permissions(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[UserPermissionsResponse]:
    if not (
        current_user.has_any_role("school_admin", "super_admin", "admin")
        or current_user.has_role("user:view")
    ):
        raise HTTPException(
            status_code=403, detail="Only school administrators can access user permissions"
        )

    # current_user.tenant_id is already the security-checked value (only
    # super_admin can influence it via X-Tenant-ID -- see get_current_user) --
    # re-reading the raw header here would let ANY caller silently retarget
    # this action at a different tenant than the one their own token grants.
    tenant_id = (current_user.tenant_id or "tenant_a").strip().lower()
    try:
        from app.domains.tenant.service import TenantService

        users = await TenantService.get_tenant_users_permissions(tenant_id, current_user.roles)
        return [UserPermissionsResponse(**u) for u in users]
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.put(
    "/users/{user_id}/permissions",
    response_model=UserPermissionsResponse,
    summary="Update user roles and permissions (admin only)",
)
async def update_user_permissions(
    user_id: int | str,
    payload: UserPermissionsUpdateRequest,
    current_user: CurrentUser = Depends(require_permission("user:invite")),
) -> UserPermissionsResponse:
    # current_user.tenant_id is already the security-checked value (only
    # super_admin can influence it via X-Tenant-ID -- see get_current_user) --
    # re-reading the raw header here would let ANY caller silently retarget
    # this action at a different tenant than the one their own token grants.
    tenant_id = (current_user.tenant_id or "tenant_a").strip().lower()
    try:
        from app.domains.tenant.service import TenantService

        updated = await TenantService.update_tenant_user_permissions(
            tenant_id=tenant_id,
            user_id=user_id,
            primary_role=payload.role,
            roles=payload.roles,
            permissions=payload.permissions,
            user_role=current_user.roles,
        )
        return UserPermissionsResponse(**updated)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.delete("/users/{user_id}", summary="Permanently delete a user (admin only)")
async def delete_user(
    user_id: int | str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    if not (
        current_user.has_any_role("school_admin", "super_admin", "admin")
        or current_user.has_role("user:delete")
    ):
        raise HTTPException(status_code=403, detail="Only school administrators can delete users")

    # current_user.tenant_id is already the security-checked value (only
    # super_admin can influence it via X-Tenant-ID -- see get_current_user) --
    # re-reading the raw header here would let ANY caller silently retarget
    # this action at a different tenant than the one their own token grants.
    tenant_id = (current_user.tenant_id or "tenant_a").strip().lower()
    try:
        from app.domains.tenant.service import TenantService

        deleted = await TenantService.delete_tenant_user(
            tenant_id=tenant_id,
            target_user_id=user_id,
            requesting_user_id=current_user.id,
            user_role=current_user.roles,
        )
        return {"status": "ok", "deleted_user_id": deleted.get("id"), "email": deleted.get("email")}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 409
        raise HTTPException(status_code=status_code, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get("/users/pending", summary="Get all pending users (admin only)")
async def get_pending_users(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    if not (current_user.has_any_role("school_admin", "super_admin", "admin")):
        raise HTTPException(
            status_code=403, detail="Only school administrators can view pending users"
        )

    # current_user.tenant_id is already the security-checked value (only
    # super_admin can influence it via X-Tenant-ID -- see get_current_user) --
    # re-reading the raw header here would let ANY caller silently retarget
    # this action at a different tenant than the one their own token grants.
    tenant_id = (current_user.tenant_id or "tenant_a").strip().lower()
    try:
        users = await AuthService.get_pending_users(tenant_id)
        return users
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


from app.core.schemas import UserRoleUpdateRequest


@router_gated.patch("/users/{email}/role", summary="Assign role to pending user (admin only)")
async def assign_user_role(
    email: str,
    payload: UserRoleUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    if not (current_user.has_any_role("school_admin", "super_admin", "admin")):
        raise HTTPException(status_code=403, detail="Only school administrators can assign roles")

    # current_user.tenant_id is already the security-checked value (only
    # super_admin can influence it via X-Tenant-ID -- see get_current_user) --
    # re-reading the raw header here would let ANY caller silently retarget
    # this action at a different tenant than the one their own token grants.
    tenant_id = (current_user.tenant_id or "tenant_a").strip().lower()
    try:
        res = await AuthService.assign_user_role(tenant_id, email, payload.role)
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/roles-catalog", summary="Get comprehensive roles & permissions catalog")
async def get_roles_catalog(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    from app.core.dependencies import COMPOSITE_ROLE_PERMISSIONS

    COMPOSITE_ROLES = [
        {
            "id": "super_admin",
            "label": "Super Administrator",
            "description": "Full platform administration across all tenants and schemas.",
        },
        {
            "id": "school_admin",
            "label": "School Administrator",
            "description": "Full school-level management, staffing, and settings.",
        },
        {
            "id": "manager",
            "label": "Operations Manager",
            "description": "Event proposal review, pricing, publishing, and budget approvals.",
        },
        {
            "id": "teacher",
            "label": "Teacher / Class Lead",
            "description": "Draft event creation, resource requests, and student approvals.",
        },
        {
            "id": "parent",
            "label": "Parent / Guardian",
            "description": "Child trip view, enrollment approval, and payment.",
        },
        {
            "id": "student",
            "label": "Student",
            "description": "Browse class events and submit enrollment requests.",
        },
        {
            "id": "event_teacher",
            "label": "Event Lead Teacher",
            "description": "Designated lead for event execution.",
        },
        {
            "id": "pending",
            "label": "Pending / Unassigned",
            "description": "Awaiting role verification and access approval.",
        },
    ]

    CATEGORIES = {
        "Events Planning & Approvals": [
            "event:create",
            "event:edit",
            "event:patch",
            "event:delete",
            "event:clone",
            "event:propose",
            "event:submit",
            "event:review",
            "event:publish",
            "event:view_draft",
            "event:audience_edit",
            "event:audience_predict",
        ],
        "Resources & Pricing": [
            "resource:create",
            "resource:view",
            "resource:read",
            "resource:edit",
            "resource:update",
            "resource:delete",
            "resource:price",
            "resource:set_cost",
            "resource_type:create",
            "resource_type:read",
        ],
        "Enrollments & Roster": [
            "enrollment:request",
            "enrollment:parent_approve",
            "enrollment:teacher_approve",
            "enrollment:cancel",
            "enrollment:view_roster",
            "enrollment:read",
        ],
        "Billing & Invoices": [
            "billing:invoice",
            "billing:pay",
            "billing:refund",
            "billing:audit",
            "billing:view_payment",
            "subsidy:manage",
        ],
        "Health & Safety": ["health:view", "health:manage", "health:manage_child", "safety:manage"],
        "Academic & Directory": [
            "school:write",
            "school:read",
            "level:create",
            "level:manage",
            "level:read",
            "class:create",
            "class:edit",
            "class:update",
            "class:read",
            "class:assign_teacher",
            "user:create",
            "user:invite",
            "user:delete",
            "user:link",
            "user:view",
            "user:read",
            "teacher:create",
            "teacher:read",
            "teacher:write",
            "parent:read",
            "student:create",
            "student:read",
            "student:view_linked",
        ],
        "Announcements & Feedback": [
            "announcement:manage",
            "notification:send",
            "notification:read",
            "notification:mark_read",
            "feedback:view",
            "feedback:create",
        ],
    }

    return {
        "composite_roles": COMPOSITE_ROLES,
        "composite_role_permissions": {k: list(v) for k, v in COMPOSITE_ROLE_PERMISSIONS.items()},
        "categories": CATEGORIES,
    }
