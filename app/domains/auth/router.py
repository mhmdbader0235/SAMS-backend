"""Auth router — registration, login, and current-user endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from pydantic import BaseModel

from app.core.database import get_control_plane_pool, get_db_pool
from app.core.dependencies import CurrentUser, get_current_user
from app.core.schemas import TokenResponse, UserLoginRequest, UserRegisterRequest
from app.domains.auth.service import AuthService
from app.domains.tenant.control_plane_repository import ControlPlaneRepository
from app.domains.tenant.tenant_repository import TenantRepository
from app.domains.tenant.user_repository import UserRepository

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
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


@router.post("/tenants", summary="Create a new tenant and generate schema (Super Admin)")
async def create_tenant(
    payload: CreateTenantRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Register a new tenant in control plane and generate its PostgreSQL schema & tables."""
    if current_user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only super_admin can create new tenants")
    try:
        res = await AuthService.create_tenant(payload.tenant_id, payload.name)
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/invitations", summary="Create a role- & tenant-scoped invitation token")
async def create_invitation(
    payload: CreateInvitationRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Generate a locked invitation code for a specific tenant and role."""
    allowed_roles = {"school_admin", "super_admin"}
    if current_user.role not in allowed_roles and "school_admin" not in current_user.roles and "super_admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="Only school_admin or super_admin can create invitations")

    # Strict Tenant Scoping: non-super_admin can only create invitations for their own tenant
    is_super = current_user.role == "super_admin" or "super_admin" in (current_user.roles or [])
    target_tenant = payload.tenant_id
    if not is_super and current_user.tenant_id:
        if payload.tenant_id and payload.tenant_id.strip().lower() != current_user.tenant_id.strip().lower():
            raise HTTPException(
                status_code=403,
                detail=f"Admins of tenant '{current_user.tenant_id}' cannot create invitations for tenant '{payload.tenant_id}'"
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
        reg_link = f"{frontend_url}/auth?invite_code={inv['code']}&auto_google=true&email={target_em}"
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
                    linked_students = await tenant_repo.get_linked_students_for_parent(local_user["id"])
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
                        class_info = await TenantService.get_class_by_head_teacher(current_user.tenant_id, db_user_id)
                        if class_info:
                            class_id = class_info.get("id")
                            class_name = f"{class_info.get('name')} ({class_info.get('level_name')})"

        email = profile["email"] if (profile and "email" in profile) else (current_user.email or "user@school.com")
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
            students=students
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[get_profile ERROR] {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/profile", summary="Update user profile")
async def update_profile(
    payload: ProfileUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user)
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


@router.get("/parent-profile", response_model=ProfileResponse, summary="Get parent profile by email")
async def get_parent_profile_by_email(
    email: str,
    current_user: CurrentUser = Depends(get_current_user)
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
            email=profile["email"],
            phone=profile["phone"],
            address=profile["address"]
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
