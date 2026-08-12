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
            if "connection was closed" in str(exc) or "500:" in str(exc):
                last_exc = exc
                await asyncio.sleep(1.5)
                continue
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
            if "connection was closed" in str(exc) or "500:" in str(exc):
                last_exc = exc
                await asyncio.sleep(1.5)
                continue
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

async def clear_tenant_db(tenant_id: str):
    print(f"\nClearing tenant DB schema: '{tenant_id}' ...")
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    try:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}";')
        await conn.execute(f'SET search_path TO "{tenant_id}", public;')
        
        tables = [
            "notifications", "student_health_and_records", "payments", "enrollment",
            "event_class_map", "resource_cost", "resources", "resource_types", "event",
            "student_parent_map", "students", "parenets", "teachers", "class", "levels", "users"
        ]
        for table in tables:
            try:
                await conn.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;")
            except Exception:
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
        except Exception:
            pass
            
        print(f"    Schema '{tenant_id}' cleared and system resource types re-seeded.")
    finally:
        await conn.close()


async def clear_control_plane_db():
    print("\nClearing control-plane tables (user_tenant_map, parents, links, invitations) ...")
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    try:
        await conn.execute("TRUNCATE TABLE user_tenant_map, parent_child_links, parent_tenant_links, parents, invitations CASCADE;")
        print("    Control-plane rows cleared.")
    finally:
        await conn.close()


async def clear_keycloak_users():
    print("\nClearing old non-admin users from Keycloak realm 'SAMS' ...")
    KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8000")
    KEYCLOAK_ADMIN = os.getenv("KEYCLOAK_ADMIN", "admin")
    KEYCLOAK_ADMIN_PASSWORD = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
    KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "SAMS")
    
    token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(token_url, data={
                "client_id": "admin-cli",
                "username": KEYCLOAK_ADMIN,
                "password": KEYCLOAK_ADMIN_PASSWORD,
                "grant_type": "password"
            }, timeout=10)
            if r.status_code == 200:
                token = r.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}
                users_r = await client.get(f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users?max=1000", headers=headers, timeout=10)
                if users_r.status_code == 200:
                    users = users_r.json()
                    count = 0
                    for u in users:
                        username = u.get("username", "")
                        email = u.get("email", "")
                        if username in ("admin", "master", "sa@desk.com") or email in ("admin@master.com", "sa@desk.com"):
                            continue
                        uid = u["id"]
                        await client.delete(f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{uid}", headers=headers, timeout=10)
                        count += 1
                    print(f"    Cleared {count} Keycloak user records.")
        except Exception as exc:
            print(f"    Keycloak cleanup notice: {exc}")


async def seed_super_admin():
    print("\nSeeding Super Admin (sa@desk.com / password123) ...")
    from app.domains.auth.service import AuthService
    from app.core.keycloak_admin import sync_user_to_keycloak
    
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    try:
        pass_hash = AuthService.hash_password("password123")
        await conn.execute("""
            INSERT INTO super_admins (email, password_hash)
            VALUES ('sa@desk.com', $1)
            ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash
        """, pass_hash)
        
        sync_user_to_keycloak(
            email="sa@desk.com",
            password="password123",
            role="super_admin",
            tenant_id="tenant_a",
            first_name="Super",
            last_name="Admin",
        )
        print("    sa@desk.com seeded in Postgres and Keycloak [OK]")
    finally:
        await conn.close()


# ─── Tenant Seeder ────────────────────────────────────────────────────────────

async def seed_tenant(client: httpx.AsyncClient, tenant_id: str):
    domain = "schoola.com" if tenant_id == "tenant_a" else "schoolb.com"
    print(f"\n=======================================================")
    print(f"  Seeding Data for Tenant: {tenant_id} ({domain})")
    print(f"=======================================================\n")
    
    report = {"admin": {}, "levels": [], "classes": [], "teachers": [],
              "students": [], "parents": [], "events": []}

    # Primary admin email
    admin_email = f"admin@{domain}"
    alt_admin_email = "admin@school.com" if tenant_id == "tenant_a" else "admin_b@school.com"

    # ── Admin ──────────────────────────────────────────────────────────
    print(f"  > Admin users for {tenant_id}")
    try:
        r = await api_post(client, "/api/v1/auth/register", {
            "email": admin_email, "password": PASSWORD,
            "tenant_id": tenant_id, "role": "school_admin",
            "invite_code": TEACHER_INVITE,
            "first_name": "Admin", "last_name": "User"
        })
    except RuntimeError:
        r = await api_post(client, "/api/v1/auth/login", {
            "email": admin_email, "password": PASSWORD, "tenant_id": tenant_id,
        })
    tok = r["access_token"]
    report["admin"] = {"email": admin_email, "password": PASSWORD, "role": "school_admin"}
    print(f"    {admin_email}  [OK]")

    if tenant_id == "tenant_a":
        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": alt_admin_email, "password": PASSWORD,
                "tenant_id": tenant_id, "role": "school_admin",
                "invite_code": TEACHER_INVITE,
                "first_name": "Admin", "last_name": "Alt"
            })
        except RuntimeError:
            pass
        print(f"    {alt_admin_email}  [OK]")

    # Seeding staff users
    mgr_email = f"manager@{domain}"

    for email, role in [(mgr_email, "manager")]:
        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": email, "password": PASSWORD,
                "tenant_id": tenant_id, "role": role,
                "invite_code": TEACHER_INVITE,
                "first_name": "Manager", "last_name": "User"
            })
        except RuntimeError:
            pass
        print(f"    {email} [{role}] [OK]")

    if tenant_id == "tenant_a":
        for legacy_email, role in [("manager@school.com", "manager")]:
            try:
                await api_post(client, "/api/v1/auth/register", {
                    "email": legacy_email, "password": PASSWORD,
                    "tenant_id": tenant_id, "role": role,
                    "invite_code": TEACHER_INVITE,
                    "first_name": "Legacy", "last_name": "Manager"
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
        {"email": f"ali.hassan@{domain}",     "name": f"Ali Hassan ({tenant_id})"},
        {"email": f"sara.karim@{domain}",     "name": f"Sara Karim ({tenant_id})"},
        {"email": f"omar.nasser@{domain}",    "name": f"Omar Nasser ({tenant_id})"},
        {"email": f"lina.farouk@{domain}",    "name": f"Lina Farouk ({tenant_id})"},
        {"email": f"hassan.mahmoud@{domain}", "name": f"Hassan Mahmoud ({tenant_id})"},
        {"email": f"dina.rabie@{domain}",     "name": f"Dina Rabie ({tenant_id})"},
    ]
    for tm in teachers_meta:
        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": tm["email"], "password": PASSWORD,
                "tenant_id": tenant_id, "role": "teacher",
                "invite_code": TEACHER_INVITE,
                "first_name": tm["name"].split()[0], "last_name": tm["name"].split()[1]
            })
        except RuntimeError:
            pass

    teachers_list = await api_get(client, "/api/v1/students/teachers", tok)
    email_to_tid  = {t["email"]: t["id"] for t in teachers_list}
    teacher_ids   = []
    for tm in teachers_meta:
        tid = email_to_tid.get(tm["email"])
        if not tid:
            conn_tmp = await asyncpg.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=CONTROL_DB)
            try:
                await conn_tmp.execute(f'SET search_path TO "{tenant_id}", public;')
                tid = await conn_tmp.fetchval("SELECT id FROM users WHERE email = $1", tm["email"])
                if tid:
                    await conn_tmp.execute("INSERT INTO teachers (id, name) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name", tid, tm["name"])
            finally:
                await conn_tmp.close()
        if not tid:
            tid = 1
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
            email = f"{fname.lower()}@{domain}"
            try:
                r = await api_post(client, "/api/v1/students", {
                    "email": email, "password": PASSWORD,
                    "name": fname, "class_id": cid,
                    "gender": "male" if si % 2 == 0 else "female",
                    "birth_data": f"200{(si%9)+1}-0{(si%9)+1}-{(si%28)+1:02d}",
                }, tok)
                sid = r["id"]
            except RuntimeError:
                conn_tmp = await asyncpg.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=CONTROL_DB)
                try:
                    await conn_tmp.execute(f'SET search_path TO "{tenant_id}", public;')
                    sid = await conn_tmp.fetchval("SELECT id FROM users WHERE email = $1", email)
                    if sid:
                        await conn_tmp.execute("""
                            INSERT INTO students (id, name, class_id, gender)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (id) DO UPDATE SET class_id = EXCLUDED.class_id
                        """, sid, fname, cid, "male" if si % 2 == 0 else "female")
                finally:
                    await conn_tmp.close()

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
        {"email": f"parent.alrashid@{domain}", "name": f"Parent Al-Rashid ({tenant_id})"},
        {"email": f"parent.bennour@{domain}",  "name": f"Parent Ben-Nour ({tenant_id})"},
        {"email": f"parent.chami@{domain}",    "name": f"Parent Chami ({tenant_id})"},
        {"email": f"parent.darwish@{domain}",  "name": f"Parent Darwish ({tenant_id})"},
        {"email": f"parent.elsayed@{domain}",  "name": f"Parent El-Sayed ({tenant_id})"},
        {"email": f"parent.farouk@{domain}",   "name": f"Parent Farouk ({tenant_id})"},
    ]
    for pidx, pm in enumerate(parents_meta):
        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": pm["email"], "password": PASSWORD,
                "tenant_id": tenant_id, "role": "parent",
                "invite_code": TEACHER_INVITE,
                "first_name": pm["name"].split()[0], "last_name": pm["name"].split()[1]
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
        {
            "title": f"Annual Tech & Science Excursion ({tenant_id})",
            "desc": "All-school educational field trip to the National Science & Tech Expo.",
            "address": "Grand Exhibition Center, Gate 4",
            "subsidy": 20.0,
            "ticket": 15.0,
            "days": 14,
            "status": "published",
            "class_indices": [0, 1, 2, 3, 4, 5],
            "budget_desc": "Charter Bus & Entry Passes",
            "budget_price": 500.0,
        },
        {
            "title": f"Science & Innovation Fair ({tenant_id})",
            "desc": "Annual interactive science project fair for Grade 7 classes.",
            "address": "School Main Auditorium & Gymnasium",
            "subsidy": 15.0,
            "ticket": 10.0,
            "days": 10,
            "status": "published",
            "class_indices": [0, 1],
            "budget_desc": "Lab materials & Display boards",
            "budget_price": 250.0,
        },
        {
            "title": f"History & Heritage Museum Tour ({tenant_id})",
            "desc": "Guided historical trip and workshop for Grade 8 students.",
            "address": "National Heritage Museum",
            "subsidy": 25.0,
            "ticket": 20.0,
            "days": 20,
            "status": "published",
            "class_indices": [2, 3],
            "budget_desc": "Museum guide fee & Refreshments",
            "budget_price": 350.0,
        },
        {
            "title": f"High School & Career Orientation Expo ({tenant_id})",
            "desc": "Career guidance, college prep, and graduation orientation for Grade 9.",
            "address": "Conference Center, East Wing",
            "subsidy": 30.0,
            "ticket": 25.0,
            "days": 30,
            "status": "published",
            "class_indices": [4, 5],
            "budget_desc": "Guest speakers & Workbooks",
            "budget_price": 600.0,
        },
        {
            "title": f"Spring Robotics & AI Workshop ({tenant_id})",
            "desc": "Proposed hands-on robotics building workshop for STEM students.",
            "address": "Innovation Lab 202",
            "subsidy": 10.0,
            "ticket": 5.0,
            "days": 40,
            "status": "proposed",
            "class_indices": [0, 2],
            "budget_desc": "Robotics kits & Microcontrollers",
            "budget_price": 450.0,
        },
        {
            "title": f"Outdoor Leadership & Camping Trip ({tenant_id})",
            "desc": "Approved leadership outdoor excursion waiting for final teacher launch.",
            "address": "Pine Valley Outdoor Camp",
            "subsidy": 35.0,
            "ticket": 40.0,
            "days": 50,
            "status": "approved",
            "class_indices": [2, 4],
            "budget_desc": "Camp ground rental & Instructors",
            "budget_price": 800.0,
        },
        {
            "title": f"Arts & Drama Spring Showcase (Draft) ({tenant_id})",
            "desc": "Teacher draft plan for the spring theater performance.",
            "address": "School Drama Stage",
            "subsidy": 10.0,
            "ticket": 8.0,
            "days": 60,
            "status": "draft",
            "class_indices": [1, 3],
            "budget_desc": "Costumes & Props",
            "budget_price": 200.0,
        },
    ]

    for ev in events_def:
        dt = (datetime.utcnow() + timedelta(days=ev["days"])).strftime("%Y-%m-%dT%H:%M:%S")
        c_mappings = [
            {
                "class_id": class_ids[cidx],
                "ticket_price": ev["ticket"],
                "budgets": [{"description": ev["budget_desc"], "price": ev["budget_price"]}],
            }
            for cidx in ev["class_indices"]
        ]
        r = await api_post(client, "/api/v1/events", {
            "title": ev["title"],
            "description": ev["desc"],
            "address": ev["address"],
            "school_subsidy": ev["subsidy"],
            "date": dt,
            "class_mappings": c_mappings,
        }, teacher_tok)
        eid = r["id"]

        # Update event status directly in SQL for precise test state setup
        for _attempt in range(5):
            try:
                conn_ev = await asyncpg.connect(
                    host=DB_HOST, port=DB_PORT, user=DB_USER,
                    password=DB_PASSWORD, database=CONTROL_DB,
                )
                try:
                    await conn_ev.execute(f'SET search_path TO "{tenant_id}", public;')
                    st = ev["status"]
                    if st == "published":
                        await conn_ev.execute(
                            "UPDATE event SET status = 'published', published_at = CURRENT_TIMESTAMP, submitted_at = CURRENT_TIMESTAMP, manager_approved_at = CURRENT_TIMESTAMP WHERE id = $1",
                            eid,
                        )
                    elif st == "proposed":
                        await conn_ev.execute(
                            "UPDATE event SET status = 'proposed', submitted_at = CURRENT_TIMESTAMP WHERE id = $1",
                            eid,
                        )
                    elif st == "approved":
                        await conn_ev.execute(
                            "UPDATE event SET status = 'approved', submitted_at = CURRENT_TIMESTAMP, manager_approved_at = CURRENT_TIMESTAMP WHERE id = $1",
                            eid,
                        )
                    break
                finally:
                    await conn_ev.close()
            except Exception:
                await asyncio.sleep(0.5)

        target_classes = ", ".join([report["classes"][cidx]["name"] for cidx in ev["class_indices"]])
        report["events"].append({
            "id": eid, "title": ev["title"], "status": ev["status"],
            "target_classes": target_classes,
            "ticket_price": ev["ticket"], "school_subsidy": ev["subsidy"], "date": dt,
        })
        print(f"    '{ev['title']}' [{ev['status'].upper()}] -> classes: [{target_classes}], id={eid}")

    # Seed sample student enrollments for published events
    print("\n  > Seeding Sample Enrollments...")
    for _attempt in range(5):
        try:
            conn_en = await asyncpg.connect(
                host=DB_HOST, port=DB_PORT, user=DB_USER,
                password=DB_PASSWORD, database=CONTROL_DB,
            )
            break
        except Exception:
            await asyncio.sleep(0.5)
    try:
        await conn_en.execute(f'SET search_path TO "{tenant_id}", public;')
        ecm_rows = await conn_en.fetch("""
            SELECT ecm.id, ecm.event_id, ecm.class_id, ecm.ticket_price
            FROM event_class_map ecm
            JOIN event e ON e.id = ecm.event_id
            WHERE e.status = 'published'
        """)
        if ecm_rows and len(student_ids) >= 4:
            en1_id = await conn_en.fetchval("""
                INSERT INTO enrollment (student_id, event_class_map_id, state)
                VALUES ($1, $2, 'requested_by_student')
                ON CONFLICT (student_id, event_class_map_id) DO NOTHING
                RETURNING id
            """, student_ids[0], ecm_rows[0]["id"])

            en2_id = await conn_en.fetchval("""
                INSERT INTO enrollment (student_id, event_class_map_id, state)
                VALUES ($1, $2, 'approved_by_parent')
                ON CONFLICT (student_id, event_class_map_id) DO NOTHING
                RETURNING id
            """, student_ids[1], ecm_rows[0]["id"])
            if en2_id:
                await conn_en.execute("""
                    INSERT INTO payments (enrollment_id, amount, status)
                    VALUES ($1, $2, 'paid')
                    ON CONFLICT DO NOTHING
                """, en2_id, ecm_rows[0]["ticket_price"])

            if len(ecm_rows) > 1:
                en3_id = await conn_en.fetchval("""
                    INSERT INTO enrollment (student_id, event_class_map_id, state)
                    VALUES ($1, $2, 'approved_by_teacher')
                    ON CONFLICT (student_id, event_class_map_id) DO NOTHING
                    RETURNING id
                """, student_ids[3], ecm_rows[1]["id"])
                if en3_id:
                    await conn_en.execute("""
                        INSERT INTO payments (enrollment_id, amount, status)
                        VALUES ($1, $2, 'paid')
                        ON CONFLICT DO NOTHING
                    """, en3_id, ecm_rows[1]["ticket_price"])
            print("    Sample enrollments & payment records created [OK]")
    finally:
        await conn_en.close()

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
    await clear_keycloak_users()
    await seed_super_admin()
    
    async with httpx.AsyncClient() as client:
        for tenant in TENANTS:
            await clear_tenant_db(tenant)
            await seed_tenant(client, tenant)
            await set_parent_phones(tenant)

    print("\nAll tenants (tenant_a & tenant_b) seeded successfully & synced with Keycloak!")


if __name__ == "__main__":
    asyncio.run(main())
