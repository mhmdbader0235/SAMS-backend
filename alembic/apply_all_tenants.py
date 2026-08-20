"""
Apply the "tenant" Alembic branch to every tenant schema, not just whichever
one happens to be connected at the moment — this is the "run once per tenant
schema" half of schema-per-tenant migrations that plain `alembic upgrade`
can't do on its own (it has no concept of "for each tenant").

Usage (from back/):
    python -m alembic.apply_all_tenants
    python -m alembic.apply_all_tenants --only tenant_a tenant_b

Tenant IDs come from the control-plane `tenants` table — the same registry
app/core/database.py's get_pool() uses to decide what should exist. A tenant
row with no matching Postgres schema yet (a schema drop without a matching row
delete, or a tenant that has never been touched by a real request) is treated
the same way get_pool() already treats it: the schema is created fresh. That
is called out explicitly below rather than silently creating schemas, since a
`tenants` row is not always proof the schema is still supposed to exist (see
FIX_PLAN.md Step 5 for a real incident where deleted schemas left orphaned
`tenants` rows behind).
"""

import argparse
import asyncio
import os
import sys

import asyncpg
from alembic.config import Config
from alembic import command

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import CONTROL_PLANE_DB_NAME, DB_HOST, DB_PASSWORD, DB_PORT, DB_USER  # noqa: E402

ALEMBIC_INI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "alembic.ini")

_NON_TENANT_SCHEMAS = {"public", "pg_catalog", "information_schema", "pg_toast", "keycloak"}


async def _discover_tenant_ids() -> list[str]:
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=CONTROL_PLANE_DB_NAME
    )
    try:
        registered_rows = await conn.fetch("SELECT tenant_id FROM tenants ORDER BY tenant_id")
        registered = [r["tenant_id"] for r in registered_rows]

        schema_rows = await conn.fetch(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name != ALL($1)",
            list(_NON_TENANT_SCHEMAS),
        )
        existing_schemas = {r["schema_name"] for r in schema_rows}
    finally:
        await conn.close()

    missing_schema = [t for t in registered if t not in existing_schemas]
    if missing_schema:
        print(
            f"[apply_all_tenants] NOTE: {len(missing_schema)} tenant(s) are registered in "
            f"`tenants` but have no Postgres schema yet - the baseline migration will CREATE "
            f"a fresh empty schema for them (same as get_pool() already does lazily on first "
            f"request), not skip them: {', '.join(missing_schema)}"
        )

    return registered


def _upgrade_one_tenant(tenant_id: str) -> None:
    cfg = Config(ALEMBIC_INI)
    cfg.attributes["target"] = "tenant"
    cfg.attributes["schema"] = tenant_id
    command.upgrade(cfg, "tenant@head")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="TENANT_ID",
        help="Limit to these tenant_ids instead of every tenant registered in the control plane.",
    )
    args = parser.parse_args()

    tenant_ids = asyncio.run(_discover_tenant_ids())
    if args.only:
        wanted = set(args.only)
        unknown = wanted - set(tenant_ids)
        if unknown:
            print(f"[apply_all_tenants] WARNING: not in the `tenants` table, skipping: {', '.join(sorted(unknown))}")
        tenant_ids = [t for t in tenant_ids if t in wanted]

    if not tenant_ids:
        print("[apply_all_tenants] No tenants to migrate.")
        return

    print(f"[apply_all_tenants] Applying tenant-branch migrations to {len(tenant_ids)} tenant schema(s)...")
    failures = []
    for tenant_id in tenant_ids:
        print(f"[apply_all_tenants] -> {tenant_id}")
        try:
            _upgrade_one_tenant(tenant_id)
        except Exception as exc:
            print(f"[apply_all_tenants]    FAILED: {exc}")
            failures.append(tenant_id)

    if failures:
        print(f"[apply_all_tenants] Done with {len(failures)} failure(s): {', '.join(failures)}")
        sys.exit(1)
    print("[apply_all_tenants] Done - all tenant schemas up to date.")


if __name__ == "__main__":
    main()
