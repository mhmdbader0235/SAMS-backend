"""
create_multitenant_demo_users.py — one-off seeder for 3 multi-school demo
accounts, exercised through the real registration path (AuthService.register_user)
so each gets the exact same control-plane rows (user_tenant_map /
parent_tenant_links) and Keycloak sync a live registration would produce.

Accounts created (password for all: 123321):
  parentteacher@example.com  -> parent  @ tenant_a, teacher @ tenant_b
  managerparent@example.com  -> manager @ tenant_a, parent  @ tenant_b
  twokids@example.com        -> parent  @ tenant_a (child "Kid One")
                                 parent  @ tenant_b (child "Kid Two")

Each is idempotent: re-running skips a (email, tenant_id) pair that is
already registered rather than erroring on "Email already registered".

Usage:
    cd back && python scripts/create_multitenant_demo_users.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.domains.auth.service import AuthService
from app.domains.tenant.control_plane_repository import ControlPlaneRepository
from app.domains.tenant.service import TenantService
from app.domains.tenant.user_repository import UserRepository
from app.core.database import get_control_plane_pool, get_db_pool

PASSWORD = "123321"
INVITE_CODE = "regester123"  # accepted for teacher/manager/student per AuthService.register_user


async def _register(email: str, role: str, tenant_id: str, name: str | None = None) -> None:
    try:
        await AuthService.register_user(
            email=email,
            password=PASSWORD,
            role=role,
            tenant_id=tenant_id,
            invite_code=INVITE_CODE,
            name=name,
        )
        print(f"  [+] Registered {email} as {role} @ {tenant_id}")
    except ValueError as exc:
        if "already registered" in str(exc).lower():
            print(f"  [=] {email} already registered @ {tenant_id} as {role} (skipped)")
        else:
            raise


async def _register_student_and_link(
    email_parent: str, tenant_id: str, child_email: str, child_name: str
) -> None:
    """Register a student in `tenant_id` and link them to the parent
    identified by `email_parent`'s local (per-tenant) parent row."""
    pool = await get_db_pool(tenant_id)
    user_repo = UserRepository(pool)

    if not await user_repo.get_user_by_email(child_email):
        await AuthService.register_user(
            email=child_email,
            password=PASSWORD,
            role="student",
            tenant_id=tenant_id,
            invite_code=INVITE_CODE,
            name=child_name,
        )
        print(f"  [+] Registered student {child_email} @ {tenant_id}")
    else:
        print(f"  [=] Student {child_email} already exists @ {tenant_id} (skipped)")

    child = await user_repo.get_user_by_email(child_email)
    parent_local = await user_repo.get_user_by_email(email_parent)
    if not child or not parent_local:
        print(f"  [!] Could not resolve ids to link {child_email} -> {email_parent} @ {tenant_id}")
        return

    await TenantService.link_student_parent(
        tenant_id=tenant_id,
        student_id=child["id"],
        parent_id=parent_local["id"],
        user_role="school_admin",  # script runs with admin authority, bypassing the router
        relationship_type="parent",
        is_primary_contact=True,
        can_approve=True,
    )
    print(f"  [+] Linked {child_email} -> {email_parent} @ {tenant_id}")


async def main() -> None:
    # Sanity check: control-plane pool must be reachable before anything else runs.
    await get_control_plane_pool()

    print("== parentteacher@example.com: parent @ tenant_a, teacher @ tenant_b ==")
    await _register("parentteacher@example.com", "parent", "tenant_a", name="Parent Teacher")
    await _register("parentteacher@example.com", "teacher", "tenant_b", name="Parent Teacher")

    print("== managerparent@example.com: manager @ tenant_a, parent @ tenant_b ==")
    await _register("managerparent@example.com", "manager", "tenant_a", name="Manager Parent")
    await _register("managerparent@example.com", "parent", "tenant_b", name="Manager Parent")

    print("== twokids@example.com: parent @ tenant_a and tenant_b, one child per school ==")
    await _register("twokids@example.com", "parent", "tenant_a", name="Two Kids")
    await _register("twokids@example.com", "parent", "tenant_b", name="Two Kids")
    await _register_student_and_link(
        "twokids@example.com", "tenant_a", "kid.one.twokids@example.com", "Kid One"
    )
    await _register_student_and_link(
        "twokids@example.com", "tenant_b", "kid.two.twokids@example.com", "Kid Two"
    )

    print("\nDone. All 3 accounts use password: 123321")
    print("Login with just the email/password (no tenant_id) to get the school picker.")


if __name__ == "__main__":
    asyncio.run(main())
