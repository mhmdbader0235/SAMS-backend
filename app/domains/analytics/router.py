"""Analytics router."""

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.dependencies import (
    CurrentUser,
    get_current_user,
    require_permission,
    require_tenant_live,
)
from app.domains.analytics.service import AnalyticsService

router = APIRouter(
    prefix="/api/v1/analytics", tags=["analytics"], dependencies=[Depends(require_tenant_live)]
)


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


@router.get(
    "/school-report",
    summary="Enrollment, trip participation, and payment status for the active academic year",
)
async def get_school_report(
    current_user: CurrentUser = Depends(require_permission("report:view")),
) -> dict:
    try:
        return await AnalyticsService.get_school_report(current_user.tenant_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/school-report/export.csv",
    summary="Enrollment-by-class report as CSV",
)
async def export_school_report_csv(
    current_user: CurrentUser = Depends(require_permission("report:view")),
) -> Response:
    try:
        csv_text = await AnalyticsService.get_school_report_csv(current_user.tenant_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=enrollment_by_class.csv"},
    )
