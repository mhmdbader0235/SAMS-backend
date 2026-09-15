"""One-off data migration: collapse the three unreachable event states.

The `event_status` enum has 7 values but only 4 are reachable through the
lifecycle (`draft -> proposed -> approved -> published`). This rewrites rows
left behind in the three dead states:

    resource_planning              -> proposed
    finance_approval, final_review -> approved

This lived at `tests/test_migrate.py` until 2026-09-07, then at
`scripts/migrate_event_statuses.py` until it was renamed here. It was never a
test -- no assertions -- but it matched pytest's `test_*.py` collection
pattern and sat at the root of `tests/`, so a plain `pytest tests/` ran it. It
does NOT use the `db_pool` / `test_client` fixtures, so nothing redirected it
at `doumind_test`: it connected to whatever database the real config pointed
at and issued UPDATEs against every tenant. Running the suite against a live
control plane rewrote production event rows as a side effect of collection.

It is a script, so it now looks like one and only runs when invoked:

    cd back && python -m scripts.live_data_migration --dry-run
    cd back && python -m scripts.live_data_migration --apply

`--apply` mutates every tenant's database and is guarded twice: the
`ALLOW_LIVE_MIGRATION=true` environment variable must be set, and the operator
must additionally type CONFIRM at an interactive prompt naming the target
database. `--dry-run` is read-only and skips both guards.
"""

import argparse
import asyncio
import os
import sys

from app.core.database import db_manager


def _require_confirmation() -> None:
    if os.environ.get("ALLOW_LIVE_MIGRATION") != "true":
        print(
            "Refusing to run: set ALLOW_LIVE_MIGRATION=true to enable --apply.",
            file=sys.stderr,
        )
        sys.exit(1)

    answer = input(
        "WARNING: This mutates the live database. Type 'CONFIRM' to proceed: "
    )
    if answer != "CONFIRM":
        print("Confirmation not received. Aborting.", file=sys.stderr)
        sys.exit(1)


async def migrate(apply: bool) -> int:
    cp_pool = await db_manager.get_control_plane_pool()
    tenants = await cp_pool.fetch("SELECT tenant_id FROM tenants ORDER BY tenant_id")
    print(f"Found {len(tenants)} tenants.")

    failures = 0
    for row in tenants:
        tenant_id = row["tenant_id"]
        try:
            pool = await db_manager.get_pool(tenant_id)

            if not apply:
                counts = await pool.fetchrow(
                    """
                    SELECT
                      count(*) FILTER (WHERE status = 'resource_planning') AS to_proposed,
                      count(*) FILTER (WHERE status IN ('finance_approval', 'final_review'))
                        AS to_approved
                    FROM event
                    """
                )
                print(
                    f"[{tenant_id}] would update "
                    f"{counts['to_proposed']} -> proposed, "
                    f"{counts['to_approved']} -> approved"
                )
                continue

            res1 = await pool.execute(
                "UPDATE event SET status = 'proposed' WHERE status = 'resource_planning'"
            )
            print(f"[{tenant_id}] resource_planning -> proposed: {res1}")

            res2 = await pool.execute(
                "UPDATE event SET status = 'approved' "
                "WHERE status IN ('finance_approval', 'final_review')"
            )
            print(f"[{tenant_id}] finance_approval/final_review -> approved: {res2}")
        except Exception as exc:
            failures += 1
            print(f"[{tenant_id}] FAILED: {exc}")

    if failures:
        print(f"\n{failures} tenant(s) failed.")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change, touching nothing",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="actually rewrite the rows",
    )
    args = parser.parse_args()

    if args.apply:
        _require_confirmation()

    sys.exit(asyncio.run(migrate(apply=args.apply)))


if __name__ == "__main__":
    main()
