from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.database import get_db_pool
from app.core.dependencies import CurrentUser, get_current_user
from app.core.schemas import (
    EventCreateRequest,
    EventResponse,
    EventsListResponse,
    FeedbackCreateRequest,
    FeedbackResponse,
    FinalDecision,
    ManagerDecision,
    PaymentResponse,
    PublishedEventOut,
    ResourceCostIn,
    ResourceLineIn,
    ResourceSummaryResponse,
    ResourceTypeCreateRequest,
    ResourceTypeResponse,
    TicketPriceUpdate,
)
from app.domains.tenant.service import TenantService
from app.domains.tenant.tenant_repository import TenantRepository, parse_id

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.get("", response_model=EventsListResponse, summary="List all events targeted at user")
async def list_events(
    current_user: CurrentUser = Depends(get_current_user),
) -> EventsListResponse:
    if not current_user.tenant_id and current_user.role != "super_admin":
        raise HTTPException(status_code=400, detail="Tenant context required")

    tenant_id = current_user.tenant_id or "tenant_a"
    try:
        events = await TenantService.get_events_for_user(
            tenant_id=tenant_id,
            user_id=current_user.id,
            user_role=current_user.role,
        )
        if current_user.role not in ("manager", "finance", "school_admin"):
            for ev in events:
                ev["total_cost"] = None
        if current_user.role in ("student", "parent"):
            for ev in events:
                ev["school_subsidy"] = None
        return EventsListResponse(events=[EventResponse(**ev) for ev in events])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("", response_model=EventResponse, summary="Create a new event (staff only)")
async def create_event(
    payload: EventCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> EventResponse:
    if current_user.role not in ("school_admin", "teacher"):
        raise HTTPException(status_code=403, detail="Only staff can create events")

    try:
        mappings = [m.dict() for m in payload.class_mappings]
        event = await TenantService.create_event(
            tenant_id=current_user.tenant_id,
            title=payload.title,
            description=payload.description or "",
            address=payload.address or "",
            school_subsidy=payload.school_subsidy,
            date_val=payload.date,
            created_by=current_user.id,
            class_mappings=mappings,
            user_role=current_user.role,
        )
        return EventResponse(**event)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{event_id}/clone", response_model=EventResponse, summary="Clone an existing event as a template draft")
async def clone_event_endpoint(
    event_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> EventResponse:
    if current_user.role not in ("school_admin", "teacher"):
        raise HTTPException(status_code=403, detail="Only staff can clone events")

    try:
        event = await TenantService.clone_event(
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            created_by_user_id=current_user.id,
            user_role=current_user.role,
        )
        return EventResponse(**event)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an event (staff only)",
)
async def delete_event(
    event_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    if current_user.role not in ("school_admin", "teacher"):
        raise HTTPException(status_code=403, detail="Only staff can delete events")

    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        existing = await repo.get_event_by_id(event_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Event not found")
        await repo.delete_event(event_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))





# =============================================================================
# Payments
# =============================================================================
@router.get("/enrollments/{enrollment_id}/payment", response_model=PaymentResponse, summary="Get payment status for enrollment")
async def get_payment(
    enrollment_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> PaymentResponse:
    try:
        payment = await TenantService.get_payment_for_enrollment(current_user.tenant_id, enrollment_id)
        if not payment:
            raise HTTPException(status_code=404, detail="Payment record not found")
        return PaymentResponse(**payment)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/enrollments/{enrollment_id}/pay", summary="Process payment for event class enrollment")
async def pay_enrollment(
    enrollment_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    if current_user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can make payments")
    try:
        success = await TenantService.pay_enrollment(current_user.tenant_id, enrollment_id)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to process payment")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# Feedback
# =============================================================================
@router.post("/{event_id}/feedbacks", response_model=FeedbackResponse, summary="Leave feedback for event")
async def create_feedback(
    event_id: int,
    payload: FeedbackCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> FeedbackResponse:
    try:
        fb_id = await TenantService.create_event_feedback(
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            user_id=current_user.id,
            rating=payload.rating,
            comments=payload.comments,
        )
        return FeedbackResponse(
            id=fb_id,
            event_id=event_id,
            user_id=parse_id(current_user.id),
            rating=payload.rating,
            comments=payload.comments,
            created_at=datetime.utcnow(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{event_id}/feedbacks", response_model=list[FeedbackResponse], summary="Get feedback list for event")
async def list_feedbacks(
    event_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> list[FeedbackResponse]:
    try:
        results = await TenantService.get_feedback_for_event(current_user.tenant_id, event_id)
        return [FeedbackResponse(**r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# Event Workflow & Resources Static Endpoints (Task 8)
# =============================================================================
from pydantic import BaseModel


class EventPatchRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    address: str | None = None
    date: datetime | None = None


class AudienceSelect(BaseModel):
    class_ids: list[int]


@router.get("/resource-types", response_model=list[ResourceTypeResponse], summary="List active resource types")
async def list_resource_types(
    category: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ResourceTypeResponse]:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        types = await repo.get_all_resource_types(category)
        return [ResourceTypeResponse(**t) for t in types]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/resource-types", response_model=ResourceTypeResponse, status_code=201, summary="Create a custom resource type")
async def create_custom_resource_type(
    payload: ResourceTypeCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> ResourceTypeResponse:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can create custom resource types")
        
    try:
        rt_id = await TenantService.create_resource_type(
            tenant_id=current_user.tenant_id,
            name=payload.name,
            category=payload.category,
            is_custom=True,
            created_by_user_id=current_user.id,
        )
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        rt = await repo.get_resource_type_by_id(rt_id)
        return ResourceTypeResponse(**rt)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/manager-queue", response_model=EventsListResponse, summary="Get events in manager review queue")
async def get_manager_queue(
    current_user: CurrentUser = Depends(get_current_user),
) -> EventsListResponse:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    if current_user.role not in ("manager", "school_admin"):
        raise HTTPException(status_code=403, detail="Only managers can view the manager queue")
        
    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        events = await repo.get_all_events(statuses=["proposed", "final_review"])
        return EventsListResponse(events=[EventResponse(**ev) for ev in events])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/finance-queue", response_model=EventsListResponse, summary="Get events in finance review/pricing queue")
async def get_finance_queue(
    current_user: CurrentUser = Depends(get_current_user),
) -> EventsListResponse:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    if current_user.role not in ("finance", "school_admin"):
        raise HTTPException(status_code=403, detail="Only finance users can view the finance queue")
        
    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        events = await repo.get_all_events(statuses=["finance_approval"])
        return EventsListResponse(events=[EventResponse(**ev) for ev in events])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/published", response_model=list[PublishedEventOut], summary="List published events (parent/student view)")
async def get_published_events(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[PublishedEventOut]:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        events = await TenantService.get_events_for_user(
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            user_role=current_user.role,
        )
        published = [ev for ev in events if ev.get("status") == "published"]
        return [PublishedEventOut(**ev) for ev in published]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/resources/{resource_id}", summary="Finance updates a resource line")
async def update_resource_line(
    resource_id: int,
    payload: ResourceLineIn,
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    if current_user.role not in ("finance", "school_admin"):
        raise HTTPException(status_code=403, detail="Only finance staff can modify resources during pricing")
        
    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")
            
        event = await repo.get_event_by_id(resource["event_id"])
        if not event or event.get("status", "draft") != "finance_approval":
            raise HTTPException(status_code=400, detail="Resources can only be edited when status is finance_approval")
            
        await repo.update_resource(
            resource_id=resource_id,
            resource_type_id=payload.resource_type_id,
            description=payload.description,
            quantity=payload.quantity,
            updated_by_user_id=current_user.id,
        )
        
        cost_info = await repo.get_resource_cost_by_resource_id(resource_id)
        if cost_info:
            new_total = float(cost_info["unit_price"]) * int(payload.quantity)
            await repo.set_resource_cost(
                resource_id=resource_id,
                unit_price=float(cost_info["unit_price"]),
                total_cost=new_total,
                currency=cost_info["currency"],
                set_by_user_id=current_user.id,
            )
            
        return {"status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/resources/{resource_id}/cost", summary="Finance sets the unit cost of a resource")
async def set_resource_pricing(
    resource_id: int,
    payload: ResourceCostIn,
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    if current_user.role not in ("finance", "school_admin"):
        raise HTTPException(status_code=403, detail="Only finance staff can update cost details")
        
    try:
        await TenantService.set_resource_cost(
            tenant_id=current_user.tenant_id,
            resource_id=resource_id,
            unit_price=payload.unit_price,
            currency=payload.currency,
            set_by_user_id=current_user.id,
        )
        return {"status": "success"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{event_id}", response_model=EventResponse, summary="Get event details by ID")
async def get_event(
    event_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> EventResponse:
    if not current_user.tenant_id and current_user.role != "super_admin":
        raise HTTPException(status_code=400, detail="Tenant context required")

    tenant_id = current_user.tenant_id or "tenant_a"
    try:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)
        event = await repo.get_event_by_id(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
            
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        if not TenantService.check_event_permission(actor, event, "read"):
            raise HTTPException(status_code=403, detail="Access denied")

        if current_user.role not in ("manager", "finance", "school_admin"):
            event["total_cost"] = None
        if current_user.role in ("student", "parent"):
            event["school_subsidy"] = None
            
        return EventResponse(**event)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{event_id}", response_model=EventResponse, summary="Update an event (staff only)")
async def update_event(
    event_id: int,
    payload: EventCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> EventResponse:
    if current_user.role not in ("school_admin", "teacher", "event_teacher", "manager"):
        raise HTTPException(status_code=403, detail="Only staff can update events")

    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        existing_event = await repo.get_event_by_id(event_id)
        if not existing_event:
            raise ValueError("Event not found")
            
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        if not TenantService.check_event_permission(actor, existing_event, "edit_draft") and not TenantService.check_event_permission(actor, existing_event, "edit_resources") and actor.role not in ("school_admin", "manager"):
            raise PermissionError("Access denied. Event can only be modified in draft status by its owner, or in resource planning by event teacher.")

        mappings = [m.dict() for m in payload.class_mappings]
        event = await TenantService.update_event_full(
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            title=payload.title,
            description=payload.description or "",
            address=payload.address or "",
            school_subsidy=payload.school_subsidy,
            date_val=payload.date,
            class_mappings=mappings,
            user_role=current_user.role,
            user_id=current_user.id,
        )
        return EventResponse(**event)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# Event Workflow & Resources Endpoints (Task 8)
# =============================================================================
from pydantic import BaseModel


class EventPatchRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    address: str | None = None
    date: datetime | None = None


class ClassMappingIn(BaseModel):
    class_id: int
    ticket_price: float = 0.0


class AudienceSelect(BaseModel):
    class_ids: list[int] = []
    class_mappings: list[ClassMappingIn] = []


@router.patch("/{event_id}", response_model=EventResponse, summary="Patch event details (draft only)")
async def patch_event(
    event_id: int,
    payload: EventPatchRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> EventResponse:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        
        event = await repo.get_event_by_id(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
            
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        if not TenantService.check_event_permission(actor, event, "edit_draft") and not TenantService.check_event_permission(actor, event, "edit_resources") and actor.role not in ("school_admin", "manager"):
            raise HTTPException(status_code=403, detail="Access denied. Event can only be modified in draft status by its owner, or in resource planning by event teacher.")
            
        # Update basics
        title = payload.title if payload.title is not None else event["title"]
        description = payload.description if payload.description is not None else event["description"]
        address = payload.address if payload.address is not None else event["address"]
        date_val = payload.date if payload.date is not None else event["date"]
        
        updated_event = await repo.update_event(
            event_id=event_id,
            title=title,
            description=description,
            address=address,
            school_subsidy=event["school_subsidy"],
            date_val=date_val,
        )
        return EventResponse(**updated_event)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{event_id}/audience", summary="Select targeted classes for the event")
async def select_audience(
    event_id: int,
    payload: AudienceSelect,
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        
        event = await repo.get_event_by_id(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
            
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        if not TenantService.check_event_permission(actor, event, "edit_draft") and not TenantService.check_event_permission(actor, event, "edit_resources") and actor.role not in ("school_admin", "manager"):
            raise HTTPException(status_code=403, detail="Access denied. Event can only be modified in draft status by its owner, or in resource planning by event teacher.")
            
        # Re-save class mappings: format as dict with class_id and budget_id/ticket_price
        class_mappings = []
        class_ids = []
        if payload.class_mappings:
            for item in payload.class_mappings:
                class_mappings.append({
                    "class_id": item.class_id,
                    "ticket_price": item.ticket_price,
                    "budgets": []
                })
                class_ids.append(item.class_id)
        else:
            for cid in payload.class_ids:
                class_mappings.append({
                    "class_id": cid,
                    "ticket_price": 0.0,
                    "budgets": []
                })
                class_ids.append(cid)
            
        await repo.update_event_full(
            event_id=event_id,
            title=event["title"],
            description=event["description"],
            address=event["address"],
            school_subsidy=event["school_subsidy"],
            date_val=event["date"],
            class_mappings=class_mappings,
        )
        
        predicted = await TenantService.get_predicted_attendance(current_user.tenant_id, class_ids)
        return {"predicted_attendance": predicted}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{event_id}/audience/prediction", summary="Get live feedback of predicted attendance")
async def get_audience_prediction(
    event_id: int,
    class_ids: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        ids_list = [int(x.strip()) for x in class_ids.split(",") if x.strip().isdigit()]
        predicted = await TenantService.get_predicted_attendance(current_user.tenant_id, ids_list)
        return {"predicted_attendance": predicted}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/resource-types", response_model=list[ResourceTypeResponse], summary="List active resource types")
async def list_resource_types(
    category: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ResourceTypeResponse]:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        types = await repo.get_all_resource_types(category)
        return [ResourceTypeResponse(**t) for t in types]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/resource-types", response_model=ResourceTypeResponse, status_code=201, summary="Create a custom resource type")
async def create_custom_resource_type(
    payload: ResourceTypeCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> ResourceTypeResponse:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can create custom resource types")
        
    try:
        rt_id = await TenantService.create_resource_type(
            tenant_id=current_user.tenant_id,
            name=payload.name,
            category=payload.category,
            is_custom=True,
            created_by_user_id=current_user.id,
        )
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        rt = await repo.get_resource_type_by_id(rt_id)
        return ResourceTypeResponse(**rt)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{event_id}/resources", summary="Set resource lines for event (full replace)")
async def set_event_resources(
    event_id: int,
    payload: list[ResourceLineIn],
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        
        event = await repo.get_event_by_id(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
            
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        if not TenantService.check_event_permission(actor, event, "edit_draft") and not TenantService.check_event_permission(actor, event, "edit_resources") and actor.role not in ("school_admin", "manager"):
            raise HTTPException(status_code=403, detail="Access denied. Event can only be modified in draft status by its owner, or in resource planning by event teacher.")
            
        resources_list = [r.dict() for r in payload]
        await TenantService.add_resources_to_event(
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            resources_list=resources_list,
            added_by_user_id=current_user.id,
        )
        return {"status": "success", "count": len(payload)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{event_id}/resources", response_model=ResourceSummaryResponse, summary="Get summary of resources and pricing")
async def get_event_resources(
    event_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> ResourceSummaryResponse:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        pool = await get_db_pool(current_user.tenant_id)
        repo = TenantRepository(pool)
        event = await repo.get_event_by_id(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
            
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        if not TenantService.check_event_permission(actor, event, "read"):
            raise HTTPException(status_code=403, detail="Access denied")
            
        summary = await TenantService.get_resource_summary(current_user.tenant_id, event_id)
        return ResourceSummaryResponse(**summary)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{event_id}/submit", summary="Submit event for manager approval")
async def submit_event(
    event_id: int,
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        if actor.role == "teacher":
            action = "submit_to_event_teacher"
        elif actor.role == "event_teacher":
            action = "submit_for_approval"
        else:
            raise PermissionError("Only teachers or event teachers can submit events")
        
        updated_event = await TenantService.transition_event(
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            action=action,
            actor=actor,
        )
        return EventResponse(**updated_event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{event_id}/manager-decision", summary="Submit manager decision (approve/reject)")
async def manager_decision(
    event_id: int,
    payload: ManagerDecision,
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        
        action = "manager_approve" if payload.decision == "approve" else "manager_reject"
        
        updated_event = await TenantService.transition_event(
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            action=action,
            actor=actor,
            reason=payload.reason,
        )
        return EventResponse(**updated_event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))





@router.post("/{event_id}/event-teacher-decision", summary="Submit event teacher decision (return to draft)")
async def event_teacher_decision(
    event_id: int,
    payload: ManagerDecision,
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        
        if payload.decision != "reject":
            raise ValueError("Event teacher can only reject/return to draft here. Use /submit to approve.")
            
        updated_event = await TenantService.transition_event(
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            action="event_teacher_reject",
            actor=actor,
            reason=payload.reason,
        )
        return EventResponse(**updated_event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{event_id}/finance-submit", summary="Submit priced event plan to final review")
async def finance_submit_priced(
    event_id: int,
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        
        updated_event = await TenantService.transition_event(
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            action="finance_submit",
            actor=actor,
        )
        return EventResponse(**updated_event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{event_id}/final-decision", summary="Submit final manager decision (publish/return)")
async def final_decision(
    event_id: int,
    payload: FinalDecision,
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
        
    try:
        class Actor:
            def __init__(self, id, role):
                self.id = id
                self.role = role
        actor = Actor(current_user.id, current_user.role)
        
        action = "manager_publish" if payload.decision == "publish" else "manager_return_to_finance"
        
        updated_event = await TenantService.transition_event(
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            action=action,
            actor=actor,
            reason=payload.reason,
        )
        return EventResponse(**updated_event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{event_id}/ticket-prices", summary="Update ticket prices for the targeted classes (finance/manager only)")
async def update_ticket_prices(
    event_id: int,
    payload: list[TicketPriceUpdate],
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    if current_user.role not in ("finance", "school_admin", "manager"):
        raise HTTPException(status_code=403, detail="Access denied. Only finance, manager or admin can modify ticket prices.")
        
    try:
        pool = await get_db_pool(current_user.tenant_id)
        
        for item in payload:
            await pool.execute(
                "UPDATE event_class_map SET ticket_price = $1 WHERE id = $2 AND event_id = $3",
                item.ticket_price,
                item.class_map_id,
                event_id,
            )
            
        return {"status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{event_id}/subsidy", summary="Update school subsidy for event (finance/manager/admin)")
async def update_event_subsidy(
    event_id: int,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant context required")
    if current_user.role not in ("finance", "school_admin", "manager"):
        raise HTTPException(status_code=403, detail="Access denied. Only finance, manager or admin can modify school subsidy.")
        
    try:
        pool = await get_db_pool(current_user.tenant_id)
        subsidy = float(payload.get("school_subsidy", 0.0))
        await pool.execute(
            "UPDATE events SET school_subsidy = $1 WHERE id = $2",
            subsidy,
            event_id,
        )
        return {"status": "success", "school_subsidy": subsidy}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))










