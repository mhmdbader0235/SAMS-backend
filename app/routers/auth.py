"""Auth router — registration, login, and current-user endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer

from ..dependencies import CurrentUser, get_current_user
from ..schemas import TokenResponse, UserLoginRequest, UserRegisterRequest
from ..services.auth_service import AuthService
from ..database import get_control_plane_pool, get_db_pool
from ..repositories.control_plane_repository import ControlPlaneRepository
from ..repositories.user_repository import UserRepository
from ..repositories.tenant_repository import TenantRepository
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_security = HTTPBearer(auto_error=False)


@router.get("/tenants", summary="List available tenant IDs")
async def list_tenants() -> dict:
    """Return the list of registered tenant (school) identifiers."""
    try:
        tenants = await AuthService.list_tenants()
        return {"tenants": [t["tenant_id"] for t in tenants]}
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
        )
        return TokenResponse(access_token=token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
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

        if current_user.role == "parent":
            cp_pool = await get_control_plane_pool()
            cp_repo = ControlPlaneRepository(cp_pool)
            profile = await cp_repo.get_parent_by_email(current_user.email)
            if profile:
                pool = await get_db_pool(current_user.tenant_id)
                tenant_repo = TenantRepository(pool)
                linked_students = await tenant_repo.get_linked_students_for_parent(current_user.id)
                students = [
                    ProfileStudentInfo(name=s["name"], email=s["email"])
                    for s in linked_students
                ]
        else:
            pool = await get_db_pool(current_user.tenant_id)
            user_repo = UserRepository(pool)
            profile = await user_repo.get_user_profile(current_user.id)
            if current_user.role == "student":
                tenant_repo = TenantRepository(pool)
                student_info = await tenant_repo.get_student_by_id(current_user.id)
                if student_info:
                    class_id = student_info.get("class_id")
                    class_name = student_info.get("class_name")
                # Fetch linked parent
                parent_info = await tenant_repo.get_parent_for_student(current_user.id)
                if parent_info:
                    parent_name = parent_info.get("name")
                    parent_email = parent_info.get("email")
            elif current_user.role == "teacher":
                from ..services.tenant_service import TenantService
                class_info = await TenantService.get_class_by_head_teacher(current_user.tenant_id, current_user.id)
                if class_info:
                    class_id = class_info.get("id")
                    class_name = f"{class_info.get('name')} ({class_info.get('level_name')})"
            
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
            
        return ProfileResponse(
            email=profile["email"],
            phone=profile.get("phone"),
            address=profile.get("address"),
            class_id=class_id,
            class_name=class_name,
            parent_name=parent_name,
            parent_email=parent_email,
            students=students
        )
    except HTTPException:
        raise
    except Exception as exc:
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
