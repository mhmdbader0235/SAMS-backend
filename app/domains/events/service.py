"""EventService — event-specific business rules that were living inline in
events/router.py.

This domain deliberately does NOT re-implement the trip lifecycle, resource
costing, or read-permission logic that TenantService/TenantRepository
already own -- those are shared with students/auth/notifications/school/
analytics and moving them here would mean duplicating (or forking) logic
those other domains depend on. This service only owns the pieces that were
duplicated or embedded directly in the events router: the "who can edit a
draft/under-review event" rule, financial-field visibility masking, subsidy
defaulting, and submit/publish action selection.
"""

from app.core.database import get_db_pool
from app.domains.events.repository import EventRepository
from app.domains.tenant.service import TenantService
from app.domains.tenant.tenant_repository import TenantRepository

_SUBSIDY_VISIBLE_ROLES = {"school_admin", "manager"}
_EDIT_OVERRIDE_ROLES = {"school_admin", "manager"}


class EventService:
    @staticmethod
    def is_editable(actor, event: dict) -> bool:
        """An event can be edited by its owner while in draft, by an event
        teacher during resource planning, or by a manager/admin at any time."""
        return (
            TenantService.check_event_permission(actor, event, "edit_draft")
            or TenantService.check_event_permission(actor, event, "edit_resources")
            or bool(set(getattr(actor, "roles", [])) & _EDIT_OVERRIDE_ROLES)
        )

    @staticmethod
    def require_editable(actor, event: dict) -> None:
        if not EventService.is_editable(actor, event):
            raise PermissionError(
                "Access denied. Event can only be modified in draft status by its owner, "
                "or in resource planning by event teacher."
            )

    @staticmethod
    def mask_financials(event: dict, actor) -> dict:
        """Hide total_cost and school_subsidy from anyone who isn't manager/school_admin."""
        if not (set(getattr(actor, "roles", [])) & _SUBSIDY_VISIBLE_ROLES):
            event = dict(event)
            event["total_cost"] = None
            event["school_subsidy"] = None
        return event

    @staticmethod
    def resolve_subsidy(payload_subsidy: float | None, actor, existing_subsidy: float = 0.0) -> float:
        """Only school_admin/manager may set school_subsidy; everyone else keeps
        the existing value (or 0.0 on create)."""
        can_set = bool(set(getattr(actor, "roles", [])) & _SUBSIDY_VISIBLE_ROLES)
        if can_set and payload_subsidy is not None:
            return payload_subsidy
        return float(existing_subsidy or 0.0)

    @staticmethod
    def resolve_submit_action(status: str, actor) -> str:
        if status == "draft" and (
            actor.has_any_role("teacher", "school_admin") or actor.has_role("event:submit")
        ):
            return "submit_for_approval"
        if status in ("approved", "ready_to_publish") and actor.has_any_role(
            "teacher", "school_admin", "manager"
        ):
            return "teacher_publish"
        raise PermissionError(
            f"Cannot submit event in '{status}' status. "
            "Only draft events can be submitted for manager approval."
        )

    @staticmethod
    def resolve_publish_action(actor) -> str:
        return "manager_publish" if actor.has_role("manager") else "teacher_publish"

    # =========================================================================
    # Orchestration (permission check + repository dispatch)
    # =========================================================================
    @staticmethod
    async def update_resource_line(
        tenant_id: str, resource_id: int, actor, updates: dict
    ) -> None:
        pool = await get_db_pool(tenant_id)
        repo = TenantRepository(pool)

        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")

        event = await repo.get_event_by_id(resource["event_id"])
        if not event:
            raise ValueError("Event not found")

        EventService.require_editable(actor, event)

        merged = {
            "resource_type_id": updates.get("resource_type_id") or resource["resource_type_id"],
            "description": (
                updates["description"]
                if updates.get("description") is not None
                else resource["description"]
            ),
            "quantity": updates.get("quantity") if updates.get("quantity") is not None else resource["quantity"],
        }
        await repo.update_resource(
            resource_id=resource_id,
            resource_type_id=merged["resource_type_id"],
            description=merged["description"],
            quantity=merged["quantity"],
            updated_by_user_id=actor.id,
        )

        if updates.get("quantity") is not None:
            cost_info = await repo.get_resource_cost_by_resource_id(resource_id)
            if cost_info:
                new_total = float(cost_info["unit_price"]) * int(merged["quantity"])
                await repo.set_resource_cost(
                    resource_id=resource_id,
                    unit_price=float(cost_info["unit_price"]),
                    total_cost=new_total,
                    currency=cost_info["currency"],
                    set_by_user_id=actor.id,
                )

    @staticmethod
    async def update_ticket_prices(tenant_id: str, event_id: int, actor, items: list[dict]) -> None:
        pool = await get_db_pool(tenant_id)
        event = await TenantRepository(pool).get_event_by_id(event_id)
        if not event:
            raise ValueError("Event not found")
        if not TenantService.check_event_permission(actor, event, "read"):
            raise PermissionError(
                "Access denied. A manager cannot price a trip they cannot yet see "
                "-- wait for the teacher to submit it."
            )
        await EventRepository(pool).update_ticket_prices(event_id, items)

    @staticmethod
    async def update_subsidy(tenant_id: str, event_id: int, actor, subsidy: float) -> float:
        pool = await get_db_pool(tenant_id)
        event = await TenantRepository(pool).get_event_by_id(event_id)
        if not event:
            raise ValueError("Event not found")
        if not (
            "super_admin" in set(getattr(actor, "roles", []))
            or TenantService.check_event_permission(actor, event, "read")
        ):
            raise PermissionError(
                "Access denied. A manager cannot set subsidy on a trip they cannot yet see "
                "-- wait for the teacher to submit it."
            )
        await EventRepository(pool).update_subsidy(event_id, subsidy)
        return subsidy
