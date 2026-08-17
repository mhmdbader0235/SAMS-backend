"""
seed_tenant_b_like_a.py
Clone and mirror the entire rich dataset from tenant_a to tenant_b, adapting emails and names to tenant_b.
"""

import asyncio
import os
import asyncpg
from app.domains.auth.service import AuthService
from app.core.keycloak_admin import sync_user_to_keycloak

DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT     = int(os.getenv("DB_PORT", "5433"))
DB_USER     = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "secure_local_password")
CONTROL_DB  = os.getenv("CONTROL_DB", "user_service_db")
PASSWORD    = "123321"

async def clone_a_to_b():
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=CONTROL_DB,
    )
    try:
        print("1. Ensuring tenant_b schema exists and columns are aligned...")
        await conn.execute('CREATE SCHEMA IF NOT EXISTS "tenant_b";')
        await conn.execute('SET search_path TO "tenant_b", public;')

        # Add missing columns if any
        await conn.execute('ALTER TABLE tenant_b.academic_settings ADD COLUMN IF NOT EXISTS system TEXT DEFAULT \'US\';')

        # Truncate all tables in tenant_b
        tables = [
            "notifications", "student_health_and_records", "payments", "enrollment",
            "resource_cost", "resources", "event_class_map", "event_feedback", "event",
            "student_parent_map", "students", "parenets", "class", "teachers",
            "levels", "blackout_dates", "academic_settings", "resource_types", "users"
        ]
        for t in tables:
            try:
                await conn.execute(f'TRUNCATE TABLE tenant_b."{t}" RESTART IDENTITY CASCADE;')
            except Exception as e:
                pass

        # Also clean control plane mappings for tenant_b
        await conn.execute("DELETE FROM public.user_tenant_map WHERE tenant_id = 'tenant_b';")
        await conn.execute("DELETE FROM public.parent_child_links WHERE tenant_id = 'tenant_b';")
        await conn.execute("DELETE FROM public.parent_tenant_links WHERE tenant_id = 'tenant_b';")

        default_pwd_hash = AuthService.hash_password(PASSWORD)

        print("2. Cloning academic_settings...")
        ac_settings = await conn.fetch("SELECT * FROM tenant_a.academic_settings")
        for s in ac_settings:
            await conn.execute("""
                INSERT INTO tenant_b.academic_settings (academic_year, start_month, weekend_days, system)
                VALUES ($1, $2, $3, $4)
            """, s["academic_year"], s["start_month"], s["weekend_days"], s.get("system", "US"))

        print("3. Cloning blackout_dates...")
        b_dates = await conn.fetch("SELECT * FROM tenant_a.blackout_dates")
        for b in b_dates:
            await conn.execute("""
                INSERT INTO tenant_b.blackout_dates (date, title, tags)
                VALUES ($1, $2, $3)
            """, b["date"], b["title"], b["tags"])

        print("4. Cloning resource_types...")
        r_types = await conn.fetch("SELECT * FROM tenant_a.resource_types ORDER BY id")
        rt_map = {} # old_id -> new_id
        for rt in r_types:
            new_rt_id = await conn.fetchval("""
                INSERT INTO tenant_b.resource_types (name, category, is_custom, created_by_user_id, is_active)
                VALUES ($1, $2, $3, NULL, $4)
                RETURNING id
            """, rt["name"], rt["category"], rt["is_custom"], rt["is_active"])
            rt_map[rt["id"]] = new_rt_id

        print("5. Cloning levels...")
        levels = await conn.fetch("SELECT * FROM tenant_a.levels ORDER BY level_id")
        level_map = {} # old_id -> new_id
        for l in levels:
            new_lid = await conn.fetchval("""
                INSERT INTO tenant_b.levels (name, isced_level, age_band_min, age_band_max, ordinal, is_active)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING level_id
            """, l["name"], l["isced_level"], l["age_band_min"], l["age_band_max"], l["ordinal"], l["is_active"])
            level_map[l["level_id"]] = new_lid

        print("6. Cloning users (Staff, Admins, Teachers, Parents, Students)...")
        users_a = await conn.fetch("SELECT * FROM tenant_a.users ORDER BY id")
        user_map = {} # old_user_id -> new_user_id

        for u in users_a:
            email_a = u["email"]
            # Convert domain to @schoolb.com
            parts = email_a.split("@")
            username = parts[0]
            email_b = f"{username}@schoolb.com"

            new_uid = await conn.fetchval("""
                INSERT INTO tenant_b.users (email, role, roles, permissions, password_hash, phone, address)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (email) DO UPDATE SET role = EXCLUDED.role, roles = EXCLUDED.roles, permissions = EXCLUDED.permissions, phone = EXCLUDED.phone
                RETURNING id
            """, email_b, u["role"], u["roles"], u["permissions"], default_pwd_hash, u["phone"], u["address"])
            user_map[u["id"]] = new_uid

            # Register in control plane user_tenant_map
            await conn.execute("""
                INSERT INTO public.user_tenant_map (email, tenant_id, role)
                VALUES ($1, 'tenant_b', $2)
                ON CONFLICT (email) DO UPDATE SET tenant_id = 'tenant_b', role = EXCLUDED.role
            """, email_b, u["role"])

        print("7. Cloning teachers...")
        teachers_a = await conn.fetch("SELECT * FROM tenant_a.teachers ORDER BY id")
        for t in teachers_a:
            old_id = t["id"]
            if old_id in user_map:
                new_uid = user_map[old_id]
                t_name = t["name"].replace("(tenant_a)", "(tenant_b)").replace("(Tenant A)", "(Tenant B)")
                await conn.execute("""
                    INSERT INTO tenant_b.teachers (id, name)
                    VALUES ($1, $2)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                """, new_uid, t_name)

        print("8. Cloning classes...")
        classes_a = await conn.fetch("SELECT * FROM tenant_a.class ORDER BY id")
        class_map = {} # old_class_id -> new_class_id
        for c in classes_a:
            old_cid = c["id"]
            new_lid = level_map.get(c["level_id"])
            new_head_id = user_map.get(c["head_teacher_id"])
            if new_lid:
                new_cid = await conn.fetchval("""
                    INSERT INTO tenant_b.class (name, level_id, head_teacher_id, capacity)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                """, c["name"], new_lid, new_head_id, c["capacity"])
                class_map[old_cid] = new_cid

        print("9. Cloning parents...")
        parents_a = await conn.fetch("SELECT * FROM tenant_a.parenets ORDER BY id")
        parent_map = {} # old_parent_id -> new_parent_id
        for p in parents_a:
            old_pid = p["id"]
            if old_pid in user_map:
                new_pid = user_map[old_pid]
                p_name = p["name"].replace("(tenant_a)", "(tenant_b)").replace("(Tenant A)", "(Tenant B)")
                await conn.execute("""
                    INSERT INTO tenant_b.parenets (id, name, phone)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, phone = EXCLUDED.phone
                """, new_pid, p_name, p["phone"])
                parent_map[old_pid] = new_pid

                # Also ensure parent exists globally in control plane
                user_row = await conn.fetchrow("SELECT email, phone FROM tenant_b.users WHERE id = $1", new_pid)
                p_email = user_row["email"] if user_row else f"{p_name.lower().replace(' ', '.')}@schoolb.com"
                p_phone = user_row["phone"] if user_row else p["phone"]
                
                cp_parent_id = await conn.fetchval("""
                    INSERT INTO public.parents (email, password_hash, phone)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (email) DO UPDATE SET phone = EXCLUDED.phone
                    RETURNING id
                """, p_email, default_pwd_hash, p_phone)
                
                await conn.execute("""
                    INSERT INTO public.parent_tenant_links (parent_id, tenant_id)
                    VALUES ($1, 'tenant_b')
                    ON CONFLICT DO NOTHING
                """, cp_parent_id)

        print("10. Cloning students...")
        students_a = await conn.fetch("SELECT * FROM tenant_a.students ORDER BY id")
        student_map = {} # old_student_id -> new_student_id
        for s in students_a:
            old_sid = s["id"]
            if old_sid in user_map:
                new_sid = user_map[old_sid]
                new_cid = class_map.get(s["class_id"])
                if new_cid:
                    await conn.execute("""
                        INSERT INTO tenant_b.students (id, name, class_id, gender, birth_data)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (id) DO UPDATE SET class_id = EXCLUDED.class_id
                    """, new_sid, s["name"], new_cid, s["gender"], s["birth_data"])
                    student_map[old_sid] = new_sid

        print("11. Cloning student_parent_map...")
        spm_a = await conn.fetch("SELECT * FROM tenant_a.student_parent_map")
        for sp in spm_a:
            new_sid = student_map.get(sp["student_id"])
            new_pid = parent_map.get(sp["parent_id"])
            if new_sid and new_pid:
                await conn.execute("""
                    INSERT INTO tenant_b.student_parent_map (student_id, parent_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                """, new_sid, new_pid)

        print("12. Cloning events...")
        events_a = await conn.fetch("SELECT * FROM tenant_a.event ORDER BY id")
        event_map = {} # old_eid -> new_eid
        for ev in events_a:
            old_eid = ev["id"]
            new_created_by = user_map.get(ev["created_by"], list(user_map.values())[0])
            new_mgr_reviewer = user_map.get(ev["manager_reviewer_id"])
            new_fin_reviewer = user_map.get(ev["finance_reviewer_id"])
            
            ev_title = ev["title"].replace("(tenant_a)", "(tenant_b)").replace("tenant_a", "tenant_b")
            new_eid = await conn.fetchval("""
                INSERT INTO tenant_b.event (
                    title, description, address, event_map_id, school_subsidy,
                    date, created_by, status, predicted_attendance,
                    manager_reviewer_id, finance_reviewer_id, total_cost,
                    submitted_at, manager_approved_at, finance_priced_at,
                    published_at, rejection_reason
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                RETURNING id
            """,
                ev_title, ev["description"], ev["address"], ev["event_map_id"], ev["school_subsidy"],
                ev["date"], new_created_by, ev["status"], ev["predicted_attendance"],
                new_mgr_reviewer, new_fin_reviewer, ev["total_cost"],
                ev["submitted_at"], ev["manager_approved_at"], ev["finance_priced_at"],
                ev["published_at"], ev["rejection_reason"]
            )
            event_map[old_eid] = new_eid

        print("13. Cloning event_class_map...")
        ecm_a = await conn.fetch("SELECT * FROM tenant_a.event_class_map ORDER BY id")
        ecm_map = {} # old_ecm_id -> new_ecm_id
        for ecm in ecm_a:
            old_ecm_id = ecm["id"]
            new_eid = event_map.get(ecm["event_id"])
            new_cid = class_map.get(ecm["class_id"])
            if new_eid and new_cid:
                new_ecm_id = await conn.fetchval("""
                    INSERT INTO tenant_b.event_class_map (event_id, class_id, ticket_price)
                    VALUES ($1, $2, $3)
                    RETURNING id
                """, new_eid, new_cid, ecm["ticket_price"])
                ecm_map[old_ecm_id] = new_ecm_id

        print("14. Cloning resources...")
        res_a = await conn.fetch("SELECT * FROM tenant_a.resources ORDER BY id")
        res_map = {} # old_res_id -> new_res_id
        for r in res_a:
            old_rid = r["id"]
            new_eid = event_map.get(r["event_id"])
            new_rtid = rt_map.get(r["resource_type_id"])
            new_added_by = user_map.get(r["added_by_user_id"])
            if new_eid and new_rtid:
                new_rid = await conn.fetchval("""
                    INSERT INTO tenant_b.resources (event_id, resource_type_id, description, quantity, added_by_user_id)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id
                """, new_eid, new_rtid, r["description"], r["quantity"], new_added_by)
                res_map[old_rid] = new_rid

        print("15. Cloning resource_cost...")
        rc_a = await conn.fetch("SELECT * FROM tenant_a.resource_cost ORDER BY id")
        for rc in rc_a:
            new_eid = event_map.get(rc["event_id"])
            new_rid = res_map.get(rc["resource_id"])
            new_set_by = user_map.get(rc["set_by_user_id"])
            if new_eid and new_rid:
                await conn.execute("""
                    INSERT INTO tenant_b.resource_cost (event_id, resource_id, unit_price, total_cost, currency, set_by_user_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (resource_id) DO NOTHING
                """, new_eid, new_rid, rc["unit_price"], rc["total_cost"], rc["currency"], new_set_by)

        print("16. Cloning enrollment and payments...")
        en_a = await conn.fetch("SELECT * FROM tenant_a.enrollment ORDER BY id")
        en_map = {}
        for en in en_a:
            new_sid = student_map.get(en["student_id"])
            new_ecm_id = ecm_map.get(en["event_class_map_id"])
            if new_sid and new_ecm_id:
                new_en_id = await conn.fetchval("""
                    INSERT INTO tenant_b.enrollment (student_id, event_class_map_id, state)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (student_id, event_class_map_id) DO NOTHING
                    RETURNING id
                """, new_sid, new_ecm_id, en["state"])
                if new_en_id:
                    en_map[en["id"]] = new_en_id

        pay_a = await conn.fetch("SELECT * FROM tenant_a.payments ORDER BY id")
        for p in pay_a:
            new_en_id = en_map.get(p["enrollment_id"])
            if new_en_id:
                await conn.execute("""
                    INSERT INTO tenant_b.payments (enrollment_id, amount, status)
                    VALUES ($1, $2, $3)
                    ON CONFLICT DO NOTHING
                """, new_en_id, p["amount"], p["status"])

        print("\n=======================================================")
        print("  Successfully cloned all data from tenant_a to tenant_b!")
        print("=======================================================\n")

        # Quick verification of counts in tenant_b
        for tbl in ["academic_settings", "blackout_dates", "levels", "class", "teachers", "students", "parenets", "event", "resources", "resource_cost", "enrollment", "payments"]:
            cnt = await conn.fetchval(f'SELECT count(*) FROM tenant_b."{tbl}"')
            print(f"  tenant_b.{tbl}: {cnt} rows")

    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(clone_a_to_b())
