"""
seed_data.py — SchoolDesk Demo Data Seeder
==========================================
Calls the running API (localhost:8001) to delete all existing data and
re-populate the tenant_a database with:

  • 3 Levels    (Grade 7, Grade 8, Grade 9)
  • 6 Teachers  (2 per level, each heads one class)
  • 6 Classes   (7A, 7B, 8A, 8B, 9A, 9B)
  • 18 Students (3 per class)
  • 6 Parents   (each linked to 3 students)
  • 6 Events    (one per class, with a budget item each)

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
BASE_URL       = os.getenv("BASE_URL", "http://127.0.0.1:9080")
TENANT_ID      = os.getenv("TENANT_ID", "tenant_a")
PASSWORD       = os.getenv("PASSWORD", "123321")
TEACHER_INVITE = os.getenv("TEACHER_INVITE", "SCHOOL-STAFF-2026")

DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT     = int(os.getenv("DB_PORT", "5433"))
DB_USER     = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "secure_local_password")
TENANT_DB   = os.getenv("TENANT_DB", "tenant_a_db")
CONTROL_DB  = os.getenv("CONTROL_DB", "user_service_db")

HEADERS = {"Content-Type": "application/json"}

# ─── HTTP helpers ─────────────────────────────────────────────────────────────

async def api_post(client, path, body, token=None):
    hdrs = {**HEADERS}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    r = await client.post(f"{BASE_URL}{path}", json=body, headers=hdrs, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"POST {path} => {r.status_code}: {r.text}")
    return r.json()


async def api_get(client, path, token):
    hdrs = {**HEADERS, "Authorization": f"Bearer {token}"}
    r = await client.get(f"{BASE_URL}{path}", headers=hdrs, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"GET {path} => {r.status_code}: {r.text}")
    return r.json()


# ─── DB wipe ─────────────────────────────────────────────────────────────────

async def clear_tenant_db():
    print("\n[1/3] Clearing tenant DB ...")
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    await conn.execute(f'SET search_path TO "{TENANT_ID}", public;')
    try:
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
                    levels,
                    users
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
        print("    Tenant DB cleared and system resource types re-seeded.")
    finally:
        await conn.close()


async def clear_control_plane_db():
    print("[2/3] Clearing control-plane parent rows ...")
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    try:
        await conn.execute("TRUNCATE TABLE parent_child_links, parent_tenant_links, parents CASCADE;")
        print("    Control-plane rows cleared.")
    finally:
        await conn.close()


# ─── Seeder ───────────────────────────────────────────────────────────────────

async def seed():
    print("[3/3] Seeding data via API ...\n")
    report = {"admin": {}, "levels": [], "classes": [], "teachers": [],
              "students": [], "parents": [], "events": []}

    async with httpx.AsyncClient() as client:

        # ── Admin ──────────────────────────────────────────────────────────
        print("  > Admin")
        try:
            r = await api_post(client, "/api/v1/auth/register", {
                "email": "admin@school.com", "password": PASSWORD,
                "tenant_id": TENANT_ID, "role": "school_admin",
            })
        except RuntimeError:
            r = await api_post(client, "/api/v1/auth/login", {
                "email": "admin@school.com", "password": PASSWORD, "tenant_id": TENANT_ID,
            })
        tok = r["access_token"]
        report["admin"] = {"email": "admin@school.com", "password": PASSWORD, "role": "school_admin"}
        print("    admin@school.com  [OK]")

        # Easy test admin account
        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": "admin@test.com", "password": "123321",
                "tenant_id": TENANT_ID, "role": "school_admin",
            })
        except RuntimeError:
            pass # already exists
        print("    admin@test.com    [OK] (pass: 123321)")

        # Seeding manager and finance users
        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": "manager@school.com", "password": PASSWORD,
                "tenant_id": TENANT_ID, "role": "manager",
                "invite_code": TEACHER_INVITE,
            })
        except RuntimeError:
            pass
        print("    manager@school.com [OK] (pass: 123321)")

        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": "manager@test.com", "password": "123321",
                "tenant_id": TENANT_ID, "role": "manager",
                "invite_code": TEACHER_INVITE,
            })
        except RuntimeError:
            pass
        print("    manager@test.com   [OK] (pass: 123321)")

        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": "finance@school.com", "password": PASSWORD,
                "tenant_id": TENANT_ID, "role": "finance",
                "invite_code": TEACHER_INVITE,
            })
        except RuntimeError:
            pass
        print("    finance@school.com [OK] (pass: 123321)")

        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": "finance@test.com", "password": "123321",
                "tenant_id": TENANT_ID, "role": "finance",
                "invite_code": TEACHER_INVITE,
            })
        except RuntimeError:
            pass
        print("    finance@test.com   [OK] (pass: 123321)")

        # Seeding event_teacher
        await api_post(client, "/api/v1/auth/register", {
            "email": "event_teacher@school.com", "password": PASSWORD,
            "tenant_id": TENANT_ID, "role": "event_teacher",
            "invite_code": TEACHER_INVITE,
        })
        print("    event_teacher@school.com [OK] (pass: 123321)")

        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": "event_teacher@test.com", "password": "123321",
                "tenant_id": TENANT_ID, "role": "event_teacher",
                "invite_code": TEACHER_INVITE,
            })
        except RuntimeError:
            pass
        print("    event_teacher@test.com   [OK] (pass: 123321)")

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
            {"email": "ali.hassan@school.com",     "name": "Ali Hassan"},
            {"email": "sara.karim@school.com",     "name": "Sara Karim"},
            {"email": "omar.nasser@school.com",    "name": "Omar Nasser"},
            {"email": "lina.farouk@school.com",    "name": "Lina Farouk"},
            {"email": "hassan.mahmoud@school.com", "name": "Hassan Mahmoud"},
            {"email": "dina.rabie@school.com",     "name": "Dina Rabie"},
        ]
        for tm in teachers_meta:
            try:
                await api_post(client, "/api/v1/auth/register", {
                    "email": tm["email"], "password": PASSWORD,
                    "tenant_id": TENANT_ID, "role": "teacher",
                    "invite_code": TEACHER_INVITE,
                })
            except RuntimeError:
                pass  # already exists

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
                email = f"{fname.lower()}.s{si+1}@student.com"
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
            {"email": "parent.al1@parent.com", "name": "Parent Al-Rashid"},
            {"email": "parent.bn2@parent.com", "name": "Parent Ben-Nour"},
            {"email": "parent.cm3@parent.com", "name": "Parent Chami"},
            {"email": "parent.da4@parent.com", "name": "Parent Darwish"},
            {"email": "parent.el5@parent.com", "name": "Parent El-Sayed"},
            {"email": "parent.fa6@parent.com", "name": "Parent Farouk"},
        ]
        for pidx, pm in enumerate(parents_meta):
            try:
                await api_post(client, "/api/v1/auth/register", {
                    "email": pm["email"], "password": PASSWORD,
                    "tenant_id": TENANT_ID, "role": "parent",
                })
            except RuntimeError as e:
                print(f"Parent registration error: {e}")
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

        # ── Multi-Class Parent ───────────────────────────────────────────────
        try:
            await api_post(client, "/api/v1/auth/register", {
                "email": "parent_two_kids@school.com", "password": PASSWORD,
                "tenant_id": TENANT_ID, "role": "parent",
            })
        except RuntimeError:
            pass

        parents_list = await api_get(client, "/api/v1/students/parents", tok)
        email_to_pid = {p["email"]: p["id"] for p in parents_list}
        multi_pid = email_to_pid.get("parent_two_kids@school.com")
        if multi_pid:
            await api_post(client, "/api/v1/students/link-parent", {"student_id": student_ids[0], "parent_id": multi_pid}, tok)  # Ahmed (Class 7A)
            await api_post(client, "/api/v1/students/link-parent", {"student_id": student_ids[3], "parent_id": multi_pid}, tok)  # Fatima (Class 7B)
            report["parents"].append({
                "id": multi_pid, "name": "Parent Multi-Class", "email": "parent_two_kids@school.com",
                "password": PASSWORD, "role": "parent",
                "linked_students": ["Ahmed (7A)", "Fatima (7B)"],
            })
            print(f"    Parent Multi-Class (parent_two_kids@school.com) -> id={multi_pid}, children: Ahmed (7A), Fatima (7B)")

        # ── Events ─────────────────────────────────────────────────────────
        print("\n  > Events (one per class)")
        
        # Login teacher to get token
        t_login = await api_post(client, "/api/v1/auth/login", {
            "email": "ali.hassan@school.com", "password": PASSWORD, "tenant_id": TENANT_ID
        })
        teacher_tok = t_login["access_token"]

        events_def = [
            {"title": "Science Fair 2026",            "class_idx": 0, "subsidy": 20.0, "ticket": 15.0, "days": 10,
             "desc": "Annual science fair for Grade 7 class 7A.", "address": "Main Hall, 1st Floor",
             "budget_desc": "Lab materials", "budget_price": 200.0},
            {"title": "Math Olympiad",                 "class_idx": 1, "subsidy": 10.0, "ticket":  5.0, "days": 45,
             "desc": "Math competition for class 7B students.",   "address": "Room 101",
             "budget_desc": "Stationery & prizes", "budget_price": 150.0},
            {"title": "History Trip - Museum",         "class_idx": 2, "subsidy": 30.0, "ticket": 25.0, "days": 15,
             "desc": "Educational visit to National Museum for 8A.", "address": "National Museum, City Centre",
             "budget_desc": "Transport & entry fees", "budget_price": 400.0},
            {"title": "Art Exhibition",                "class_idx": 3, "subsidy": 15.0, "ticket": 10.0, "days": 60,
             "desc": "Student artworks showcase for class 8B.", "address": "Gallery Room, 2nd Floor",
             "budget_desc": "Art supplies & frames", "budget_price": 300.0},
            {"title": "Sports Day",                    "class_idx": 4, "subsidy": 50.0, "ticket":  0.0, "days": 5,
             "desc": "Annual sports day competition for 9A.", "address": "School Football Field",
             "budget_desc": "Equipment & refreshments", "budget_price": 600.0},
            {"title": "Coding & Robotics Hackathon",   "class_idx": 5, "subsidy": 40.0, "ticket": 20.0, "days": 25,
             "desc": "Coding & robotics workshop for 9B.", "address": "Computer Lab 3",
             "budget_desc": "Hardware components", "budget_price": 500.0},
            {"title": "Robotics Competition",          "class_idx": 0, "subsidy": 25.0, "ticket": 12.0, "days": 30,
             "desc": "Inter-school robotics tournament for Grade 7.", "address": "Innovation Hub",
             "budget_desc": "Robot kits", "budget_price": 350.0},
            {"title": "Music & Drama Gala",            "class_idx": 1, "subsidy": 35.0, "ticket": 15.0, "days": 20,
             "desc": "Stage performance and musical concert.", "address": "School Auditorium",
             "budget_desc": "Audio equipment & costumes", "budget_price": 450.0},
            {"title": "Chemistry Lab Tour",            "class_idx": 2, "subsidy": 20.0, "ticket": 18.0, "days": 40,
             "desc": "University chemistry department visit.", "address": "State University Lab",
             "budget_desc": "Lab safety gear & bus", "budget_price": 380.0},
            {"title": "Environmental Clean-Up Trip",   "class_idx": 3, "subsidy": 60.0, "ticket":  0.0, "days": 8,
             "desc": "Community eco-service field activity.", "address": "National Park Bay",
             "budget_desc": "Buses & safety supplies", "budget_price": 250.0},
            {"title": "Astronomy Stargazing Night",    "class_idx": 4, "subsidy": 45.0, "ticket": 10.0, "days": 12,
             "desc": "Night observatory telescope experience.", "address": "Desert Observatory Outpost",
             "budget_desc": "Telescopes & night meal", "budget_price": 520.0},
            {"title": "Literary Book Club Fair",       "class_idx": 5, "subsidy": 15.0, "ticket":  5.0, "days": 18,
             "desc": "Book reading and author meet & greet.", "address": "Central Library",
             "budget_desc": "Books & bookmarks", "budget_price": 180.0},
            {"title": "Annual Debate Championship",    "class_idx": 0, "subsidy": 30.0, "ticket":  8.0, "days": 35,
             "desc": "Public speaking and debate contest.", "address": "Conference Room B",
             "budget_desc": "Trophies & certificates", "budget_price": 220.0},
            {"title": "Geography Field Trip",          "class_idx": 1, "subsidy": 50.0, "ticket": 22.0, "days": 28,
             "desc": "Geological landscape survey trip.", "address": "Red Canyon Valley",
             "budget_desc": "Buses & guide fees", "budget_price": 480.0},
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

        # Get manager and finance tokens
        mgr_r = await api_post(client, "/api/v1/auth/login", {
            "email": "manager@school.com", "password": PASSWORD, "tenant_id": TENANT_ID
        })
        mgr_tok = mgr_r["access_token"]

        fin_r = await api_post(client, "/api/v1/auth/login", {
            "email": "finance@school.com", "password": PASSWORD, "tenant_id": TENANT_ID
        })
        fin_tok = fin_r["access_token"]

        et_r = await api_post(client, "/api/v1/auth/login", {
            "email": "event_teacher@school.com", "password": PASSWORD, "tenant_id": TENANT_ID
        })
        et_tok = et_r["access_token"]

        # Get system resource types
        rt_list = await api_get(client, "/api/v1/events/resource-types", tok)
        name_to_rtid = {rt["name"]: rt["id"] for rt in rt_list}

        print("\n  > Transitioning events to practice workflows ...")
        
        # Helper to find eid by title prefix
        def get_eid(prefix):
            for e in report["events"]:
                if e["title"].startswith(prefix):
                    return e["id"]
            return None

        # 1. Resource Planning stage (for Event Teacher practice)
        for prefix in ["Coding & Robotics", "Annual Debate"]:
            eid = get_eid(prefix)
            if eid:
                await api_post(client, f"/api/v1/events/{eid}/submit", {}, teacher_tok)
                print(f"    Event '{prefix}' (id={eid}) -> RESOURCE_PLANNING (Event Teacher queue)")

        # 2. Proposed stage (for Manager practice)
        for prefix in ["Math Olympiad", "Music & Drama"]:
            eid = get_eid(prefix)
            if eid:
                await api_post(client, f"/api/v1/events/{eid}/submit", {}, teacher_tok)
                await api_post(client, f"/api/v1/events/{eid}/resources", [
                    {"resource_type_id": name_to_rtid["Male Supervisor"], "description": "Staff supervisor", "quantity": 1},
                    {"resource_type_id": name_to_rtid["Kids Meal"], "description": "Snacks", "quantity": 25}
                ], et_tok)
                await api_post(client, f"/api/v1/events/{eid}/submit", {}, et_tok)
                print(f"    Event '{prefix}' (id={eid}) -> PROPOSED (Manager approval queue)")

        # 3. Finance Approval stage (for Finance pricing practice)
        for prefix in ["History Trip", "Chemistry Lab"]:
            eid = get_eid(prefix)
            if eid:
                await api_post(client, f"/api/v1/events/{eid}/submit", {}, teacher_tok)
                await api_post(client, f"/api/v1/events/{eid}/resources", [
                    {"resource_type_id": name_to_rtid["40-Seat Bus"], "description": "Bus transport", "quantity": 1},
                    {"resource_type_id": name_to_rtid["Female Supervisor"], "description": "Supervisor", "quantity": 2},
                    {"resource_type_id": name_to_rtid["Kids Meal"], "description": "Meals", "quantity": 30}
                ], et_tok)
                await api_post(client, f"/api/v1/events/{eid}/submit", {}, et_tok)
                await api_post(client, f"/api/v1/events/{eid}/manager-decision", {"decision": "approve"}, mgr_tok)
                print(f"    Event '{prefix}' (id={eid}) -> FINANCE_APPROVAL (Finance pricing queue)")

        # 4. Final Review stage (for Manager final review practice)
        for prefix in ["Art Exhibition", "Geography Field"]:
            eid = get_eid(prefix)
            if eid:
                await api_post(client, f"/api/v1/events/{eid}/submit", {}, teacher_tok)
                await api_post(client, f"/api/v1/events/{eid}/resources", [
                    {"resource_type_id": name_to_rtid["Female Supervisor"], "description": "Guide", "quantity": 2},
                    {"resource_type_id": name_to_rtid["Adult Meal"], "description": "Food", "quantity": 20}
                ], et_tok)
                await api_post(client, f"/api/v1/events/{eid}/submit", {}, et_tok)
                await api_post(client, f"/api/v1/events/{eid}/manager-decision", {"decision": "approve"}, mgr_tok)
                summary = await api_get(client, f"/api/v1/events/{eid}/resources", fin_tok)
                for res in summary["resources"]:
                    await client.put(f"{BASE_URL}/api/v1/events/resources/{res['id']}/cost", json={
                        "unit_price": 25.0, "currency": "JOD"
                    }, headers={**HEADERS, "Authorization": f"Bearer {fin_tok}"})
                await api_post(client, f"/api/v1/events/{eid}/finance-submit", {}, fin_tok)
                print(f"    Event '{prefix}' (id={eid}) -> FINAL_REVIEW (Manager final review queue)")

        # 5. Published stage (for Student & Parent enrollment practice across Grade 7, 8, and 9)
        for prefix in ["Science Fair 2026", "Environmental Clean-Up", "Sports Day"]:
            eid = get_eid(prefix)
            if eid:
                await api_post(client, f"/api/v1/events/{eid}/submit", {}, teacher_tok)
                await api_post(client, f"/api/v1/events/{eid}/resources", [
                    {"resource_type_id": name_to_rtid["20-Seat Bus"], "description": "Bus", "quantity": 2},
                    {"resource_type_id": name_to_rtid["Male Supervisor"], "description": "Coach", "quantity": 2},
                    {"resource_type_id": name_to_rtid["Kids Meal"], "description": "Snacks", "quantity": 40}
                ], et_tok)
                await api_post(client, f"/api/v1/events/{eid}/submit", {}, et_tok)
                await api_post(client, f"/api/v1/events/{eid}/manager-decision", {"decision": "approve"}, mgr_tok)
                summary = await api_get(client, f"/api/v1/events/{eid}/resources", fin_tok)
                for res in summary["resources"]:
                    await client.put(f"{BASE_URL}/api/v1/events/resources/{res['id']}/cost", json={
                        "unit_price": 15.0, "currency": "JOD"
                    }, headers={**HEADERS, "Authorization": f"Bearer {fin_tok}"})
                await api_post(client, f"/api/v1/events/{eid}/finance-submit", {}, fin_tok)
                await api_post(client, f"/api/v1/events/{eid}/final-decision", {"decision": "publish"}, mgr_tok)
                print(f"    Event '{prefix}' (id={eid}) -> PUBLISHED (Enrollments open for all grades)")

    return report


# ─── Summary writer ───────────────────────────────────────────────────────────

def write_summary(d):
    SEP = "=" * 72
    lines = [
        SEP,
        "  SchoolDesk - Seeded Demo Data",
        "  All passwords : 123321",
        "  Tenant        : tenant_a",
        SEP,
        "",
        "== ADMIN ==",
        f"  {d['admin']['email']}   pw: {d['admin']['password']}",
        "",
        "== LEVELS ==",
    ]
    for lv in d["levels"]:
        lines.append(f"  [id={lv['id']}]  {lv['name']}")

    lines += ["", "== CLASSES =="]
    for cl in d["classes"]:
        lines.append(f"  [id={cl['id']}]  {cl['name']}  ({cl['level']})  head: {cl['head_teacher']}")

    lines += ["", "== TEACHERS =="]
    for t in d["teachers"]:
        lines.append(f"  [id={t['id']}]  {t['name']}   {t['email']}   pw: {t['password']}")

    lines += ["", "== STUDENTS =="]
    for s in d["students"]:
        lines.append(
            f"  [id={s['id']}]  {s['name']:<12}  {s['email']:<30}  "
            f"pw: {s['password']}   class: {s['class']} ({s['level']})"
        )

    lines += ["", "== PARENTS =="]
    for p in d["parents"]:
        children = ", ".join(p["linked_students"])
        lines.append(
            f"  [id={p['id']}]  {p['name']:<20}  {p['email']:<28}  "
            f"pw: {p['password']}   children: {children}"
        )

    lines += ["", "== EVENTS =="]
    for ev in d["events"]:
        lines.append(
            f"  [id={ev['id']}]  {ev['title']:<28}  class: {ev['target_class']} ({ev['target_level']})"
            f"  ticket: ${ev['ticket_price']}  subsidy: ${ev['school_subsidy']}  date: {ev['date']}"
        )

    lines += ["", SEP]
    txt = "\n".join(lines)

    with open("seeded_data_summary.txt", "w", encoding="utf-8") as f:
        f.write(txt)

    print("\n" + txt)
    print("\nSummary saved -> seeded_data_summary.txt")


# ─── Entry ────────────────────────────────────────────────────────────────────

async def set_parent_phones():
    print("\nSetting fake phone numbers for parents ...")
    conn_t = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    await conn_t.execute(f'SET search_path TO "{TENANT_ID}", public;')
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


async def main():
    print("=" * 55)
    print("  SchoolDesk Demo Seeder")
    print("=" * 55)
    await clear_tenant_db()
    await clear_control_plane_db()
    data = await seed()
    await set_parent_phones()
    write_summary(data)
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
