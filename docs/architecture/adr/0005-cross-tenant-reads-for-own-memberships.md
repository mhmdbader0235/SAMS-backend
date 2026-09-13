# ADR-0005: Cross-tenant reads for a caller's own verified memberships

- **Status:** Accepted
- **Date:** 2026-09-09
- **Amends:** CLAUDE.md §4's tenancy invariant ("No query, join, or repository method may span
  tenant schemas -- analytics is the single sanctioned exception")

## Context

SchoolDesk assumed one person belongs to exactly one school. Real customers don't fit that: a
parent with children at two schools, a manager who works at two schools, a teacher at one school
who is a parent at another. `user_tenant_map` was already re-keyed to `(email, tenant_id)` for
exactly this shape (`cp_0002_user_tenant_map_composite_pk.py`), but nothing read it that way --
`get_current_user` collapsed every account to one tenant, and the login/switcher UX assumed the
same.

Two capabilities were built on top of that groundwork: (A) a role-agnostic tenant **switcher**,
letting any multi-membership user select which of their own schools a request acts in, and (B) a
**unified read**, `GET /api/v1/family/overview`, showing a parent every child's trips across every
school they belong to on one page.

(B) is a second cross-tenant read, structurally different from the one CLAUDE.md §4 already
names as the sole exception (`domains/analytics/`, super_admin-only, scatter-gathering across
*every* tenant on the platform for a platform operator). This ADR records why a second exception
is warranted, and the guardrails that keep it from becoming a general escape hatch.

## Decision

1. **A user's membership set is server-authoritative and read live, per request** -- from
   `user_tenant_map` / `parent_tenant_links`, never from the JWT and never from client input.
   `CurrentUser.memberships` (`app/core/dependencies.py`) is populated this way on every request
   for every non-super_admin user.

2. **`X-Tenant-ID` is reinterpreted from an assertion into a selection.** For super_admin it stays
   an assertion (their access is already total). For everyone else, the header now names a choice
   from their own verified membership set -- accepted only if it resolves to one of those rows,
   otherwise a 403, never a silent fallback to the caller's home tenant. A genuine selection
   rebuilds role/permissions from the target tenant's own `users` row rather than unioning with
   the token's home-tenant roles, so a teacher-at-school-A does not carry teacher's composite
   permissions into school-B, where they hold only `parent`.

3. **Reads may fan out over the caller's own memberships, by iterating per-tenant pools --
   never by a query, join, or repository method spanning schemas.** `FamilyService.get_overview`
   (`app/domains/family/service.py`) enumerates only `current_user.memberships`, never
   `get_all_tenants()` and never a client-supplied tenant list, and composes each school's data
   through existing single-tenant-safe repository methods (`get_linked_students_for_parent`,
   `get_enrollments_for_user`, `get_events_for_user`) -- it owns no SQL of its own beyond one
   small bulk payment lookup (`get_payments_by_enrollment_ids`), which stays inside
   `TenantRepository` and never crosses a schema boundary either.

4. **Writes stay strictly single-tenant.** There is no cross-tenant write endpoint. The family
   overview page calls the existing single-tenant enrollment/payment endpoints, one call per
   write, with an explicit per-request tenant selection (`api.js`'s `tenantHeaders(tenantId)`).

5. **Cross-tenant surfaces are a closed OPA allowlist, not a loosened tenant check.**
   `school_policy.rego`'s `valid_tenant` (`input.user.tenant_id == input.resource.tenant_id`)
   is untouched. A new, narrow rule instead: `cross_tenant_action := {"family:overview_read"}`,
   requiring the action to be in that set, `"parent" in input.user.roles`, and
   `not input.resource.tenant_id` -- that last clause is load-bearing, since
   `require_permission()` always force-injects the caller's tenant_id into `resource`, making this
   rule unreachable through that path. Only a direct `current_user.can(action, resource=None)`
   call (as `family/router.py` makes) can reach it -- exactly the pattern `require_permission`'s
   own docstring already prescribes for actions needing per-request resource data.

6. **Never silently drop a school on error.** `AnalyticsService.get_platform_analytics`'s
   `if isinstance(res, Exception): continue` erases a failed tenant from its output -- acceptable
   for a platform dashboard, a correctness bug for a page claiming "all your children's trips".
   `FamilyService.get_overview` zips every result back against the membership list; a failure
   becomes a visible `status: "error"` section, never a missing one.

## Consequences

- **Role becomes a property of the `(user, tenant)` edge, not the account.** The same human can
  legitimately hold different roles at different schools; which one applies depends on which
  school the current request is acting in.
- **Membership revocation (or a role change) now takes effect on the user's next request, not
  their next login** -- membership status and the tenant-local role are both re-read live, on
  every request, exactly like every other tenant-scoped role check in this codebase already did
  before this feature; only the cross-tenant fan-out is new.
- **The audit obligations this ADR creates are only as strong as `audit_log`'s current
  mutability.** `tenant_selection.denied` writes use the real `AuditService.record`, not the
  `_log_audit()` stdout stub -- but the immutable-audit-log requirement itself remains
  unimplemented (see PROJECT_UNDERSTANDING.md §14). A denied cross-tenant attempt is logged, but
  that log is not yet tamper-evident.
- **`event_teacher`/catalog parity note (CLAUDE.md §7):** the new `family:overview_read`
  permission was added to exactly one role (`parent`) in all four required places --
  `permissions_catalog.json`, the generated `front/src/permissions.generated.js` and
  `back/policies/data/permissions.json`, and `school_policy.rego`'s own hand-written
  `cross_tenant_action` set (deliberately not consuming the generated data file -- see A2.2's own
  scope note on why the rego rewire was left as separate, larger future work). No other role
  catalog needed a change.
- **Not done by this ADR:** reload-free tenant switching (rejected separately -- see the
  multi-school access plan's "Rejected, with reasons"), and wiring the last-ditch cross-tenant
  scan (`dependencies.py`, the "no `user_tenant_map` row yet" self-heal) out of the codebase --
  that scan remains, still doing a real cross-tenant enumeration for a narrower, pre-existing
  reason (self-healing accounts that predate organization membership), and is a candidate for
  deletion once every user reliably has a resolvable membership row.
