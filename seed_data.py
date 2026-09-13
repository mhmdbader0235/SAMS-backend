"""
seed_data.py — SAMS Comprehensive Multi-Tenant Demo Seeder

Comprehensive seed covering 100+ users across every tenant (tenant_a,
tenant_b, tenant_c), with every core app feature represented:
- 4 grade levels x 3 classes per level, per tenant
- 15 teachers, 1 school_admin, 1 manager per tenant
- 60 students (~5 per class) with 8 parents linked
- 8 events spread across every lifecycle state (draft/proposed/approved/published)
- Enrollments, payments, and resources (with costs) for each event
- School profile, campus, and a real admin contact per tenant (Day-1 onboarding data)
- Every registered super_admin gets a real, visible account in every tenant

Direct database writes + Keycloak sync via sync_user_to_keycloak().
No external dependencies beyond the app codebase itself.

Usage:
    python seed_data.py

Credentials (all tenants use password: 123321):
    super_admin:    sa@desk.com (and any other row in public.super_admins)
    admin:          admin.a@{tenant}.com
    manager:        manager.a@{tenant}.com
    teachers:       teacher.{1..15}@{tenant}.com
    students:       student.{1..60}@{tenant}.com
    parents:        parent.{1..8}@{tenant}.com
    (where {tenant} is tenanta / tenantb / tenantc -- see email_domain())
"""

import asyncio
import asyncpg
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.domains.auth.service import AuthService
from app.core.keycloak_admin import sync_user_to_keycloak
from app.core.database import get_db_pool

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8000")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "SAMS")


def wait_for_keycloak(timeout_s: int = 90) -> bool:
    """Poll Keycloak's realm endpoint until it responds, so the sync calls
    below don't race a container that is still booting -- this is what
    caused the 'Remote end closed connection' / WinError 10053 failures
    when this script previously ran seconds after `docker-compose up -d`."""
    url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    print(f"  [+] Keycloak is ready (after {attempt} check(s))")
                    return True
        except Exception:
            pass
        time.sleep(2)
    print(f"  [!] Keycloak did not become ready within {timeout_s}s -- continuing anyway")
    return False


def sync_with_retry(email, password, role, tenant_id=None, first_name=None, last_name=None, attempts=4):
    """sync_user_to_keycloak() swallows its own errors and returns False rather
    than raising, so a transient failure (Keycloak still warming up, a
    connection reset under rapid sequential admin-token requests) would
    otherwise silently leave a user un-synced. Retry with backoff and report
    the final outcome so failures are visible instead of silent."""
    for i in range(attempts):
        ok = sync_user_to_keycloak(email, password, role, tenant_id, first_name, last_name)
        if ok:
            return True
        time.sleep(0.5 * (i + 1))
    print(f"    [!] Keycloak sync FAILED after {attempts} attempts: {email}")
    return False

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5433"))
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "secure_local_password")
CONTROL_DB = os.getenv("CONTROL_DB", "user_service_db")
TENANTS = ["tenant_a", "tenant_b", "tenant_c"]
PASSWORD = "123321"
PASSWORD_HASH = AuthService.hash_password(PASSWORD)

LEVELS_PER_TENANT = 4
CLASSES_PER_LEVEL = 3
TEACHERS_PER_TENANT = 15
STUDENTS_PER_TENANT = 60
PARENTS_PER_TENANT = 8
EVENTS_PER_TENANT = 8


def email_domain(tenant_id: str) -> str:
    """Keycloak's realm User Profile email validator rejects underscores in
    the domain part (confirmed: "x@tenant_a.com" -> error-invalid-email,
    "x@tenanta.com" -> 201 Created) -- every tenant_id here has one
    ("tenant_a", "tenant_b", "tenant_c"), so strip it for the email domain
    while keeping the real tenant_id for the DB/attributes."""
    return tenant_id.replace("_", "")


async def main():
    print("=" * 80)
    print("SAMS Comprehensive Seed v2 (Direct DB + Keycloak Sync)")
    print("=" * 80)

    try:
        # Step 0: Wait for Keycloak to be ready before any sync call
        print("\n[0/5] Waiting for Keycloak readiness...")
        wait_for_keycloak()

        # Step 1: Control plane setup
        print("\n[1/5] Setting up control plane...")
        await setup_control_plane()

        # Step 2-4: Seed each tenant
        for tenant_id in TENANTS:
            print(f"\n[2-4/5] Seeding {tenant_id}...")
            await seed_tenant(tenant_id)

        # Step 5: Summary
        print("\n[5/5] Seed complete!")
        print_summary()

    except Exception as e:
        print(f"\n[FATAL] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


async def setup_control_plane():
    """Ensure control plane tables and super_admin exist."""
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    try:
        # Ensure tenants
        for tenant_id in TENANTS:
            await conn.execute(
                """INSERT INTO tenants (tenant_id, name, db_host, db_port, db_user, db_password, db_name)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (tenant_id) DO NOTHING""",
                tenant_id, tenant_id.title(), DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, CONTROL_DB,
            )

        # Ensure super_admin
        sa_email = "sa@desk.com"
        existing = await conn.fetchval("SELECT id FROM super_admins WHERE email = $1", sa_email)
        if not existing:
            await conn.execute(
                "INSERT INTO super_admins (email, password_hash) VALUES ($1, $2)",
                sa_email, PASSWORD_HASH,
            )
            sync_with_retry(sa_email, PASSWORD, "super_admin")
            print("  [+] Created super_admin")
        else:
            print("  [+] Super admin already exists")

        print(f"  [+] Ensured {len(TENANTS)} tenants in control plane")
    finally:
        await conn.close()


async def seed_tenant(tenant_id: str):
    """Seed a complete tenant schema."""
    # Trigger the app's own schema provisioning (_initialize_tenant_tables)
    # so a tenant whose schema was dropped/never created (e.g. tenant_b after
    # a cleanup) gets every table this script assumes exists, instead of
    # failing on the first INSERT with UndefinedTableError.
    await get_db_pool(tenant_id)

    # Use direct pool for this tenant
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    async def map_user(email: str, role: str) -> None:
        """Register email -> tenant_id/role in the control-plane user_tenant_map.

        Without this, a seeded user exists in the tenant's `users` table but
        is invisible to the Keycloak-SSO tenant-resolution lookup
        (ControlPlaneRepository.get_tenant_for_email) -- login then failed
        closed (previously: defaulted to tenant_a), couldn't find the user
        there, and JIT-provisioned a stray 'pending' account in the wrong
        tenant. This is what happened to student.11@tenantb.com; every user
        this script creates needs this call, mirroring what the live
        register()/login()/create_* paths do via
        _upsert_user_tenant_mapping / upsert_user_tenant_map.

        Conflicts on (email, tenant_id), matching those two -- see alembic
        cp_0002. This used to conflict on (email) alone, which not only
        collapsed a user seeded into two tenants down to whichever ran last,
        but now raises "no unique or exclusion constraint matching the ON
        CONFLICT specification" outright, since the table's real constraint
        is the wider one.
        """
        # cp_0004 made identity_id NOT NULL on user_tenant_map; resolve/create
        # the identities row first or this INSERT fails NOT NULL for any
        # email seeded here for the first time.
        identity_id = await conn.fetchval(
            """
            INSERT INTO identities (email)
            VALUES ($1)
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING id
            """,
            email.strip().lower(),
        )
        await conn.execute(
            """
            INSERT INTO user_tenant_map (identity_id, email, tenant_id, role, updated_at)
            VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
            ON CONFLICT (email, tenant_id) DO UPDATE
                SET role = EXCLUDED.role,
                    updated_at = CURRENT_TIMESTAMP
            """,
            identity_id, email.strip().lower(), tenant_id, role,
        )

    try:
        await conn.execute(f'SET search_path TO "{tenant_id}", public')

        # 1. Clear demo data
        tables = [
            "notifications", "student_health_and_records", "payments",
            "enrollment", "resource_cost", "resources", "event_class_map",
            "event_feedback", "event", "student_parent_map", "students",
            "parenets", "class", "teachers", "levels", "blackout_dates",
            "academic_settings", "resource_types", "users"
        ]
        for t in tables:
            try:
                await conn.execute(f'TRUNCATE TABLE "{tenant_id}"."{t}" RESTART IDENTITY CASCADE')
            except:
                pass

        # 1b. Truncating `users` above wipes the real super_admin rows that
        # _initialize_tenant_tables() (app/core/database.py) auto-provisions
        # for every public.super_admins entry -- restore them immediately so
        # a reseed doesn't silently undo that. Mirrors that function's own
        # guarded INSERT exactly, so both stay in sync.
        await conn.execute(
            """
            INSERT INTO users (email, role, password_hash)
            SELECT sa.email, 'super_admin', sa.password_hash
            FROM public.super_admins sa
            WHERE NOT EXISTS (
                SELECT 1 FROM users u WHERE u.email = sa.email
            )
            """
        )

        # 2. Setup school profile
        await conn.execute("""
            INSERT INTO school_profile
            (legal_name, display_name, school_code, country, timezone, currency,
             profile_committed_at, structure_committed_at, curriculum_locked_at, activated_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW(), NOW(), NOW())
        """, f"School {tenant_id}", f"{tenant_id.title()} School",
            f"SCH-{tenant_id.upper()}", "Jordan", "Asia/Amman", "JOD")

        # 2b. Real admin contact -- the "Contact Administrator" support modal
        # (UserPendingRoleView.vue) fetches GET /api/v1/school/contacts and
        # shows the first one; without a real row it fabricates a fake
        # "admin@{tenant}.school.com" address that doesn't correspond to any
        # actual account. This row makes that surface a real, working login.
        existing_contact = await conn.fetchval(
            "SELECT id FROM school_contact WHERE role_title = 'School Administrator'"
        )
        if not existing_contact:
            await conn.execute(
                """INSERT INTO school_contact
                   (role_title, name, phone, email, is_emergency_contact, escalation_order, visible_to)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                "School Administrator", "Admin A", "+962791234567",
                f"admin.a@{email_domain(tenant_id)}.com", True, 1, ["staff", "pending"],
            )

        # 3. Academic settings
        await conn.execute("""
            INSERT INTO academic_settings (academic_year, start_month, weekend_days, system)
            VALUES ($1, $2, $3, $4)
        """, "2026-2027", 9, ["Saturday", "Sunday"], "US")

        # 4. Blackout dates
        await conn.execute("""
            INSERT INTO blackout_dates (date, title, tags)
            VALUES ($1, $2, $3), ($4, $5, $6)
        """,
            datetime(2026, 12, 25).date(), "Christmas", ["winter"],
            datetime(2027, 4, 1).date(), "Spring Break", ["spring"])

        # 5. Resource types (system defaults)
        resource_types = [
            ("Bus (20-seat)", "transport", False),
            ("Bus (40-seat)", "transport", False),
            ("Adult Supervisor", "staff", False),
            ("Kid Meal", "meals", False),
            ("Adult Meal", "meals", False),
        ]
        for name, category, is_custom in resource_types:
            await conn.execute(
                "INSERT INTO resource_types (name, category, is_custom, is_active) VALUES ($1, $2, $3, $4)",
                name, category, is_custom, True,
            )

        # 6. Levels (grades)
        level_ids = []
        for i in range(1, LEVELS_PER_TENANT + 1):
            lid = await conn.fetchval(
                "INSERT INTO levels (name, isced_level, age_band_min, age_band_max, ordinal, is_active) VALUES ($1, $2, $3, $4, $5, $6) RETURNING level_id",
                f"Grade {i}", 1, 5 + i, 6 + i, i, True,
            )
            level_ids.append(lid)

        # 7. Users: admin, manager, teachers, students, parents
        all_user_ids = {}

        # Admin
        admin_email = f"admin.a@{email_domain(tenant_id)}.com"
        admin_id = await conn.fetchval(
            "INSERT INTO users (email, role, password_hash) VALUES ($1, $2, $3) RETURNING id",
            admin_email, "school_admin", PASSWORD_HASH,
        )
        all_user_ids["admin"] = (admin_id, admin_email)
        await map_user(admin_email, "school_admin")
        sync_with_retry(admin_email, PASSWORD, "school_admin", tenant_id, "Admin", "A")

        # Manager
        manager_email = f"manager.a@{email_domain(tenant_id)}.com"
        manager_id = await conn.fetchval(
            "INSERT INTO users (email, role, password_hash) VALUES ($1, $2, $3) RETURNING id",
            manager_email, "manager", PASSWORD_HASH,
        )
        all_user_ids["manager"] = (manager_id, manager_email)
        await map_user(manager_email, "manager")
        sync_with_retry(manager_email, PASSWORD, "manager", tenant_id, "Manager", "A")

        # Teachers
        teacher_ids = []
        for i in range(1, TEACHERS_PER_TENANT + 1):
            email = f"teacher.{i}@{email_domain(tenant_id)}.com"
            tid = await conn.fetchval(
                "INSERT INTO users (email, role, password_hash) VALUES ($1, $2, $3) RETURNING id",
                email, "teacher", PASSWORD_HASH,
            )
            teacher_ids.append(tid)
            await conn.execute(
                "INSERT INTO teachers (id, name) VALUES ($1, $2)",
                tid, f"Teacher {i}",
            )
            await map_user(email, "teacher")
            sync_with_retry(email, PASSWORD, "teacher", tenant_id, f"Teacher", str(i))

        # 8. Classes
        class_ids = []
        teacher_idx = 0
        for level_id in level_ids:
            for j in range(1, CLASSES_PER_LEVEL + 1):
                head_tid = teacher_ids[teacher_idx % len(teacher_ids)]
                teacher_idx += 1

                cid = await conn.fetchval(
                    "INSERT INTO class (name, level_id, head_teacher_id, capacity) VALUES ($1, $2, $3, $4) RETURNING id",
                    f"Grade {level_ids.index(level_id) + 1} Section {chr(65 + j - 1)}",
                    level_id, head_tid, 25,
                )
                class_ids.append(cid)

        # 9. Parents
        #
        # Every parent must exist in BOTH places: this tenant's local `users`/
        # `parenets` tables (below) AND the control-plane `public.parents`
        # table -- parents are documented as control-plane records that can
        # span tenants (see student_parent_map / parent_tenant_links), and
        # AuthService.login_user() checks public.parents FIRST, before ever
        # looking at the tenant-local table. A parent seeded only locally
        # (the previous behavior here) left exactly the gap that let
        # get_current_user's Keycloak-SSO JIT-provisioning path -- triggered
        # by any authenticated request, not just SSO -- fill it in later with
        # an unusable 'keycloak_managed' placeholder hash, permanently
        # shadowing the real password. Using the same PASSWORD_HASH for both
        # rows keeps them byte-for-byte consistent with each other.
        parent_ids = []
        for i in range(1, PARENTS_PER_TENANT + 1):
            email = f"parent.{i}@{email_domain(tenant_id)}.com"
            pid = await conn.fetchval(
                "INSERT INTO users (email, role, password_hash) VALUES ($1, $2, $3) RETURNING id",
                email, "parent", PASSWORD_HASH,
            )
            parent_ids.append(pid)
            await conn.execute(
                "INSERT INTO parenets (id, name, phone) VALUES ($1, $2, $3)",
                pid, f"Parent {i}", f"+962{77000000 + i:08d}",
            )
            global_parent_id = await conn.fetchval(
                """
                INSERT INTO public.parents (email, password_hash, phone)
                VALUES ($1, $2, $3)
                ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash
                RETURNING id
                """,
                email, PASSWORD_HASH, f"+962{77000000 + i:08d}",
            )
            await conn.execute(
                "INSERT INTO public.parent_tenant_links (parent_id, tenant_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                global_parent_id, tenant_id,
            )
            await map_user(email, "parent")
            sync_with_retry(email, PASSWORD, "parent", tenant_id, "Parent", str(i))

        # 10. Students
        student_ids = []
        students_per_class = max(1, STUDENTS_PER_TENANT // len(class_ids))
        for class_idx, cid in enumerate(class_ids):
            for s in range(students_per_class):
                global_idx = (class_idx * students_per_class) + s + 1
                email = f"student.{global_idx}@{email_domain(tenant_id)}.com"
                sid = await conn.fetchval(
                    "INSERT INTO users (email, role, password_hash) VALUES ($1, $2, $3) RETURNING id",
                    email, "student", PASSWORD_HASH,
                )
                student_ids.append(sid)
                await conn.execute(
                    "INSERT INTO students (id, name, class_id, gender) VALUES ($1, $2, $3, $4)",
                    sid, f"Student {global_idx}", cid, "M" if global_idx % 2 == 0 else "F",
                )
                await map_user(email, "student")
                sync_with_retry(email, PASSWORD, "student", tenant_id, "Student", str(global_idx))

        # 11. Link students to parents
        for s_idx, sid in enumerate(student_ids):
            p_count = min(2, len(parent_ids))
            for p_idx in range(p_count):
                pid = parent_ids[(s_idx + p_idx) % len(parent_ids)]
                try:
                    await conn.execute(
                        "INSERT INTO student_parent_map (student_id, parent_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        sid, pid,
                    )
                except:
                    pass

        # 12. Create events
        states = ["draft", "proposed", "approved", "published"]
        for e_idx in range(EVENTS_PER_TENANT):
            state = states[e_idx % len(states)]
            event_date = datetime.now(timezone.utc) + timedelta(days=30 + e_idx)

            eid = await conn.fetchval(
                """INSERT INTO event (title, description, address, date, created_by, status,
                   school_subsidy, predicted_attendance)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   RETURNING id""",
                f"Trip {e_idx + 1} ({state})",
                f"Sample field trip #{e_idx + 1}",
                f"Venue {e_idx + 1}, {tenant_id}",
                event_date,
                teacher_ids[e_idx % len(teacher_ids)],
                state,
                50.0 + (e_idx * 10),
                len(student_ids) // 2,
            )

            # Map to a class
            if class_ids:
                cid = class_ids[e_idx % len(class_ids)]
                ecm_id = await conn.fetchval(
                    "INSERT INTO event_class_map (event_id, class_id, ticket_price) VALUES ($1, $2, $3) RETURNING id",
                    eid, cid, 25.0,
                )

                # Create resources
                resource_type_ids = await conn.fetch(
                    "SELECT id FROM resource_types LIMIT 3"
                )
                for rt_idx, rt in enumerate(resource_type_ids):
                    rid = await conn.fetchval(
                        "INSERT INTO resources (event_id, resource_type_id, description, quantity, added_by_user_id) VALUES ($1, $2, $3, $4, $5) RETURNING id",
                        eid, rt["id"], f"Resource {rt_idx + 1}", 1 + rt_idx, teacher_ids[0],
                    )
                    # Set cost
                    try:
                        await conn.execute(
                            "INSERT INTO resource_cost (event_id, resource_id, unit_price, total_cost, currency, set_by_user_id) VALUES ($1, $2, $3, $4, $5, $6)",
                            eid, rid, 50.0 + (rt_idx * 25), 50.0 + (rt_idx * 25), "JOD", manager_id,
                        )
                    except:
                        pass

                # Create enrollments for some students -- only for published
                # events. A draft/proposed/approved trip has never been
                # announced to any class, so an "approved" enrollment on one
                # is a logical impossibility that showed up as real drafts
                # sitting in a student's "My Active Enrollments" list.
                if state == "published":
                    for s_idx in range(min(3, len(student_ids))):
                        sid = student_ids[s_idx]
                        try:
                            enr_id = await conn.fetchval(
                                "INSERT INTO enrollment (student_id, event_class_map_id, state) VALUES ($1, $2, $3) RETURNING id",
                                sid, ecm_id, "approved_by_teacher",
                            )
                            # Create payment
                            await conn.execute(
                                "INSERT INTO payments (enrollment_id, amount, status) VALUES ($1, $2, $3)",
                                enr_id, 25.0, "paid",
                            )
                        except:
                            pass

        print(f"  [+] Seeded {tenant_id}: {len(level_ids)} levels, {len(class_ids)} classes")
        print(f"       {len(teacher_ids)} teachers, {len(student_ids)} students, {len(parent_ids)} parents")
        print(f"       {EVENTS_PER_TENANT} events with enrollments")

    finally:
        await conn.close()


def print_summary():
    """Print login credentials for all seeded users."""
    print("\n" + "=" * 80)
    print("LOGIN CREDENTIALS (password: 123321 for all)")
    print("=" * 80)
    print("\nSUPER ADMIN:")
    print("  sa@desk.com")

    for tenant_id in TENANTS:
        print(f"\n{tenant_id.upper()}:")
        print(f"  admin.a@{email_domain(tenant_id)}.com (school admin)")
        print(f"  manager.a@{email_domain(tenant_id)}.com (manager)")
        print(f"  teacher.1@{email_domain(tenant_id)}.com ... teacher.{TEACHERS_PER_TENANT}@{email_domain(tenant_id)}.com")
        print(f"  student.1@{email_domain(tenant_id)}.com ... student.{STUDENTS_PER_TENANT}@{email_domain(tenant_id)}.com")
        print(f"  parent.1@{email_domain(tenant_id)}.com ... parent.{PARENTS_PER_TENANT}@{email_domain(tenant_id)}.com")

    print("\n" + "=" * 80)
    print(f"TOTAL: 2 tenants × ({1} admin + {1} manager + {TEACHERS_PER_TENANT} teachers")
    print(f"       + {STUDENTS_PER_TENANT} students + {PARENTS_PER_TENANT} parents) = {(1+1+TEACHERS_PER_TENANT+STUDENTS_PER_TENANT+PARENTS_PER_TENANT)*2} users/tenants")
    print(f"       + 1 super_admin = {(1+1+TEACHERS_PER_TENANT+STUDENTS_PER_TENANT+PARENTS_PER_TENANT)*2 + 1} TOTAL")
    print("=" * 80)


if __name__ == "__main__":
    try:
        asyncio.run(main())
        print("\nSeed successful! Run the app and login with any credential above.")
    except KeyboardInterrupt:
        print("\n\nSeed cancelled by user")
        sys.exit(1)
