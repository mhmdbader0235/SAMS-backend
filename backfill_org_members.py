"""Backfill Keycloak Organization membership from control-plane user_tenant_map.

Run once after tenants have organizations (app startup does that via
ensure_keycloak_organizations), to give existing users the `organization` claim
their tokens need.

    python backfill_org_members.py            # dry run, prints what it would do
    python backfill_org_members.py --apply    # actually add memberships

Why user_tenant_map and not the Keycloak `tenant_id` user attribute: that attribute
does not exist. This realm's declarative User Profile declares only username,
email, firstName and lastName and leaves `unmanagedAttributePolicy` unset, so
Keycloak silently discards the `tenant_id`/`role` attributes that
sync_user_to_keycloak and _find_or_create_user_shell write. Verified against the
live realm -- 500 users, zero carrying a tenant_id attribute. The control plane is
the only place the mapping actually survived.

Safe to re-run: adding a user who is already a member is a no-op (Keycloak returns
409, which add_user_to_organization treats as success).
"""

import asyncio
import sys

from app.core.database import get_control_plane_pool
from app.core.keycloak_admin import add_user_to_organization, find_organization_by_alias


async def main(apply: bool) -> int:
    pool = await get_control_plane_pool()
    rows = await pool.fetch(
        """
        SELECT email, tenant_id, role
        FROM   user_tenant_map
        ORDER BY email, tenant_id
        """
    )
    print(f"{len(rows)} membership rows in user_tenant_map\n")

    # A row pointing at a tenant with no organization cannot be backfilled --
    # report those rather than failing per-user in a loop.
    tenants = sorted({r["tenant_id"] for r in rows})
    missing_orgs = [t for t in tenants if not find_organization_by_alias(t)]
    if missing_orgs:
        print(f"WARNING: {len(missing_orgs)} tenant(s) have no organization: {missing_orgs}")
        print("         Start the backend once so ensure_keycloak_organizations() runs.\n")

    if not apply:
        print("DRY RUN -- nothing will be changed. Re-run with --apply to write.\n")
        for r in rows[:20]:
            mark = "SKIP" if r["tenant_id"] in missing_orgs else "would add"
            print(f"  {mark:9} {r['email']:50} -> {r['tenant_id']} ({r['role']})")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
        return 0

    added = failed = skipped = 0
    for r in rows:
        if r["tenant_id"] in missing_orgs:
            skipped += 1
            continue
        if add_user_to_organization(r["email"], r["tenant_id"]):
            added += 1
        else:
            failed += 1
            print(f"  FAILED {r['email']} -> {r['tenant_id']}")

    print(f"\nadded/confirmed: {added}   failed: {failed}   skipped (no org): {skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(apply="--apply" in sys.argv)))
