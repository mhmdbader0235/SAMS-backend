"""Find email collisions cp_0003's identities migration will otherwise abort on.

cp_0003 collapses `public.parents`, `public.super_admins`, and
`public.user_tenant_map` into one `identities` row per distinct email. That
assumes one email is one person -- true for the overwhelming majority, but a
shared family mailbox, a departmental address, or a reissued account can put
two different humans behind one address in two different stores. Silently
merging those two humans into one identity would hand one of them the
other's access, unrecoverably, the moment cp_0004 re-keys memberships onto
it.

This script finds every email that appears in more than one of those three
stores, prints the memberships/roles on both sides so a human decides with
evidence rather than an email address alone, and (with `--record-decision`)
writes that decision into `identity_merge_decisions` -- the ledger cp_0003's
migration gate checks. It does NOT decide anything itself.

    cd back && python -m scripts.check_identity_clashes                # dry-run, read-only
    cd back && python -m scripts.check_identity_clashes --record-decision \
        alice@example.com --decision same_person --decided-by ops@doumind.ai

`--record-decision` mutates the control-plane database and is guarded the
same way scripts/live_data_migration.py's --apply is: ALLOW_LIVE_MIGRATION=true
plus an interactive CONFIRM. Listing collisions (the default) is read-only and
unguarded, and safe to run against a production clone repeatedly while the
reconciliation list is being worked through.
"""

import argparse
import asyncio
import os
import sys

import asyncpg

from app.core.database import db_manager

_COLLISION_QUERY = """
    SELECT email, array_agg(DISTINCT store ORDER BY store) AS stores
    FROM (
        SELECT email, 'parent' AS store FROM parents
        UNION
        SELECT email, 'super_admin' AS store FROM super_admins
        UNION
        -- A role='parent' row here is a parent's OWN mirror, written by
        -- AuthService.login_user on every parent login ("Save email->tenant
        -- mapping so Keycloak logins resolve correctly") -- the same
        -- identity as their `parents` row, not a second account. Only a
        -- NON-parent membership (teacher/student/manager/school_admin/...)
        -- is evidence of a genuinely separate tenant-local account sharing
        -- this email, which is the actual ambiguity worth a human's review.
        SELECT DISTINCT email, 'tenant_member' AS store FROM user_tenant_map WHERE role != 'parent'
    ) by_store
    GROUP BY email
    HAVING COUNT(DISTINCT store) > 1
    ORDER BY email
"""


async def _load_evidence(cp_pool, email: str) -> dict:
    """Everything a reviewer needs to tell 'same person' from 'two people'."""
    parent = await cp_pool.fetchrow("SELECT id, created_at FROM parents WHERE email = $1", email)
    super_admin = await cp_pool.fetchrow(
        "SELECT id, created_at FROM super_admins WHERE email = $1", email
    )
    memberships = await cp_pool.fetch(
        "SELECT tenant_id, role, updated_at FROM user_tenant_map WHERE email = $1 "
        "ORDER BY tenant_id",
        email,
    )
    return {
        "parent": dict(parent) if parent else None,
        "super_admin": dict(super_admin) if super_admin else None,
        "memberships": [dict(m) for m in memberships],
    }


async def _undecided_collisions(cp_pool) -> list[dict]:
    rows = await cp_pool.fetch(_COLLISION_QUERY)
    undecided = []
    for row in rows:
        try:
            already = await cp_pool.fetchval(
                "SELECT 1 FROM identity_merge_decisions WHERE email = $1", row["email"]
            )
        except asyncpg.exceptions.UndefinedTableError:
            # cp_0003 hasn't run yet -- the ledger doesn't exist, so by
            # definition nothing has been decided. Every collision is
            # undecided; this is the expected state before the first run.
            already = None
        if not already:
            undecided.append(dict(row))
    return undecided


async def list_collisions() -> int:
    cp_pool = await db_manager.get_control_plane_pool()
    undecided = await _undecided_collisions(cp_pool)

    if not undecided:
        print("No unreviewed email collisions. cp_0003 is clear to run.")
        return 0

    print(f"{len(undecided)} unreviewed email collision(s):\n")
    for row in undecided:
        email = row["email"]
        evidence = await _load_evidence(cp_pool, email)
        print(f"  {email}  (stores: {', '.join(row['stores'])})")
        if evidence["parent"]:
            print(f"    - parent record: id={evidence['parent']['id']}")
        if evidence["super_admin"]:
            print(f"    - super_admin record: id={evidence['super_admin']['id']}")
        for m in evidence["memberships"]:
            print(f"    - {m['tenant_id']}: role={m['role']} (updated_at={m['updated_at']})")
        print(
            "    -> record a decision: python -m scripts.check_identity_clashes "
            f"--record-decision {email} --decision same_person|distinct_people "
            "--decided-by <you>"
        )
        print()
    return 1


def _require_confirmation() -> None:
    if os.environ.get("ALLOW_LIVE_MIGRATION") != "true":
        print(
            "Refusing to record a decision: set ALLOW_LIVE_MIGRATION=true to enable "
            "--record-decision.",
            file=sys.stderr,
        )
        sys.exit(1)
    answer = input(
        "This writes to identity_merge_decisions on the live control-plane database. "
        "Type 'CONFIRM' to proceed: "
    )
    if answer != "CONFIRM":
        print("Confirmation not received. Aborting.", file=sys.stderr)
        sys.exit(1)


async def record_decision(args: argparse.Namespace) -> int:
    cp_pool = await db_manager.get_control_plane_pool()
    _require_confirmation()
    await cp_pool.execute(
        """
        INSERT INTO identity_merge_decisions (email, decision, keeps_email, decided_by, note)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (email) DO UPDATE
            SET decision   = EXCLUDED.decision,
                keeps_email = EXCLUDED.keeps_email,
                decided_by = EXCLUDED.decided_by,
                note       = EXCLUDED.note,
                decided_at = CURRENT_TIMESTAMP
        """,
        args.record_decision,
        args.decision,
        args.keeps_email,
        args.decided_by,
        args.note,
    )
    print(f"Recorded '{args.decision}' for {args.record_decision}.")
    if args.decision == "distinct_people":
        print(
            "distinct_people recorded -- re-address one side by hand in its own "
            "table before cp_0003 is re-run; the collision query will otherwise "
            "keep finding this email."
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record-decision",
        metavar="EMAIL",
        help="Record a human decision for this email (requires --decision and --decided-by).",
    )
    parser.add_argument("--decision", choices=["same_person", "distinct_people"])
    parser.add_argument("--decided-by", help="Who is making this decision (name or email).")
    parser.add_argument(
        "--keeps-email",
        help="For --decision distinct_people: which store keeps this email address.",
    )
    parser.add_argument("--note", help="Free-text context for the decision.")
    args = parser.parse_args()

    if args.record_decision:
        if not args.decision or not args.decided_by:
            parser.error("--record-decision requires --decision and --decided-by")
        sys.exit(asyncio.run(record_decision(args)))
    else:
        sys.exit(asyncio.run(list_collisions()))


if __name__ == "__main__":
    main()
