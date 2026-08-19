"""School Setup Router — Day-1 onboarding: identity, campus, contacts, activation.

Deliberately NOT gated by require_tenant_live — these are the endpoints a
tenant stuck in "setup" status must still be able to reach in order to finish
setup in the first place.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import CurrentUser, get_current_user
from app.core.schemas import (
    SchoolCampusSchema,
    SchoolContactSchema,
    SchoolProfileResponse,
    SchoolProfileUpdateRequest,
    SchoolSetupStateResponse,
)
from app.domains.school.service import SchoolService

router = APIRouter(prefix="/api/v1/school", tags=["school"])


@router.get("/setup-state", response_model=SchoolSetupStateResponse, summary="Get the tenant's onboarding progress")
async def get_setup_state(current_user: CurrentUser = Depends(get_current_user)) -> SchoolSetupStateResponse:
    try:
        state = await SchoolService.get_setup_state(current_user.tenant_id or "tenant_a")
        return SchoolSetupStateResponse(**state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/profile", response_model=SchoolProfileResponse, summary="Get school profile, campuses, and contacts")
async def get_profile(current_user: CurrentUser = Depends(get_current_user)) -> SchoolProfileResponse:
    try:
        bundle = await SchoolService.get_profile_bundle(current_user.tenant_id or "tenant_a")
        return SchoolProfileResponse(**bundle)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/profile", response_model=SchoolProfileResponse, summary="Update school identity, locale, and brand")
async def update_profile(
    payload: SchoolProfileUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> SchoolProfileResponse:
    try:
        await SchoolService.update_profile(
            current_user.tenant_id or "tenant_a",
            payload.model_dump(exclude_unset=True),
            current_user.roles,
        )
        bundle = await SchoolService.get_profile_bundle(current_user.tenant_id or "tenant_a")
        return SchoolProfileResponse(**bundle)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/campuses", summary="Create or update the primary campus")
async def upsert_campus(
    payload: SchoolCampusSchema,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        return await SchoolService.upsert_campus(
            current_user.tenant_id or "tenant_a",
            payload.model_dump(exclude={"id"}, exclude_unset=True),
            current_user.roles,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/campuses", summary="List campuses")
async def list_campuses(current_user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    try:
        return await SchoolService.list_campuses(current_user.tenant_id or "tenant_a")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/contacts", summary="Add a school contact")
async def create_contact(
    payload: SchoolContactSchema,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        return await SchoolService.create_contact(
            current_user.tenant_id or "tenant_a",
            payload.model_dump(exclude={"id"}),
            current_user.roles,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/contacts", summary="List school contacts")
async def list_contacts(current_user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    try:
        return await SchoolService.list_contacts(current_user.tenant_id or "tenant_a")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/contacts/{contact_id}", summary="Update a school contact")
async def update_contact(
    contact_id: int,
    payload: SchoolContactSchema,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        return await SchoolService.update_contact(
            current_user.tenant_id or "tenant_a",
            contact_id,
            payload.model_dump(exclude={"id"}),
            current_user.roles,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/contacts/{contact_id}", summary="Remove a school contact")
async def delete_contact(
    contact_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    try:
        await SchoolService.delete_contact(current_user.tenant_id or "tenant_a", contact_id, current_user.roles)
        return {"status": "ok"}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/setup/commit-profile",
    response_model=SchoolProfileResponse,
    summary="Validate and lock in Stage 1 (School Information)",
)
async def commit_profile(current_user: CurrentUser = Depends(get_current_user)) -> SchoolProfileResponse:
    try:
        await SchoolService.commit_profile(current_user.tenant_id or "tenant_a", current_user.roles)
        bundle = await SchoolService.get_profile_bundle(current_user.tenant_id or "tenant_a")
        return SchoolProfileResponse(**bundle)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/setup/activate",
    response_model=SchoolProfileResponse,
    summary="Activate the tenant — permanently locks the curriculum system",
)
async def activate(current_user: CurrentUser = Depends(get_current_user)) -> SchoolProfileResponse:
    try:
        await SchoolService.activate(current_user.tenant_id or "tenant_a", current_user.roles)
        bundle = await SchoolService.get_profile_bundle(current_user.tenant_id or "tenant_a")
        return SchoolProfileResponse(**bundle)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
