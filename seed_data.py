"""
seed_data.py — SchoolDesk Multi-Tenant Demo Data Seeder
======================================================
Calls the running API to delete existing data and re-populate BOTH tenant_a and tenant_b databases:

  • 3 Levels    (Grade 7, Grade 8, Grade 9) per tenant
  • 6 Teachers  (2 per level) per tenant
  • 6 Classes   (7A, 7B, 8A, 8B, 9A, 9B) per tenant
  • 18 Students (3 per class) per tenant
  • 6 Parents   (each linked to 3 students) per tenant
  • Events in various workflow states (Draft, Proposed, Finance Approval, Published) per tenant

All users registered via API will be automatically synced with Keycloak and added to their respective Keycloak Organization (tenant_a / tenant_b)!

All passwords  : 123321
Teacher invite : SCHOOL-STAFF-2026

Usage:
    python seed_data.py
"""

import asyncio
import os
from datetime import datetime, timedelta

import asyncpg
import httpx

# ─── Config ──────────────────────────────────────────────────────────────────
BASE_URL       = os.getenv("BASE_URL", "http://127.0.0.1:8001")
TENANTS        = ["tenant_a", "tenant_b"]
PASSWORD       = os.getenv("PASSWORD", "123321")
TEACHER_INVITE = os.getenv("TEACHER_INVITE", "SCHOOL-STAFF-2026")

DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT     = int(os.getenv("DB_PORT", "5433"))
DB_USER     = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "secure_local_password")
CONTROL_DB  = os.getenv("CONTROL_DB", "user_service_db")

HEADERS = {"Content-Type": "application/json"}

# ─── HTTP helpers ─────────────────────────────────────────────────────────────

async def api_post(client, path, body, token=None):
    hdrs = {**HEADERS}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    
    last_exc = None
    for attempt in range(6):
        try:
            r = await client.post(f"{BASE_URL}{path}", json=body, headers=hdrs, timeout=30)
            if r.status_code >= 300:
                raise RuntimeError(f"POST {path} => {r.status_code}: {r.text}")
            return r.json()
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            last_exc = exc
            await asyncio.sleep(1.5)
        except RuntimeError as exc:
            # If server returned an error response (like 422 or 400), don't retry unnecessarily
            raise exc
            
    if last_exc:
        raise last_exc


async def api_get(client, path, token):
    hdrs = {**HEADERS, "Authorization": f"Bearer {token}"}
    last_exc = None
    for attempt in range(6):
        try:
            r = await client.get(f"{BASE_URL}{path}", headers=hdrs, timeout=30)
            if r.status_code >= 300:
                raise RuntimeError(f"GET {path} => {r.status_code}: {r.text}")
            return r.json()
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            last_exc = exc
            await asyncio.sleep(1.5)
        except RuntimeError as exc:
            raise exc
            
    if last_exc:
        raise last_exc


# ─── DB wipe ─────────────────────────────────────────────────────────────────

async def clear_tenant_db(tenant_id: str):
    print(f"\nClearing tenant DB schema: '{tenant_id}' ...")
    try:
        from app.core.database import get_db_pool
        await get_db_pool(tenant_id)
    except Exception:
        pass

    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    try:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}";')
        await conn.execute(f'SET search_path TO "{tenant_id}", public;')
        try:
            await conn.execute("""
                TRUNCATE TABLE
                    notifications,
                    student_health_and_records,
                    payments,
                    enrollment,
                    event_class_map,
                    resource_cost,
                    resources,
                    resource_types,
                    event,
                    student_parent_map,
                    students,
                    parenets,
                    teachers,
                    class,
                    users,
                    levels
                RESTART IDENTITY CASCADE;
            """)
        except asyncpg.exceptions.UndefinedTableError:
            tables = [
                "notifications", "student_health_and_records", "payments", "enrollment",
                "event_class_map", "resource_cost", "resources", "resource_types", "event",
                "student_parent_map", "students", "parenets", "teachers", "class", "levels", "users"
            ]
            for table in tables:
                try:
                    await conn.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;")
                except asyncpg.exceptions.UndefinedTableError:
                    pass
        
        # Re-seed system resource types after clearing the table
        try:
            await conn.execute("""
                INSERT INTO resource_types (name, category, is_custom, created_by_user_id, is_active)
                VALUES
                    ('20-Seat Bus', 'transport', false, NULL, true),
                    ('40-Seat Bus', 'transport', false, NULL, true),
                    ('Male Supervisor', 'staffing', false, NULL, true),
                    ('Female Supervisor', 'staffing', false, NULL, true),
                    ('Kids Meal', 'meals', false, NULL, true),
                    ('Adult Meal', 'meals', false, NULL, true);
            """)
        except asyncpg.exceptions.UndefinedTableError:
            pass
            
        print(f"    Schema '{tenant_id}' cleared and system resource types re-seeded.")
    finally:
        await conn.close()


async def clear_control_plane_db():
    print("\nClearing control-plane parent rows ...")
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    try:
        await conn.execute("TRUNCATE TABLE parent_child_links, parent_tenant_links, parents CASCADE;")
        print("    Control-plane rows cleared.")
    finally:
        await conn.close()


# ─── Tenant Seeder ────────────────────────────────────────────────────────────

async def seed_tenant(client: httpx.AsyncClient, tenant_id: str):
    print(f"\n=======================================================")
    print(f"  Seeding Data for Tenant: {tenant_id}")
    print(f"=======================================================\n")
    
    report = {"admin": {}, "levels": [], "classes": [], "teachers": [],
              "students": [], "parents": [], "events": []}

    # Primary admin email
    admin_email = f"admin.{tenant_id}@school.com"
    alt_admin_email = "admin@school.com" if tenant_id == "tenant_a" else "admin_b@school.com"

    # ── Admin ──────────────────────────────────────────────────────────
    print(f"  > Admin users for {tenant_id}")
    try:
        r = await api_post(client, "/api/v1/auth/register", {
            "email": admin_email, "password": PASSWORD,
            "tenant_id": tenant_id, "role": "school_admin",
        })
    except RuntimeError:
        r = await api_post(client, "/api/v1/auth/login", {
            "email": admin_email, "password": PASSWORD, "tenant_id": tenant_id,
        })
    tok = r["access_token"]
    report["admin"] = {"email": admin_email, "password": PASSWORD, "role": "school_admin"}
    print(f"    {admin_email}  [OK]")

    try:
        await api_post(client, "/api/v1/auth/register", {
            "email": alt_admin_email, "password": PASSWORD,
            "tenant_id": tenant_id, "role": "school_admin",
        })
    except RuntimeError:
        pass
    print(f"    {alt_admin_email}  [OK]")

    # Seeding staff users
    mgr_email = f"manager.{tenant_id}@school.com"

    for email, role in [(mgr_email, "manager")]:
        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": email, "password": PASSWORD,
                "tenant_id": tenant_id, "role": role,
                "invite_code": TEACHER_INVITE,
            })
        except RuntimeError:
            pass
        print(f"    {email} [{role}] [OK]")

    # If tenant_a, also add legacy school.com accounts for easy backward compatibility
    if tenant_id == "tenant_a":
        for legacy_email, role in [("manager@school.com", "manager")]:
            try:
                await api_post(client, "/api/v1/auth/register", {
                    "email": legacy_email, "password": PASSWORD,
                    "tenant_id": tenant_id, "role": role,
                    "invite_code": TEACHER_INVITE,
                })
            except RuntimeError:
                pass


    # ── Levels ─────────────────────────────────────────────────────────
    print("\n  > Levels")
    level_ids = []
    for name in ["Grade 7", "Grade 8", "Grade 9"]:
        r = await api_post(client, "/api/v1/students/levels", {"name": name}, tok)
        level_ids.append(r["level_id"])
        report["levels"].append({"id": r["level_id"], "name": name})
        print(f"    {name} -> id={r['level_id']}")

    # ── Teachers ───────────────────────────────────────────────────────
    print("\n  > Teachers")
    teachers_meta = [
        {"email": f"ali.hassan.{tenant_id}@school.com",     "name": f"Ali Hassan ({tenant_id})"},
        {"email": f"sara.karim.{tenant_id}@school.com",     "name": f"Sara Karim ({tenant_id})"},
        {"email": f"omar.nasser.{tenant_id}@school.com",    "name": f"Omar Nasser ({tenant_id})"},
        {"email": f"lina.farouk.{tenant_id}@school.com",    "name": f"Lina Farouk ({tenant_id})"},
        {"email": f"hassan.mahmoud.{tenant_id}@school.com", "name": f"Hassan Mahmoud ({tenant_id})"},
        {"email": f"dina.rabie.{tenant_id}@school.com",     "name": f"Dina Rabie ({tenant_id})"},
    ]
    for tm in teachers_meta:
        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": tm["email"], "password": PASSWORD,
                "tenant_id": tenant_id, "role": "teacher",
                "invite_code": TEACHER_INVITE,
            })
        except RuntimeError:
            pass

    teachers_list = await api_get(client, "/api/v1/students/teachers", tok)
    email_to_tid  = {t["email"]: t["id"] for t in teachers_list}
    teacher_ids   = []
    for tm in teachers_meta:
        tid = email_to_tid[tm["email"]]
        teacher_ids.append(tid)
        report["teachers"].append({
            "id": tid, "name": tm["name"],
            "email": tm["email"], "password": PASSWORD, "role": "teacher",
        })
        print(f"    {tm['name']} ({tm['email']}) -> id={tid}")

    # ── Classes ────────────────────────────────────────────────────────
    print("\n  > Classes")
    classes_def = [
        {"name": "7A", "level_idx": 0, "teacher_idx": 0},
        {"name": "7B", "level_idx": 0, "teacher_idx": 1},
        {"name": "8A", "level_idx": 1, "teacher_idx": 2},
        {"name": "8B", "level_idx": 1, "teacher_idx": 3},
        {"name": "9A", "level_idx": 2, "teacher_idx": 4},
        {"name": "9B", "level_idx": 2, "teacher_idx": 5},
    ]
    class_ids = []
    level_names = ["Grade 7", "Grade 8", "Grade 9"]
    for cd in classes_def:
        r = await api_post(client, "/api/v1/students/classes", {
            "name": cd["name"],
            "level_id": level_ids[cd["level_idx"]],
            "head_teacher_id": teacher_ids[cd["teacher_idx"]],
        }, tok)
        cid = r["id"]
        class_ids.append(cid)
        report["classes"].append({
            "id": cid, "name": cd["name"],
            "level": level_names[cd["level_idx"]],
            "head_teacher": teachers_meta[cd["teacher_idx"]]["name"],
        })
        print(f"    {cd['name']} ({level_names[cd['level_idx']]}) -> id={cid}")

    # ── Students ───────────────────────────────────────────────────────
    print("\n  > Students (3 per class)")
    first_names = [
        "Ahmed",   "Mariam",  "Khalid",
        "Fatima",  "Youssef", "Nour",
        "Ziad",    "Layla",   "Tariq",
        "Hana",    "Sami",    "Rana",
        "Karim",   "Dalia",   "Rami",
        "Heba",    "Adel",    "Salma",
    ]
    student_ids = []
    si = 0
    for cidx, cid in enumerate(class_ids):
        for j in range(3):
            fname = first_names[si]
            email = f"{fname.lower()}.s{si+1}.{tenant_id}@school.com"
            r = await api_post(client, "/api/v1/students", {
                "email": email, "password": PASSWORD,
                "name": fname, "class_id": cid,
                "gender": "male" if si % 2 == 0 else "female",
                "birth_data": f"200{(si%9)+1}-0{(si%9)+1}-{(si%28)+1:02d}",
            }, tok)
            sid = r["id"]
            student_ids.append(sid)
            report["students"].append({
                "id": sid, "name": fname, "email": email,
                "password": PASSWORD, "role": "student",
                "class": report["classes"][cidx]["name"],
                "level": report["classes"][cidx]["level"],
            })
            print(f"    {fname} ({email}) -> class {report['classes'][cidx]['name']}, id={sid}")
            si += 1

    # ── Parents ────────────────────────────────────────────────────────
    print("\n  > Parents (each linked to 3 students)")
    parents_meta = [
        {"email": f"parent.al1.{tenant_id}@school.com", "name": f"Parent Al-Rashid ({tenant_id})"},
        {"email": f"parent.bn2.{tenant_id}@school.com", "name": f"Parent Ben-Nour ({tenant_id})"},
        {"email": f"parent.cm3.{tenant_id}@school.com", "name": f"Parent Chami ({tenant_id})"},
        {"email": f"parent.da4.{tenant_id}@school.com", "name": f"Parent Darwish ({tenant_id})"},
        {"email": f"parent.el5.{tenant_id}@school.com", "name": f"Parent El-Sayed ({tenant_id})"},
        {"email": f"parent.fa6.{tenant_id}@school.com", "name": f"Parent Farouk ({tenant_id})"},
    ]
    for pidx, pm in enumerate(parents_meta):
        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": pm["email"], "password": PASSWORD,
                "tenant_id": tenant_id, "role": "parent",
            })
        except RuntimeError as e:
            pass

        parents_list  = await api_get(client, "/api/v1/students/parents", tok)
        email_to_pid  = {p["email"]: p["id"] for p in parents_list}
        pid           = email_to_pid[pm["email"]]

        child_names = []
        for j in range(3):
            s_idx = pidx * 3 + j
            s_id  = student_ids[s_idx]
            await api_post(client, "/api/v1/students/link-parent", {
                "student_id": s_id, "parent_id": pid,
            }, tok)
            child_names.append(report["students"][s_idx]["name"])

        report["parents"].append({
            "id": pid, "name": pm["name"], "email": pm["email"],
            "password": PASSWORD, "role": "parent",
            "linked_students": child_names,
        })
        print(f"    {pm['name']} ({pm['email']}) -> id={pid}, children: {', '.join(child_names)}")

    # ── Events ─────────────────────────────────────────────────────────
    print("\n  > Events")
    
    t_login = await api_post(client, "/api/v1/auth/login", {
        "email": teachers_meta[0]["email"], "password": PASSWORD, "tenant_id": tenant_id
    })
    teacher_tok = t_login["access_token"]

    events_def = [
        {"title": f"Science Fair ({tenant_id})",            "class_idx": 0, "subsidy": 20.0, "ticket": 15.0, "days": 10,
         "desc": "Annual science fair for Grade 7 class 7A.", "address": "Main Hall, 1st Floor",
         "budget_desc": "Lab materials", "budget_price": 200.0},
        {"title": f"Math Olympiad ({tenant_id})",                 "class_idx": 1, "subsidy": 10.0, "ticket":  5.0, "days": 45,
         "desc": "Math competition for class 7B students.",   "address": "Room 101",
         "budget_desc": "Stationery & prizes", "budget_price": 150.0},
        {"title": f"History Trip ({tenant_id})",         "class_idx": 2, "subsidy": 30.0, "ticket": 25.0, "days": 15,
         "desc": "Educational visit to National Museum for 8A.", "address": "National Museum",
         "budget_desc": "Transport & entry fees", "budget_price": 400.0},
        {"title": f"Art Exhibition ({tenant_id})",                "class_idx": 3, "subsidy": 15.0, "ticket": 10.0, "days": 60,
         "desc": "Student artworks showcase for class 8B.", "address": "Gallery Room",
         "budget_desc": "Art supplies & frames", "budget_price": 300.0},
    ]
    for ev in events_def:
        dt = (datetime.utcnow() + timedelta(days=ev["days"])).strftime("%Y-%m-%dT%H:%M:%S")
        r  = await api_post(client, "/api/v1/events", {
            "title": ev["title"],
            "description": ev["desc"],
            "address": ev["address"],
            "school_subsidy": ev["subsidy"],
            "date": dt,
            "class_mappings": [{
                "class_id": class_ids[ev["class_idx"]],
                "ticket_price": ev["ticket"],
                "budgets": [{"description": ev["budget_desc"], "price": ev["budget_price"]}],
            }],
        }, teacher_tok)
        eid   = r["id"]
        cname = report["classes"][ev["class_idx"]]["name"]
        clvl  = report["classes"][ev["class_idx"]]["level"]
        report["events"].append({
            "id": eid, "title": ev["title"],
            "target_class": cname, "target_level": clvl,
            "ticket_price": ev["ticket"], "school_subsidy": ev["subsidy"], "date": dt,
        })
        print(f"    '{ev['title']}' -> {cname} ({clvl}), id={eid}")

    return report


async def set_parent_phones(tenant_id: str):
    print(f"\nSetting fake phone numbers for parents in {tenant_id} ...")
    conn_t = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    await conn_t.execute(f'SET search_path TO "{tenant_id}", public;')
    conn_cp = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    try:
        parents = await conn_t.fetch("SELECT id, name FROM parenets")
        for i, parent in enumerate(parents):
            fake_phone = f"+1-555-010{i+1}"
            parent_id = parent["id"]
            await conn_t.execute("UPDATE users SET phone = $1 WHERE id = $2", fake_phone, parent_id)
            await conn_t.execute("UPDATE parenets SET phone = $1 WHERE id = $2", fake_phone, parent_id)
            
            email = await conn_t.fetchval("SELECT email FROM users WHERE id = $1", parent_id)
            if email:
                await conn_cp.execute("UPDATE parents SET phone = $1 WHERE email = $2", fake_phone, email)
        print("    Fake phone numbers seeded successfully.")
    finally:
        await conn_t.close()
        await conn_cp.close()


# ─── Entry ────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  SchoolDesk Multi-Tenant Seeder (Keycloak Synced)")
    print("=" * 60)
    await clear_control_plane_db()
    
    async with httpx.AsyncClient() as client:
        for tenant in TENANTS:
            await clear_tenant_db(tenant)
            await seed_tenant(client, tenant)
            await set_parent_phones(tenant)

    print("\nAll tenants (tenant_a & tenant_b) seeded successfully & synced with Keycloak!")


if __name__ == "__main__":
    asyncio.run(main())
