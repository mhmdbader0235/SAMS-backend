"""Analytics router."""

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import CurrentUser, get_current_user
from ..services.analytics_service import AnalyticsService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/platform", summary="Get platform-wide aggregated analytics (super_admin only)")
async def get_platform_analytics(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    if current_user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only super_admins can view platform analytics")

    try:
        data = await AnalyticsService.get_platform_analytics(current_user.role)
        return data
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
