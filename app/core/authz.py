"""Single in-process authorization gate for role/permission decisions.

Extracted verbatim from TenantService._has_intersection (module roadmap
Wave A2) so it has one home instead of being a static method callers reach
through TenantService for no structural reason. Logic is unchanged -- this is
a location move, not a behavior change; see
tests/unit/test_permission_catalog_matches_literal.py and
tests/unit/test_authz_no_reverse_expansion.py for the proof.

Scope: role/permission-only decisions ("does this caller hold `class:update`
or the `school_admin` role?"). Resource-state decisions (event lifecycle,
ownership, tenant isolation) stay with OPA / TenantService.check_event_permission
-- see ADR 0005 for the split rationale.
"""

from app.core.dependencies import COMPOSITE_ROLE_PERMISSIONS


def require(user_role: str | list[str] | None, allowed_roles: set[str] | str) -> bool:
    if not user_role:
        return False
    if isinstance(user_role, str):
        roles = {user_role}
    elif isinstance(user_role, list | tuple | set):
        roles = set(user_role)
    else:
        roles = {str(user_role)}

    reqs = {allowed_roles} if isinstance(allowed_roles, str) else set(allowed_roles)

    if "super_admin" in roles or "*" in roles:
        return True

    if "admin" in roles:
        roles.add("school_admin")

    # Forward expansion only: if the caller holds a composite role (e.g.
    # "teacher"), grant the granular permissions that role carries (e.g.
    # "class:read"), so `reqs` can be either role names or permission
    # strings. There must be no reverse direction here — walking a
    # granular permission the caller holds (e.g. "school:read", which
    # nearly every role has) back to "every role that could plausibly
    # hold it" silently grants "school_admin" to anyone, which is exactly
    # how a student token used to pass admin-only checks. See
    # SchoolService._require_admin for the strict-membership pattern
    # this mirrors.
    expanded_roles = set(roles)
    for r in list(roles):
        if r in COMPOSITE_ROLE_PERMISSIONS:
            expanded_roles.update(COMPOSITE_ROLE_PERMISSIONS[r])

    return bool(expanded_roles.intersection(reqs))
