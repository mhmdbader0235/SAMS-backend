"""
AnalyticsService — coordinates platform-wide analytics for Super-Admins.

Implements scatter-gather pattern fetching aggregated data from all tenants.
Does not import FastAPI or asyncpg directly.
"""

import asyncio

from app.core.database import get_control_plane_pool, get_db_pool
from app.domains.tenant.control_plane_repository import ControlPlaneRepository
from app.domains.tenant.tenant_repository import TenantRepository


class AnalyticsService:
    @staticmethod
    async def get_platform_analytics(requesting_user_role: str) -> dict:
        """Fetch platform-wide aggregated analytics from all tenant databases.

        Concurrently queries all tenant pools using asyncio.gather.
        Only accessible by super_admin users.
        """
        if requesting_user_role != "super_admin":
            raise PermissionError("Only super_admins can access platform analytics")

        # Get list of all tenants from the Control-Plane database
        cp_pool = await get_control_plane_pool()
        cp_repo = ControlPlaneRepository(cp_pool)
        tenants = await cp_repo.get_all_tenants()

        # Scatter phase: define tasks for each tenant DB
        async def fetch_tenant_data(tenant: dict) -> dict:
            tenant_id = tenant["tenant_id"]
            try:
                tenant_pool = await get_db_pool(tenant_id)
                repo = TenantRepository(tenant_pool)
                counts = await repo.get_analytics_summary()
                return {
                    "tenant_id": tenant_id,
                    "name": tenant["name"],
                    "status": "success",
                    **counts,
                }
            except Exception as exc:
                return {
                    "tenant_id": tenant_id,
                    "name": tenant["name"],
                    "status": "error",
                    "error_message": str(exc),
                    "student_count": 0,
                    "class_count": 0,
                    "enrollment_count": 0,
                    "event_count": 0,
                }

        tasks = [fetch_tenant_data(t) for t in tenants]

        # Gather phase: Execute concurrently with return_exceptions=True
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        summaries: list[dict] = []
        total_students = 0
        total_classes = 0
        total_enrollments = 0
        total_events = 0

        for res in results:
            if isinstance(res, Exception):
                # This handles case where fetch_tenant_data itself raised a breaking exception
                continue
            summaries.append(res)
            total_students += res.get("student_count", 0)
            total_classes += res.get("class_count", 0)
            total_enrollments += res.get("enrollment_count", 0)
            total_events += res.get("event_count", 0)

        return {
            "platform_totals": {
                "total_tenants": len(tenants),
                "total_students": total_students,
                "total_classes": total_classes,
                "total_enrollments": total_enrollments,
                "total_events": total_events,
            },
            "tenants": summaries,
        }
