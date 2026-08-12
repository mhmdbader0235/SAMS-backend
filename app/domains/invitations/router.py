"""
Invitation Router Layer.

Handles HTTP request parsing, Pydantic validation, RBAC clearance enforcement,
and response formatting for pre-provisioned user invitations.
Must contain ZERO business logic, Keycloak calls, or raw SQL queries.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.database import get_control_plane_pool
from app.core.dependencies import CurrentUser, get_current_user
from app.domains.invitations.service import InvitationService
from app.schemas.invitation import InvitationCreateRequest, InvitationResponse

router = APIRouter(prefix="/api/v1/invitations", tags=["invitations"])


@router.post(
    "",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create pre-provisioned user invitation (School Admin / Super Admin)",
)
async def create_invitation(
    payload: InvitationCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> InvitationResponse:
    """Pre-provision a user in Keycloak and create an invitation audit record."""
    # RBAC Guard: Require school_admin, super_admin, or user:invite clearance
    if not (current_user.has_role("school_admin") or current_user.has_role("super_admin") or current_user.has_role("user:invite")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only school_admin, super_admin, or users with user:invite permission can send invitations",
        )

    # Strict Tenant Scoping: non-super_admin can only create invitations for their own tenant
    is_super = current_user.has_role("super_admin")
    if not is_super and current_user.tenant_id:
        if payload.tenant_id and payload.tenant_id.strip().lower() != current_user.tenant_id.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Admins of tenant '{current_user.tenant_id}' cannot send invitations for tenant '{payload.tenant_id}'",
            )
        payload.tenant_id = current_user.tenant_id

    try:
        cp_pool = await get_control_plane_pool()
        res = await InvitationService.send_user_invitation(
            cp_pool=cp_pool,
            payload=payload,
            current_user=current_user,
        )
        return InvitationResponse(**res)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
