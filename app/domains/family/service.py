"""FamilyService -- the cross-tenant "all my children, all schools" read.

Mirrors AnalyticsService.get_platform_analytics' scatter-gather shape (see
app/domains/analytics/service.py), but is a fundamentally different kind of
cross-tenant read: analytics fans out over EVERY tenant on the platform and
is super_admin-only; this fans out only over the CALLER'S OWN verified
memberships (CurrentUser.memberships, never a client-supplied list and never
get_all_tenants()). No repository of its own -- every per-tenant query
below reuses an existing single-tenant-safe method from another domain, so
this file owns no SQL (see CLAUDE.md §4's tenancy invariant: no query, join,
or repository method may span tenant schemas).

Deliberately does NOT silently drop a school on error the way analytics does
(`if isinstance(res, Exception): continue`) -- on a page claiming "all your
children's trips", losing a school without saying so is a correctness bug,
not a cosmetic one. Every membership becomes a schools[] entry, ok or error.
"""

import asyncio

from app.core.database import get_db_pool
from app.domains.school.service import SchoolService
from app.domains.tenant.service import TenantService
from app.domains.tenant.user_repository import UserRepository

# A wedged/unreachable school degrades its own section instead of hanging
# the whole request for every other school the caller belongs to.
_PER_TENANT_TIMEOUT_SECONDS = 5
# Typical n is 2 (a parent at two schools); this is a hard sanity cap, not a
# tuned limit -- nothing about this design assumes more than a handful.
_MAX_MEMBERSHIPS = 10
_MAX_CONCURRENCY = 4

# PII the overview must never carry, even though the reused repository
# methods below return it as part of their normal single-tenant shape (see
# get_linked_students_for_parent's birth_data/gender/email columns). Kept
# here as the single point that enforces the plan's "PII -- what must not be
# aggregated" list, so a future field added to those methods doesn't leak
# into a cross-tenant response just because a caller forgot to re-check.
_CHILD_FIELDS = ("id", "name", "class_id", "class_name")
_ENROLLMENT_FIELDS = (
    "id",
    "student_id",
    "student_name",
    "class_name",
    "event_class_map_id",
    "event_title",
    "event_date",
    "event_status",
    "state",
    "ticket_price",
    "payment_status",
)


class FamilyService:
    @staticmethod
    async def get_overview(user_id, email: str, memberships: list[dict]) -> dict:
        if len(memberships) > _MAX_MEMBERSHIPS:
            raise ValueError(
                f"Too many school memberships ({len(memberships)}) for the family overview."
            )

        semaphore = asyncio.Semaphore(min(_MAX_CONCURRENCY, len(memberships) or 1))

        async def fetch_school(membership: dict) -> dict:
            tenant_id = membership["tenant_id"]
            role = membership.get("role")
            async with semaphore:
                try:
                    async with asyncio.timeout(_PER_TENANT_TIMEOUT_SECONDS):
                        return await FamilyService._fetch_one_school(tenant_id, role, email)
                except Exception as exc:
                    return {
                        "tenant_id": tenant_id,
                        "role": role,
                        "status": "error",
                        "error_message": str(exc),
                        "school_name": None,
                        "currency": None,
                        "timezone": None,
                        "children": [],
                        "enrollments": [],
                    }

        # Zipped back against the membership list, not just whatever
        # `results` happens to contain -- return_exceptions=True means a
        # bare exception (not one fetch_school already caught) still lands
        # as its own list entry, and this keeps every membership visible in
        # the output either way, per the "never silently drop a school" rule
        # above.
        results = await asyncio.gather(
            *(fetch_school(m) for m in memberships), return_exceptions=True
        )

        schools = []
        degraded = []
        for membership, res in zip(memberships, results, strict=True):
            if isinstance(res, Exception):
                res = {
                    "tenant_id": membership["tenant_id"],
                    "role": membership.get("role"),
                    "status": "error",
                    "error_message": str(res),
                    "school_name": None,
                    "currency": None,
                    "timezone": None,
                    "children": [],
                    "enrollments": [],
                }
            schools.append(res)
            if res["status"] == "error":
                degraded.append(res["tenant_id"])

        return {"schools": schools, "degraded": degraded}

    @staticmethod
    async def _fetch_one_school(tenant_id: str, role: str | None, email: str) -> dict:
        summary = await SchoolService.get_locale_summary(tenant_id)

        pool = await get_db_pool(tenant_id)
        user_row = await UserRepository(pool).get_user_by_email(email) if email else None
        if not user_row:
            # A verified membership row exists but this tenant has no local
            # `users` row for this email yet (e.g. linked by invitation, never
            # logged in there) -- an empty, healthy section, not an error.
            return {
                "tenant_id": tenant_id,
                "role": role,
                "status": "ok",
                "error_message": None,
                "school_name": summary["school_name"],
                "currency": summary["currency"],
                "timezone": summary["timezone"],
                "children": [],
                "enrollments": [],
            }

        local_user_id = user_row["id"]

        children_raw, enrollments_raw, events_raw = await asyncio.gather(
            TenantService.get_linked_students_for_parent(tenant_id, local_user_id),
            TenantService.get_enrollments_for_user(tenant_id, local_user_id, "parent"),
            TenantService.get_events_for_user(tenant_id, local_user_id, "parent"),
        )

        enrollment_ids = [e["id"] for e in enrollments_raw]
        payments = await TenantService.get_payments_for_enrollments(tenant_id, enrollment_ids)

        # class_map_id -> {event date, event status}, built once from the
        # events this parent can already see, so enriching each enrollment
        # below is a dict lookup rather than another per-row query.
        map_to_event: dict = {}
        for ev in events_raw:
            for m in ev.get("class_mappings", []) or []:
                map_to_event[m["id"]] = {"date": ev.get("date"), "status": ev.get("status")}

        children = [{k: c.get(k) for k in _CHILD_FIELDS} for c in children_raw]

        enrollments = []
        for e in enrollments_raw:
            ev_info = map_to_event.get(e.get("event_class_map_id"), {})
            payment = payments.get(e["id"])
            row = {k: e.get(k) for k in _ENROLLMENT_FIELDS if k in e}
            row["event_date"] = ev_info.get("date")
            row["event_status"] = ev_info.get("status")
            row["payment_status"] = payment["status"] if payment else None
            enrollments.append(row)

        return {
            "tenant_id": tenant_id,
            "role": role,
            "status": "ok",
            "error_message": None,
            "school_name": summary["school_name"],
            "currency": summary["currency"],
            "timezone": summary["timezone"],
            "children": children,
            "enrollments": enrollments,
        }
