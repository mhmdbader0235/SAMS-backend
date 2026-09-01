"""Pydantic schemas — request bodies and response models."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# =============================================================================
# Auth schemas
# =============================================================================
class UserRoleUpdateRequest(BaseModel):
    role: str


class UserSummaryResponse(BaseModel):
    id: int | str
    email: str
    role: str
    created_at: datetime | None = None


class UserRegisterRequest(BaseModel):
    # tenant_id defaults to None, not "tenant_a". "tenant_a" is the first real
    # school, so an omitted tenant_id used to mean "register this person into
    # school A" -- with realm registrationAllowed=true and the hardcoded fallback
    # passphrases in AuthService.register_user, that was a public path into a live
    # tenant. Registration now requires either an explicit tenant or an invitation
    # that names one.
    email: EmailStr
    password: str
    tenant_id: str | None = None
    role: str | None = "student"
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    invite_code: str | None = None


class UserLoginRequest(BaseModel):
    # Also None rather than "tenant_a": omitting it means "resolve my tenant",
    # which AuthService.login_user does from user_tenant_map. Defaulting to
    # tenant_a instead SKIPPED that resolution and authenticated the caller
    # against school A's users table.
    email: EmailStr
    password: str
    tenant_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# =============================================================================
# Level schemas
# =============================================================================
class LevelResponse(BaseModel):
    level_id: int
    name: str
    isced_level: int | None = None
    age_band_min: int | None = None
    age_band_max: int | None = None
    ordinal: int | None = None
    is_active: bool = True


class LevelCreateRequest(BaseModel):
    name: str
    isced_level: int | None = None
    age_band_min: int | None = None
    age_band_max: int | None = None
    ordinal: int | None = None
    is_active: bool = True


class LevelUpdateRequest(BaseModel):
    name: str | None = None
    isced_level: int | None = None
    age_band_min: int | None = None
    age_band_max: int | None = None
    ordinal: int | None = None
    is_active: bool | None = None


# =============================================================================
# Teacher / Parent / Student schemas
# =============================================================================
class TeacherResponse(BaseModel):
    id: int
    name: str
    email: str


class TeacherCreateRequest(BaseModel):
    email: EmailStr
    password: str
    name: str


class ParentResponse(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None = None
    # Only populated when this parent is embedded under a specific student
    # (StudentResponse.parents) -- the standalone parent directory has no
    # single student context for these to describe, so they default out.
    relationship_type: str | None = None
    is_primary_contact: bool = False
    can_approve: bool = True


class StudentCreateRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    class_id: int | None = None
    gender: str | None = None
    birth_data: str | None = None


class StudentReassignClassRequest(BaseModel):
    class_id: int | None = None


class StudentBulkEnrollRequest(BaseModel):
    student_ids: list[int]
    # Required, unlike the single-student reassign endpoint: bulk-enroll has
    # no "unassign a cohort" use case in the product, so a request that omits
    # class_id (or sends it null) is almost always a client bug -- reject it
    # with 422 instead of silently clearing class_id for every listed student.
    class_id: int


class StudentResponse(BaseModel):
    id: int
    name: str
    class_id: int | None = None
    gender: str | None = None
    birth_data: str | None = None
    email: str
    class_name: str | None = None
    created_at: datetime | None = None
    parents: list[ParentResponse] = []


class StudentParentLinkRequest(BaseModel):
    student_id: int
    parent_id: int
    relationship_type: str | None = None
    is_primary_contact: bool = False
    # Defaults True to match today's implicit behavior (any linked parent
    # can approve/pay/cancel) -- set False only to deliberately restrict a
    # contact (e.g. non-custodial) from approving trips for this student.
    can_approve: bool = True


# =============================================================================
# Class schemas
# =============================================================================
class ClassCreateRequest(BaseModel):
    name: str
    level_id: int
    head_teacher_id: int | None = None
    capacity: int = Field(default=25, gt=0)
    is_active: bool = True


class ClassUpdateRequest(BaseModel):
    name: str | None = None
    level_id: int | None = None
    head_teacher_id: int | None = None
    capacity: int | None = Field(default=None, gt=0)
    is_active: bool | None = None


# =============================================================================
# Academic Year / Rollover schemas (ADR 0002 / ADR 0003)
# =============================================================================
class AcademicYearCreateRequest(BaseModel):
    name: str
    start_date: date | None = None
    end_date: date | None = None


class RolloverCreateRequest(BaseModel):
    from_year_id: int
    to_year_id: int


class RolloverLineOverrideRequest(BaseModel):
    # Explicit None means "clear the override, defer back to the engine's
    # proposed_action" -- omitting the field entirely is not meaningful here
    # since an override always names an action or clears one.
    override_action: str | None = None
    to_level_id: int | None = None
    to_section_label: str | None = None


class ClassResponse(BaseModel):
    id: int
    name: str
    level_id: int
    head_teacher_id: int | None = None
    capacity: int = 25
    is_active: bool = True
    teacher_name: str | None = None
    teacher_email: str | None = None
    level_name: str | None = None
    student_count: int = 0
    created_at: datetime | None = None


# =============================================================================
# Event & Mapping schemas
# =============================================================================
class ClassMappingRequest(BaseModel):
    class_id: int
    ticket_price: float = 0.0


class ClassMappingResponse(BaseModel):
    id: int
    class_id: int
    ticket_price: float
    class_name: str | None = None
    level_name: str | None = None
    student_count: int | None = 0


class TicketPriceUpdate(BaseModel):
    class_map_id: int
    ticket_price: float


class EventCreateRequest(BaseModel):
    title: str
    description: str | None = ""
    address: str | None = ""
    school_subsidy: float = 0.0
    date: datetime
    class_mappings: list[ClassMappingRequest] = []


class EventResponse(BaseModel):
    id: int
    title: str
    description: str
    address: str | None = None
    event_map_id: int | None = None
    school_subsidy: float | None = None
    date: datetime
    created_by: int
    created_at: datetime | None = None
    class_mappings: list[ClassMappingResponse] = []
    status: str = "draft"
    predicted_attendance: int | None = None
    manager_reviewer_id: int | None = None
    finance_reviewer_id: int | None = None
    total_cost: float | None = None
    submitted_at: datetime | None = None
    manager_approved_at: datetime | None = None
    finance_priced_at: datetime | None = None
    published_at: datetime | None = None
    rejection_reason: str | None = None


class EventsListResponse(BaseModel):
    events: list[EventResponse]


# =============================================================================
# Enrollment schemas
# =============================================================================
class EnrollmentCreateRequest(BaseModel):
    student_id: int
    event_class_map_id: int


class EnrollmentStateUpdateRequest(BaseModel):
    state: str  # approved_by_parent, approved_by_teacher, rejected_by_parent, rejected_by_teacher


class EnrollmentResponse(BaseModel):
    id: int
    student_id: int
    event_class_map_id: int
    state: str
    teacher_id: int | None
    parent_id: int | None
    student_name: str | None = None
    student_email: str | None = None
    class_name: str | None = None
    event_title: str | None = None
    ticket_price: float | None = 0.0
    created_at: datetime


# =============================================================================
# Payment schemas
# =============================================================================
class PaymentResponse(BaseModel):
    id: int
    enrollment_id: int
    amount: float
    status: str
    created_at: datetime


# =============================================================================
# Feedback schemas
# =============================================================================
class FeedbackCreateRequest(BaseModel):
    rating: int
    comments: str | None = None


class FeedbackResponse(BaseModel):
    id: int
    event_id: int
    user_id: int
    rating: int
    comments: str | None
    created_at: datetime
    user_name: str | None = None


# =============================================================================
# Student Health (PII) schemas
# =============================================================================
class StudentHealthCreateRequest(BaseModel):
    national_id: str
    medical_conditions: str
    emergency_contact: str


class StudentHealthResponse(BaseModel):
    id: UUID
    student_id: int
    national_id: str
    medical_conditions: str
    emergency_contact: str
    is_masked: bool


# =============================================================================
# Event Workflow & Resource schemas
# =============================================================================
from typing import Literal


class ResourceTypeResponse(BaseModel):
    id: int
    name: str
    category: str
    is_custom: bool
    created_by_user_id: int | None
    is_active: bool
    created_at: datetime


class ResourceTypeCreateRequest(BaseModel):
    name: str
    category: str


class ResourceLineIn(BaseModel):
    resource_type_id: int
    description: str | None = None
    quantity: int = Field(gt=0)


class ResourceLineResponse(BaseModel):
    id: int
    resource_type_id: int
    resource_type_name: str
    resource_type_category: str
    description: str | None
    quantity: int
    added_by_user_id: int
    updated_by_user_id: int | None
    unit_price: float
    total_cost: float
    set_by_user_id: int | None


class ResourceSummaryResponse(BaseModel):
    event_id: int
    resources: list[ResourceLineResponse]
    total_cost: float
    currency: str


class ResourceCostIn(BaseModel):
    unit_price: float = Field(ge=0.0)
    currency: str = "JOD"


class ManagerDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = None


class PublishedEventOut(BaseModel):
    id: int
    title: str
    description: str
    address: str | None
    date: datetime
    class_mappings: list[ClassMappingResponse] = []


# =============================================================================
# Structure Setup Schemas
# =============================================================================
class SpineSectionSchema(BaseModel):
    # id is the stable identity a save must match against -- None means
    # "this section doesn't exist yet, create it". Matching by name/ordinal
    # instead (the old behavior) is what let a rename silently orphan the
    # original row instead of updating it.
    id: int | None = None
    name: str
    capacity: int = Field(gt=0)


class SpineLevelSchema(BaseModel):
    # Same rationale as SpineSectionSchema.id.
    level_id: int | None = None
    name: str
    isced_level: int | None = None
    age_band_min: int | None = None
    age_band_max: int | None = None
    ordinal: int | None = None
    is_active: bool = True
    sections: list[SpineSectionSchema] = []


class AcademicSettingsSchema(BaseModel):
    academic_year: str
    start_month: int
    weekend_days: list[str] = []


class BlackoutDateSchema(BaseModel):
    date: str
    title: str
    tags: list[str] = []


class StructureSetupRequest(BaseModel):
    system: str
    levels: list[SpineLevelSchema]
    calendar: AcademicSettingsSchema
    blackout_dates: list[BlackoutDateSchema]


# =============================================================================
# Grades + Classes bulk import (CSV/XLSX)
# =============================================================================
class StructureImportRowResult(BaseModel):
    row_number: int  # 1-based, matches the row as seen in a spreadsheet (header = row 1)
    grade_name: str
    class_name: str | None = None
    action: str  # e.g. "create_grade", "update_grade+create_class", "skipped"
    status: str  # "valid" | "error"
    errors: list[str] = []
    warnings: list[str] = []


class StructureImportPreviewResponse(BaseModel):
    filename: str
    total_rows: int
    valid_rows: int
    error_rows: int
    rows: list[StructureImportRowResult]


class StructureImportCommitResponse(BaseModel):
    filename: str
    total_rows: int
    applied_rows: int
    skipped_rows: int
    created_grades: int
    updated_grades: int
    created_classes: int
    updated_classes: int
    row_results: list[StructureImportRowResult]


class ResourceLineUpdate(BaseModel):
    """All-optional counterpart to ResourceLineIn for PATCH, so changing just
    the quantity of a resource line doesn't require resending every field."""

    resource_type_id: int | None = None
    description: str | None = None
    quantity: int | None = Field(default=None, gt=0)


# =============================================================================
# School Setup (Day-1 Onboarding) Schemas
# =============================================================================
class SchoolCampusSchema(BaseModel):
    id: int | None = None
    name: str | None = None
    address_line1: str | None = None
    area: str | None = None
    city: str | None = None
    state_region: str | None = None
    country: str | None = None
    po_box: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    day_start: str | None = None
    day_end: str | None = None
    access_notes: str | None = None
    accessibility_notes: str | None = None
    is_primary: bool = True


class SchoolContactSchema(BaseModel):
    id: int | None = None
    role_title: str
    name: str
    phone: str | None = None
    email: str | None = None
    is_emergency_contact: bool = False
    escalation_order: int | None = None
    visible_to: list[str] = ["staff"]


class SchoolProfileUpdateRequest(BaseModel):
    legal_name: str | None = None
    display_name: str | None = None
    school_code: str | None = None
    school_type: str | None = None
    regulator: str | None = None
    licence_number: str | None = None
    licence_expiry: date | None = None
    tax_registration: str | None = None
    country: str | None = None
    timezone: str | None = None
    hemisphere: str | None = None
    default_language: str | None = None
    additional_languages: list[str] | None = None
    currency: str | None = None
    logo_url: str | None = None
    logo_dark_url: str | None = None
    primary_color: str | None = None
    website: str | None = None


class SchoolProfileResponse(BaseModel):
    legal_name: str | None = None
    display_name: str | None = None
    school_code: str | None = None
    school_type: str | None = None
    regulator: str | None = None
    licence_number: str | None = None
    licence_expiry: date | None = None
    tax_registration: str | None = None
    country: str | None = None
    timezone: str | None = None
    hemisphere: str | None = None
    default_language: str | None = None
    additional_languages: list[str] = []
    currency: str = "JOD"
    logo_url: str | None = None
    logo_dark_url: str | None = None
    primary_color: str | None = None
    website: str | None = None
    profile_committed_at: datetime | None = None
    structure_committed_at: datetime | None = None
    curriculum_locked_at: datetime | None = None
    activated_at: datetime | None = None
    campuses: list[SchoolCampusSchema] = []
    contacts: list[SchoolContactSchema] = []


class SchoolSetupStateResponse(BaseModel):
    status: str
    steps: dict
    blocking: list[str] = []
    warnings: list[str] = []
    activated_at: datetime | None = None
