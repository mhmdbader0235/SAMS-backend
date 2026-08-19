"""Students and Classes router."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database import get_db_pool
from app.core.dependencies import CurrentUser, get_current_user, require_tenant_live
from app.core.schemas import (
    ClassCreateRequest,
    ClassResponse,
    ClassUpdateRequest,
    EnrollmentCreateRequest,
    EnrollmentResponse,
    EnrollmentStateUpdateRequest,
    LevelCreateRequest,
    LevelResponse,
    LevelUpdateRequest,
    ParentResponse,
    StructureSetupRequest,
    StudentBulkEnrollRequest,
    StudentCreateRequest,
    StudentHealthCreateRequest,
    StudentHealthResponse,
    StudentParentLinkRequest,
    StudentReassignClassRequest,
    StudentResponse,
    TeacherCreateRequest,
    TeacherResponse,
)
from app.domains.tenant.service import TenantService
from app.domains.tenant.tenant_repository import TenantRepository, parse_id
from app.domains.tenant.user_repository import UserRepository

router = APIRouter(prefix="/api/v1/students", tags=["students"])
# Everything except the structure endpoints below is off-limits until the
# tenant has completed Day-1 setup (see app/domains/school/) -- those two
# endpoints ARE the mechanism by which setup gets completed, so they must
# stay reachable while the tenant is still in "setup" status.
router_gated = APIRouter(prefix="/api/v1/students", tags=["students"], dependencies=[Depends(require_tenant_live)])


# =============================================================================
# Levels
# =============================================================================
@router_gated.post("/levels", response_model=LevelResponse, summary="Create a school level (staff only)")
async def create_level(
    payload: LevelCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> LevelResponse:
    try:
        level_id = await TenantService.create_level(
            tenant_id=current_user.tenant_id,
            name=payload.name,
            user_role=current_user.roles,
        )
        return LevelResponse(
            level_id=level_id,
            name=payload.name,
            isced_level=payload.isced_level,
            age_band_min=payload.age_band_min,
            age_band_max=payload.age_band_max,
            ordinal=payload.ordinal,
            is_active=payload.is_active
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get("/levels", response_model=list[LevelResponse], summary="List all school levels")
async def list_levels(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[LevelResponse]:
    if not (current_user.has_any_role("school_admin", "super_admin", "manager", "teacher") or current_user.has_role("level:read")):
        raise HTTPException(status_code=403, detail="Forbidden: Students and unauthorized users cannot list school levels")
    try:
        results = await TenantService.get_all_levels(current_user.tenant_id)
        return [LevelResponse(**r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.put("/levels/{level_id}", response_model=LevelResponse, summary="Update level details")
async def update_level(
    level_id: int,
    payload: LevelUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> LevelResponse:
    try:
        res = await TenantService.update_level(
            tenant_id=current_user.tenant_id,
            level_id=level_id,
            name=payload.name,
            isced_level=payload.isced_level,
            age_band_min=payload.age_band_min,
            age_band_max=payload.age_band_max,
            ordinal=payload.ordinal,
            is_active=payload.is_active,
            user_role=current_user.roles
        )
        return LevelResponse(**res)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.delete("/levels/{level_id}", summary="Delete school level")
async def delete_level(
    level_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        await TenantService.delete_level(
            tenant_id=current_user.tenant_id,
            level_id=level_id,
            user_role=current_user.roles
        )
        return {"status": "ok", "message": f"Level {level_id} deleted successfully"}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


from app.core.schemas import StructureSetupRequest

@router.get("/structure", summary="Get current Academic Structure and Calendar")
async def get_academic_structure(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        tenant_id = current_user.tenant_id or "tenant_a"
        return await TenantService.get_academic_structure(
            tenant_id=tenant_id,
            user_role=current_user.roles,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/structure/setup", summary="Save Academic Structure and Calendar (admin only)")
async def setup_academic_structure(
    payload: StructureSetupRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        tenant_id = current_user.tenant_id or "tenant_a"
        await TenantService.save_academic_structure(
            tenant_id=tenant_id,
            payload=payload.model_dump(),
            user_role=current_user.roles,
        )
        return {"status": "ok", "message": "Academic structure configured successfully"}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



# =============================================================================
# Teachers / Parents List
# =============================================================================
@router_gated.post("/teachers", response_model=TeacherResponse, summary="Create a teacher profile (staff only)")
async def create_teacher(
    payload: TeacherCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> TeacherResponse:
    try:
        teacher_id = await TenantService.create_teacher(
            tenant_id=current_user.tenant_id,
            email=payload.email,
            password=payload.password,
            name=payload.name,
            user_role=current_user.roles,
        )
        return TeacherResponse(
            id=teacher_id,
            name=payload.name,
            email=payload.email
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


from pydantic import BaseModel


class StaffUserCreateRequest(BaseModel):
    email: str
    password: str

class StaffUserResponse(BaseModel):
    id: int
    email: str
    role: str


@router_gated.post("/managers", response_model=StaffUserResponse, summary="Create a manager user (admin only)")
async def create_manager(
    payload: StaffUserCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> StaffUserResponse:
    if not (current_user.has_role("school_admin") or current_user.has_role("super_admin")):
        raise HTTPException(status_code=403, detail="Only school_admin or super_admin can create managers")
    try:
        user_id = await TenantService.create_staff_user(
            tenant_id=current_user.tenant_id,
            email=payload.email,
            password=payload.password,
            role="manager",
            user_role=current_user.roles,
        )
        return StaffUserResponse(id=user_id, email=payload.email, role="manager")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.post("/finance", response_model=StaffUserResponse, summary="Create a finance user (admin only)")
async def create_finance(
    payload: StaffUserCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> StaffUserResponse:
    if not (current_user.has_role("school_admin") or current_user.has_role("super_admin")):
        raise HTTPException(status_code=403, detail="Only school_admin or super_admin can create finance users")
    try:
        user_id = await TenantService.create_staff_user(
            tenant_id=current_user.tenant_id,
            email=payload.email,
            password=payload.password,
            role="finance",
            user_role=current_user.roles,
        )
        return StaffUserResponse(id=user_id, email=payload.email, role="finance")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get("/teachers", response_model=list[TeacherResponse], summary="List all teachers")
async def list_teachers(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[TeacherResponse]:
    if not (current_user.has_any_role("school_admin", "super_admin", "manager", "teacher") or current_user.has_role("teacher:read")):
        raise HTTPException(status_code=403, detail="Forbidden: Students and unauthorized users cannot list teachers")
    try:
        results = await TenantService.get_all_teachers(current_user.tenant_id)
        return [TeacherResponse(**r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get("/parents", response_model=list[ParentResponse], summary="List all parents")
async def list_parents(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ParentResponse]:
    if not (current_user.has_any_role("school_admin", "super_admin", "manager") or current_user.has_role("parent:read")):
        raise HTTPException(status_code=403, detail="Forbidden: Students and unauthorized users cannot list parents")
    try:
        results = await TenantService.get_all_parents(current_user.tenant_id)
        return [ParentResponse(**r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# Student Profiles
# =============================================================================
@router_gated.post("", response_model=StudentResponse, summary="Create a student profile (staff only)")
async def create_student(
    payload: StudentCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> StudentResponse:
    try:
        student_id = await TenantService.create_student(
            tenant_id=current_user.tenant_id,
            email=payload.email,
            password=payload.password,
            name=payload.name,
            class_id=payload.class_id,
            gender=payload.gender,
            birth_data=payload.birth_data,
            user_role=current_user.roles,
        )
        # Fetch student details
        s_info = await TenantService.get_student_by_id(current_user.tenant_id, student_id)
        return StudentResponse(**s_info)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get("", response_model=list[StudentResponse], summary="List all students")
async def list_students(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[StudentResponse]:
    if not (current_user.has_any_role("school_admin", "super_admin", "manager", "teacher") or current_user.has_role("student:read")):
        raise HTTPException(status_code=403, detail="Forbidden: Students and unauthorized users cannot list all students")
    try:
        results = await TenantService.get_all_students(current_user.tenant_id)
        return [StudentResponse(**r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.post("/link-parent", summary="Link parent and student (staff only)")
async def link_parent_student(
    payload: StudentParentLinkRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        await TenantService.link_student_parent(
            tenant_id=current_user.tenant_id,
            student_id=payload.student_id,
            parent_id=payload.parent_id,
            user_role=current_user.roles,
            user_id=current_user.id,
        )
        return {"status": "ok", "message": "Student linked to parent"}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get("/linked", response_model=list[StudentResponse], summary="List linked students for current parent")
async def list_linked_students(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[StudentResponse]:
    if not current_user.has_role("parent"):
        raise HTTPException(status_code=403, detail="Only parents can view their linked children")
    try:
        pool = await get_db_pool(current_user.tenant_id)
        user_repo = UserRepository(pool)
        local_user = None
        if current_user.email:
            local_user = await user_repo.get_user_by_email(current_user.email)
        
        target_id = local_user["id"] if local_user else parse_id(current_user.id)
        results = await TenantService.get_linked_students_for_parent(current_user.tenant_id, target_id)
        return [StudentResponse(**r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# Classes
# =============================================================================
@router_gated.post("/classes", response_model=ClassResponse, summary="Create a class (staff only)")
async def create_class(
    payload: ClassCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> ClassResponse:
    try:
        class_id = await TenantService.create_class(
            tenant_id=current_user.tenant_id,
            name=payload.name,
            level_id=payload.level_id,
            head_teacher_id=payload.head_teacher_id,
            capacity=payload.capacity,
            user_role=current_user.roles,
        )
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        c_info = await repo.get_class_by_id(class_id)
        return ClassResponse(**c_info)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get("/classes", response_model=list[ClassResponse], summary="List all classes")
async def list_classes(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ClassResponse]:
    if not (current_user.has_any_role("school_admin", "super_admin", "manager", "teacher") or current_user.has_role("class:read")):
        raise HTTPException(status_code=403, detail="Forbidden: Students and unauthorized users cannot list classes")
    try:
        results = await TenantService.get_all_classes(current_user.tenant_id)
        return [ClassResponse(**r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.put("/classes/{class_id}", response_model=ClassResponse, summary="Update class details")
async def update_class(
    class_id: int,
    payload: ClassUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> ClassResponse:
    if not current_user.has_any_role("school_admin", "super_admin", "teacher"):
        raise HTTPException(status_code=403, detail="Only staff can update classes")
    try:
        updated = await TenantService.update_class(
            tenant_id=current_user.tenant_id,
            class_id=class_id,
            name=payload.name,
            level_id=payload.level_id,
            head_teacher_id=payload.head_teacher_id,
            capacity=payload.capacity,
            user_role=current_user.roles,
        )
        return ClassResponse(**updated)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.delete("/classes/{class_id}", summary="Delete a class")
async def delete_class(
    class_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        await TenantService.delete_class(
            tenant_id=current_user.tenant_id,
            class_id=class_id,
            user_role=current_user.roles,
        )
        return {"status": "ok", "message": f"Class {class_id} deleted successfully"}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get("/classes/{class_id}/students", summary="List students in a class")
async def get_class_students(
    class_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    try:
        return await TenantService.get_students_for_class(current_user.tenant_id, class_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.put("/students/{student_id}/class", summary="Reassign student to class")
async def reassign_student_class(
    student_id: int,
    payload: StudentReassignClassRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        await TenantService.reassign_student_class(
            tenant_id=current_user.tenant_id,
            student_id=student_id,
            new_class_id=payload.class_id,
            user_role=current_user.roles,
        )
        return {"status": "ok", "message": f"Student {student_id} reassigned successfully"}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.post("/students/bulk-enroll", summary="Bulk enroll/reassign students to a class section")
async def bulk_reassign_students(
    payload: StudentBulkEnrollRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        count = await TenantService.bulk_reassign_students(
            tenant_id=current_user.tenant_id,
            student_ids=payload.student_ids,
            new_class_id=payload.class_id,
            user_role=current_user.roles,
        )
        return {"status": "ok", "enrolled_count": count, "message": f"Successfully enrolled {count} students."}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# Enrollments
# =============================================================================
@router_gated.post("/enrollments", response_model=EnrollmentResponse, summary="Enroll student in event class map")
async def enroll_student(
    payload: EnrollmentCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> EnrollmentResponse:
    state = "requested_by_student"
    parent_id = None
    teacher_id = None

    if current_user.has_role("parent"):
        # Check if student is linked to the parent
        parent_id = parse_id(current_user.id)
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        linked = await repo.is_student_linked_to_parent(payload.student_id, parent_id)
        if linked:
            state = "approved_by_parent"
        elif current_user.has_role("teacher"):
            state = "approved_by_teacher"
            teacher_id = parse_id(current_user.id)
            parent_id = None
        else:
            raise HTTPException(status_code=403, detail="Parent is not linked to this student")
    elif current_user.has_role("teacher"):
        state = "approved_by_teacher"
        teacher_id = parse_id(current_user.id)

    try:
        enroll_id = await TenantService.enroll_student(
            tenant_id=current_user.tenant_id,
            student_id=payload.student_id,
            event_class_map_id=payload.event_class_map_id,
            state=state,
            teacher_id=teacher_id,
            parent_id=parent_id,
        )
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        details = await repo.get_enrollment_by_id(enroll_id)
        return EnrollmentResponse(**details)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get("/enrollments", response_model=list[EnrollmentResponse], summary="Get enrollments for current user")
async def get_enrollments(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[EnrollmentResponse]:
    try:
        results = await TenantService.get_enrollments_for_user(
            current_user.tenant_id, current_user.id, current_user.roles
        )
        return [EnrollmentResponse(**r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.post("/enrollments/{enrollment_id}/approve", response_model=EnrollmentResponse, summary="Approve/Reject enrollment")
async def update_enrollment_approval(
    enrollment_id: int,
    payload: EnrollmentStateUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> EnrollmentResponse:
    teacher_id = None
    parent_id = None

    pool = await get_db_pool(current_user.tenant_id)
    repo = TenantRepository(pool)
    details = await repo.get_enrollment_by_id(enrollment_id)
    if not details:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    current_state = details["state"]

    is_parent_decision = False
    if current_user.has_role("parent"):
        parent_id = parse_id(current_user.id)
        linked = await repo.is_student_linked_to_parent(details["student_id"], parent_id)
        if linked:
            is_parent_decision = True

    if is_parent_decision:
        parent_id = parse_id(current_user.id)
        if current_state != "requested_by_student":
            raise HTTPException(
                status_code=400,
                detail=f"Parent cannot approve/reject an enrollment in '{current_state}' state"
            )
        if payload.state not in ("approved_by_parent", "rejected_by_parent"):
            raise HTTPException(
                status_code=400,
                detail="Parent can only transition enrollment to approved_by_parent or rejected_by_parent"
            )
    elif current_user.has_role("teacher"):
        teacher_id = parse_id(current_user.id)
        if current_state == "requested_by_student":
            raise HTTPException(
                status_code=400,
                detail="Enrollment must be approved by a parent before teacher approval"
            )
        if current_state != "approved_by_parent":
            raise HTTPException(
                status_code=400,
                detail=f"Teacher cannot approve/reject an enrollment in '{current_state}' state"
            )
        if payload.state not in ("approved_by_teacher", "rejected_by_teacher"):
            raise HTTPException(
                status_code=400,
                detail="Teacher can only transition enrollment to approved_by_teacher or rejected_by_teacher"
            )
    else:
        raise HTTPException(status_code=403, detail="Unauthorized role for approval")

    try:
        await TenantService.update_enrollment_state(
            tenant_id=current_user.tenant_id,
            enrollment_id=enrollment_id,
            state=payload.state,
            teacher_id=teacher_id,
            parent_id=parent_id,
        )
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        details = await repo.get_enrollment_by_id(enrollment_id)
        return EnrollmentResponse(**details)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.delete("/enrollments/{enrollment_id}", summary="Cancel/Unenroll an enrollment")
async def cancel_enrollment(
    enrollment_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    if not current_user.tenant_id and not current_user.has_role("super_admin"):
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        await TenantService.cancel_enrollment(
            tenant_id=current_user.tenant_id or "tenant_a",
            enrollment_id=enrollment_id,
            user_id=current_user.id,
            user_role=current_user.roles,
        )
        return {"status": "ok", "message": "Enrollment successfully cancelled"}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# PII Student Health & Records
# =============================================================================
@router_gated.post("/{student_id}/health", summary="Create or update student health records (staff only)")
async def create_or_update_health(
    student_id: int,
    payload: StudentHealthCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        rec_id = await TenantService.create_or_update_health_record(
            tenant_id=current_user.tenant_id,
            student_id=student_id,
            national_id=payload.national_id,
            medical_conditions=payload.medical_conditions,
            emergency_contact=payload.emergency_contact,
            requesting_user_id=current_user.id,
            requesting_user_role=current_user.roles,
        )
        return {"status": "success", "health_record_id": str(rec_id)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router_gated.get("/{student_id}/health", response_model=StudentHealthResponse, summary="Get student health records (staff only)")
async def get_health(
    student_id: int,
    elevated_clearance: bool = Query(False, description="school_admin elevated clearance check"),
    current_user: CurrentUser = Depends(get_current_user),
) -> StudentHealthResponse:
    try:
        record = await TenantService.get_health_record(
            tenant_id=current_user.tenant_id,
            student_id=student_id,
            requesting_user_id=current_user.id,
            requesting_user_role=current_user.roles,
            elevated_clearance=elevated_clearance,
        )
        if not record:
            raise HTTPException(status_code=404, detail="Health record not found")
        return StudentHealthResponse(**record)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

