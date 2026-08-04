"""Pydantic schemas — request bodies and response models."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


# =============================================================================
# Auth schemas
# =============================================================================
class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_id: str | None = "tenant_a"
    role: str | None = "student"
    invite_code: str | None = None


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_id: str | None = "tenant_a"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# =============================================================================
# Level schemas
# =============================================================================
class LevelCreateRequest(BaseModel):
    name: str


class LevelResponse(BaseModel):
    level_id: int
    name: str


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


class StudentCreateRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    class_id: int
    gender: str | None = None
    birth_data: str | None = None


class StudentResponse(BaseModel):
    id: int
    name: str
    class_id: int
    gender: str | None = None
    birth_data: str | None = None
    email: str
    class_name: str | None = None
    created_at: datetime
    parents: list[ParentResponse] = []


class StudentParentLinkRequest(BaseModel):
    student_id: int
    parent_id: int


# =============================================================================
# Class schemas
# =============================================================================
class ClassCreateRequest(BaseModel):
    name: str
    level_id: int
    head_teacher_id: int


class ClassResponse(BaseModel):
    id: int
    name: str
    level_id: int
    head_teacher_id: int
    teacher_name: str
    teacher_email: str
    level_name: str
    created_at: datetime


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

from pydantic import Field


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


class FinalDecision(BaseModel):
    decision: Literal["publish", "return_to_finance"]
    reason: str | None = None


class PublishedEventOut(BaseModel):
    id: int
    title: str
    description: str
    address: str | None
    date: datetime
    class_mappings: list[ClassMappingResponse] = []

