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

import os
import asyncio
import httpx
import asyncpg
from datetime import datetime, timedelta

# ─── Config ──────────────────────────────────────────────────────────────────
BASE_URL       = os.getenv("BASE_URL", "http://127.0.0.1:8001")
TENANT_ID      = os.getenv("TENANT_ID", "tenant_a")
PASSWORD       = os.getenv("PASSWORD", "123321")
TEACHER_INVITE = os.getenv("TEACHER_INVITE", "SCHOOL-STAFF-2026")

DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT     = int(os.getenv("DB_PORT", "5433"))
DB_USER     = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "secure_local_password")
TENANT_DB   = os.getenv("TENANT_DB", "tenant_a_db")
CONTROL_DB  = os.getenv("CONTROL_DB", "control_plane_db")

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
        password=DB_PASSWORD, database=TENANT_DB,
    )
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

        # ── Events ─────────────────────────────────────────────────────────
        print("\n  > Events (one per class)")
        
        # Login teacher to get token
        t_login = await api_post(client, "/api/v1/auth/login", {
            "email": "ali.hassan@school.com", "password": PASSWORD, "tenant_id": TENANT_ID
        })
        teacher_tok = t_login["access_token"]

        events_def = [
            {"title": "Science Fair 2026",      "class_idx": 0, "subsidy": 20.0, "ticket": 15.0, "days": 30,
             "desc": "Annual science fair for Grade 7 class 7A.", "address": "Main Hall, 1st Floor",
             "budget_desc": "Lab materials", "budget_price": 200.0},
            {"title": "Math Olympiad",           "class_idx": 1, "subsidy": 10.0, "ticket":  5.0, "days": 45,
             "desc": "Math competition for class 7B students.",   "address": "Room 101",
             "budget_desc": "Stationery & prizes", "budget_price": 150.0},
            {"title": "History Trip - Museum",   "class_idx": 2, "subsidy": 30.0, "ticket": 25.0, "days": 20,
             "desc": "Educational visit to National Museum for 8A.", "address": "National Museum, City Centre",
             "budget_desc": "Transport & entry fees", "budget_price": 400.0},
            {"title": "Art Exhibition",          "class_idx": 3, "subsidy": 15.0, "ticket": 10.0, "days": 60,
             "desc": "Student artworks showcase for class 8B.", "address": "Gallery Room, 2nd Floor",
             "budget_desc": "Art supplies & frames", "budget_price": 300.0},
            {"title": "Sports Day",              "class_idx": 4, "subsidy": 50.0, "ticket":  0.0, "days": 15,
             "desc": "Annual sports day competition for 9A.", "address": "School Football Field",
             "budget_desc": "Equipment & refreshments", "budget_price": 600.0},
            {"title": "Tech Workshop",           "class_idx": 5, "subsidy": 40.0, "ticket": 20.0, "days": 25,
             "desc": "Coding & robotics workshop for 9B.", "address": "Computer Lab",
             "budget_desc": "Hardware components", "budget_price": 500.0},
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

        # Get system resource types
        rt_list = await api_get(client, "/api/v1/events/resource-types", tok)
        name_to_rtid = {rt["name"]: rt["id"] for rt in rt_list}

        print("\n  > Transitioning events and seeding resource costs ...")
        # Find created event IDs in the report
        event_objs = report["events"]
        
        # Helper to find eid by title prefix
        def get_eid(prefix):
            for e in event_objs:
                if e["title"].startswith(prefix):
                    return e["id"]
            return None

        # 1. Math Olympiad -> proposed
        math_id = get_eid("Math Olympiad")
        if math_id:
            # Add resources
            await api_post(client, f"/api/v1/events/{math_id}/resources", [
                {"resource_type_id": name_to_rtid["Male Supervisor"], "description": "Supervisor check", "quantity": 1},
                {"resource_type_id": name_to_rtid["Kids Meal"], "description": "Student snacks", "quantity": 20}
            ], teacher_tok)
            # Submit
            await api_post(client, f"/api/v1/events/{math_id}/submit", {}, teacher_tok)
            print(f"    Event 'Math Olympiad' (id={math_id}) transitioned to PROPOSED")

        # 2. History Trip - Museum -> finance_approval
        hist_id = get_eid("History Trip")
        if hist_id:
            # Add resources
            await api_post(client, f"/api/v1/events/{hist_id}/resources", [
                {"resource_type_id": name_to_rtid["40-Seat Bus"], "description": "Main transport", "quantity": 1},
                {"resource_type_id": name_to_rtid["Male Supervisor"], "description": "Lead supervisor", "quantity": 1},
                {"resource_type_id": name_to_rtid["Female Supervisor"], "description": "Assistant supervisor", "quantity": 1},
                {"resource_type_id": name_to_rtid["Kids Meal"], "description": "Lunch boxes", "quantity": 30}
            ], teacher_tok)
            # Submit
            await api_post(client, f"/api/v1/events/{hist_id}/submit", {}, teacher_tok)
            # Manager approves
            await api_post(client, f"/api/v1/events/{hist_id}/manager-decision", {"decision": "approve"}, mgr_tok)
            print(f"    Event 'History Trip - Museum' (id={hist_id}) transitioned to FINANCE_APPROVAL")

        # 3. Art Exhibition -> final_review
        art_id = get_eid("Art Exhibition")
        if art_id:
            # Add resources
            await api_post(client, f"/api/v1/events/{art_id}/resources", [
                {"resource_type_id": name_to_rtid["Female Supervisor"], "description": "Gallery guard", "quantity": 2},
                {"resource_type_id": name_to_rtid["Adult Meal"], "description": "Refreshments", "quantity": 40}
            ], teacher_tok)
            # Submit
            await api_post(client, f"/api/v1/events/{art_id}/submit", {}, teacher_tok)
            # Manager approves
            await api_post(client, f"/api/v1/events/{art_id}/manager-decision", {"decision": "approve"}, mgr_tok)
            # Price resources
            summary = await api_get(client, f"/api/v1/events/{art_id}/resources", fin_tok)
            for res in summary["resources"]:
                price = 10.0 if "Meal" in res["resource_type_name"] else 40.0
                await client.put(f"{BASE_URL}/api/v1/events/resources/{res['id']}/cost", json={
                    "unit_price": price, "currency": "JOD"
                }, headers={**HEADERS, "Authorization": f"Bearer {fin_tok}"})
            # Submit pricing
            await api_post(client, f"/api/v1/events/{art_id}/finance-submit", {}, fin_tok)
            print(f"    Event 'Art Exhibition' (id={art_id}) transitioned to FINAL_REVIEW")

        # 4. Sports Day -> published
        sports_id = get_eid("Sports Day")
        if sports_id:
            # Add resources
            await api_post(client, f"/api/v1/events/{sports_id}/resources", [
                {"resource_type_id": name_to_rtid["20-Seat Bus"], "description": "Team transport", "quantity": 2},
                {"resource_type_id": name_to_rtid["Male Supervisor"], "description": "Field coach", "quantity": 2},
                {"resource_type_id": name_to_rtid["Kids Meal"], "description": "Snack packs", "quantity": 50}
            ], teacher_tok)
            # Submit
            await api_post(client, f"/api/v1/events/{sports_id}/submit", {}, teacher_tok)
            # Manager approves
            await api_post(client, f"/api/v1/events/{sports_id}/manager-decision", {"decision": "approve"}, mgr_tok)
            # Price resources
            summary = await api_get(client, f"/api/v1/events/{sports_id}/resources", fin_tok)
            for res in summary["resources"]:
                price = 3.0 if "Meal" in res["resource_type_name"] else (30.0 if "Supervisor" in res["resource_type_name"] else 80.0)
                await client.put(f"{BASE_URL}/api/v1/events/resources/{res['id']}/cost", json={
                    "unit_price": price, "currency": "JOD"
                }, headers={**HEADERS, "Authorization": f"Bearer {fin_tok}"})
            # Submit pricing
            await api_post(client, f"/api/v1/events/{sports_id}/finance-submit", {}, fin_tok)
            # Manager publishes
            await api_post(client, f"/api/v1/events/{sports_id}/final-decision", {"decision": "publish"}, mgr_tok)
            print(f"    Event 'Sports Day' (id={sports_id}) transitioned to PUBLISHED")

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
        password=DB_PASSWORD, database=TENANT_DB,
    )
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
