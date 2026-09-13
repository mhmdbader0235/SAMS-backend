"""Family router -- the cross-tenant "all my children, all schools" read.

Mounted UNGATED in main.py (no require_tenant_live): a parent's OTHER
school might be mid-setup while this one they're viewing from is live, and
the whole point of this page is to still show that school's section
(degraded, but visible) rather than 400 the entire request over one
tenant's onboarding status.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import CurrentUser, get_current_user
from app.domains.family.service import FamilyService

router = APIRouter(prefix="/api/v1/family", tags=["family"])


@router.get(
    "/overview",
    summary="All of a parent's children and their trips, across every school they belong to",
)
async def get_family_overview(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Deliberately calls current_user.can(..., resource=None) directly
    rather than depending on require_permission(action) -- require_permission
    always force-injects the caller's own tenant_id into the resource it
    sends OPA, which would make the policy's `not input.resource.tenant_id`
    guard (see school_policy.rego's cross_tenant_action allowlist) permanently
    unreachable. Calling can() here, with no resource, is what that guard is
    built to recognise -- exactly as require_permission's own docstring
    directs for actions needing per-request resource data.
    """
    if not await current_user.can("family:overview_read", resource=None):
        raise HTTPException(
            status_code=403, detail="Permission denied for action 'family:overview_read'"
        )

    try:
        return await FamilyService.get_overview(
            user_id=current_user.id,
            email=current_user.email,
            memberships=current_user.memberships,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
