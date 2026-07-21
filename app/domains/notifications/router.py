"""Notifications router."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import CurrentUser, get_current_user
from app.domains.tenant.service import TenantService

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class NotificationResponse(BaseModel):
    id: UUID
    event_id: int
    recipient_user_id: int
    delivered_at: datetime
    read_at: datetime | None = None
    title: str
    description: str
    event_type: str | None = "general"
    student_name: str | None = None


class NotificationsListResponse(BaseModel):
    notifications: list[NotificationResponse]


@router.get("", response_model=NotificationsListResponse, summary="List all notifications for the user")
async def list_notifications(
    current_user: CurrentUser = Depends(get_current_user),
) -> NotificationsListResponse:
    tenant_id = current_user.tenant_id or "tenant_a"
    try:
        notifs = await TenantService.get_notifications_for_user(
            tenant_id=tenant_id,
            user_id=current_user.id,
            user_role=current_user.role,
        )
        return NotificationsListResponse(
            notifications=[NotificationResponse(**n) for n in notifs]
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{notif_id}/read", summary="Mark a notification as read")
async def mark_notification_read(
    notif_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    tenant_id = current_user.tenant_id or "tenant_a"
    try:
        success = await TenantService.mark_notification_read(tenant_id, notif_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found or already read",
            )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
