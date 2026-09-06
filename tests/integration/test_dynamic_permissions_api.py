"""
Integration tests for the Dynamic Permissions & Roles API endpoints.

Tests:
1. Roles and capabilities catalog retrieval
2. School Administrator accessing user permissions matrix
3. Modifying user roles (multi-role composite assignment) and custom permissions
4. Enforcement of RBAC guards (non-admin 403 Forbidden)
5. Dynamic role propagation to CurrentUser context and profile
6. Error handling for non-existent users
"""

import asyncpg
from httpx import AsyncClient

from app.core.config import TEACHER_INVITE_CODE
from tests.integration._helpers import register_school_admin


class TestDynamicPermissionsApi:
    async def test_get_roles_and_capabilities_catalog(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        # Register user
        reg_payload = {
            "email": "teacher_cat@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        reg_resp = await test_client.post("/api/v1/auth/register", json=reg_payload)
        assert reg_resp.status_code == 200
        token = reg_resp.json()["access_token"]

        catalog_resp = await test_client.get(
            "/api/v1/auth/roles-catalog", headers={"Authorization": f"Bearer {token}"}
        )
        assert catalog_resp.status_code == 200
        data = catalog_resp.json()
        assert "composite_roles" in data
        assert "categories" in data
        assert "composite_role_permissions" in data

        role_ids = [r["id"] for r in data["composite_roles"]]
        assert "school_admin" in role_ids
        assert "teacher" in role_ids
        assert "manager" in role_ids
        assert "parent" in role_ids
        assert "student" in role_ids
        assert "finance" not in role_ids  # retired -- manager owns pricing/cost duties now

    async def test_school_admin_can_view_and_modify_user_permissions(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        # 1. Register School Admin (via a real invitation — required since
        # school_admin can no longer self-register with a generic passphrase)
        admin_token = await register_school_admin(test_client, "admin_perm@school.com")

        # 2. Register Target Teacher
        teacher_reg = {
            "email": "target_teacher@school.com",
            "password": "teacherpass123",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE,
        }
        teacher_resp = await test_client.post("/api/v1/auth/register", json=teacher_reg)
        assert teacher_resp.status_code == 200

        # 3. Admin lists users in tenant
        list_resp = await test_client.get(
            "/api/v1/auth/users-permissions", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert list_resp.status_code == 200
        users_list = list_resp.json()
        assert len(users_list) >= 2

        target_user = next(
            (u for u in users_list if u["email"] == "target_teacher@school.com"), None
        )
        assert target_user is not None
        target_id = target_user["id"]

        # 4. Admin assigns multi-role (Teacher + Parent) and custom permissions (billing:refund, event:publish)
        update_payload = {
            "role": "teacher",
            "roles": ["teacher", "parent"],
            "permissions": ["billing:refund", "event:publish"],
        }
        update_resp = await test_client.put(
            f"/api/v1/auth/users/{target_id}/permissions",
            json=update_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert update_resp.status_code == 200
        updated_data = update_resp.json()
        assert "teacher" in updated_data["roles"]
        assert "parent" in updated_data["roles"]
        assert "billing:refund" in updated_data["permissions"]
        assert "event:publish" in updated_data["permissions"]

        # 5. Verify teacher login context reflects updated permissions
        t_login_resp = await test_client.post(
            "/api/v1/auth/login",
            json={
                "email": "target_teacher@school.com",
                "password": "teacherpass123",
                "tenant_id": "tenant_a",
            },
        )
        assert t_login_resp.status_code == 200
        t_token = t_login_resp.json()["access_token"]

        t_me_resp = await test_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {t_token}"}
        )
        assert t_me_resp.status_code == 200
        me_data = t_me_resp.json()
        # Roles list should include the assigned multi-roles and permissions
        assert "teacher" in me_data["roles"]
        assert "parent" in me_data["roles"]
        assert "billing:refund" in me_data["roles"]
        assert "event:publish" in me_data["roles"]

    async def test_non_admin_forbidden_from_permissions_management(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        # Register a student
        student_reg = {
            "email": "student_hacker@school.com",
            "password": "studentpass123",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        resp = await test_client.post("/api/v1/auth/register", json=student_reg)
        assert resp.status_code == 200
        student_token = resp.json()["access_token"]

        # Attempt to list permissions matrix -> 403
        list_resp = await test_client.get(
            "/api/v1/auth/users-permissions", headers={"Authorization": f"Bearer {student_token}"}
        )
        assert list_resp.status_code == 403

        # Attempt to modify permissions -> 403
        update_resp = await test_client.put(
            "/api/v1/auth/users/1/permissions",
            json={"role": "super_admin", "roles": ["super_admin"], "permissions": ["*"]},
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert update_resp.status_code == 403

    async def test_student_with_custom_event_create_can_load_audience_data(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """Regression test: a student granted only the custom event:create
        permission could reach the event wizard's audience step (Step 2),
        but GET /api/v1/students/classes and GET /api/v1/students both 403'd
        for them — those endpoints only allowed staff roles or the separate
        class:read/student:read grants, leaving the audience step permanently
        stuck with an empty class list and no visible error."""
        admin_token = await register_school_admin(test_client, "admin_audience@school.com")

        student_reg = {
            "email": "student_audience@school.com",
            "password": "studentpass123",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123",
        }
        student_resp = await test_client.post("/api/v1/auth/register", json=student_reg)
        assert student_resp.status_code == 200
        student_token = student_resp.json()["access_token"]

        # Baseline: a plain student cannot list classes or students.
        baseline_classes = await test_client.get(
            "/api/v1/students/classes", headers={"Authorization": f"Bearer {student_token}"}
        )
        assert baseline_classes.status_code == 403
        baseline_students = await test_client.get(
            "/api/v1/students", headers={"Authorization": f"Bearer {student_token}"}
        )
        assert baseline_students.status_code == 403

        # Admin grants the student event:create only (no class:read/student:read).
        list_resp = await test_client.get(
            "/api/v1/auth/users-permissions", headers={"Authorization": f"Bearer {admin_token}"}
        )
        target_user = next(
            (u for u in list_resp.json() if u["email"] == "student_audience@school.com"), None
        )
        assert target_user is not None
        update_resp = await test_client.put(
            f"/api/v1/auth/users/{target_user['id']}/permissions",
            json={"role": "student", "roles": ["student"], "permissions": ["event:create"]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert update_resp.status_code == 200

        # Fresh login to pick up the newly-granted permission.
        login_resp = await test_client.post(
            "/api/v1/auth/login",
            json={
                "email": "student_audience@school.com",
                "password": "studentpass123",
                "tenant_id": "tenant_a",
            },
        )
        assert login_resp.status_code == 200
        refreshed_token = login_resp.json()["access_token"]

        # Now the audience step's two data calls must succeed.
        classes_resp = await test_client.get(
            "/api/v1/students/classes", headers={"Authorization": f"Bearer {refreshed_token}"}
        )
        assert classes_resp.status_code == 200
        students_resp = await test_client.get(
            "/api/v1/students", headers={"Authorization": f"Bearer {refreshed_token}"}
        )
        assert students_resp.status_code == 200

    async def test_update_non_existent_user_returns_404(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        # Register admin
        admin_token = await register_school_admin(test_client, "admin_404@school.com")

        update_resp = await test_client.put(
            "/api/v1/auth/users/999999/permissions",
            json={"role": "teacher", "roles": ["teacher"], "permissions": []},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert update_resp.status_code == 404


class TestDeleteUser:
    async def test_school_admin_can_delete_tenant_user(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        admin_token = await register_school_admin(test_client, "admin_del@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        t_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "victim_teacher@school.com",
                "password": "pass",
                "role": "teacher",
                "tenant_id": "tenant_a",
                "invite_code": TEACHER_INVITE_CODE,
            },
        )
        assert t_reg.status_code == 200

        users = (
            await test_client.get("/api/v1/auth/users-permissions", headers=admin_headers)
        ).json()
        target = next(u for u in users if u["email"] == "victim_teacher@school.com")

        del_resp = await test_client.delete(
            f"/api/v1/auth/users/{target['id']}", headers=admin_headers
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["email"] == "victim_teacher@school.com"

        # The account is really gone — a fresh registration with the same
        # email must succeed (it would fail with "Email already registered"
        # if the row were still there).
        re_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "victim_teacher@school.com",
                "password": "pass",
                "role": "teacher",
                "tenant_id": "tenant_a",
                "invite_code": TEACHER_INVITE_CODE,
            },
        )
        assert re_reg.status_code == 200

    async def test_school_admin_cannot_delete_super_admin(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        # super_admin is deliberately NOT JIT-provisioned into a tenant's local
        # `users` table (see get_current_user in dependencies.py -- a prior
        # version did this and it surprised tenant admins who found a "user I
        # never created" in their school). So a school_admin's own
        # users-permissions listing for their tenant must never contain a
        # super_admin row in the first place: there is nothing to delete.
        admin_token = await register_school_admin(test_client, "admin_vs_sa@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        users = (
            await test_client.get("/api/v1/auth/users-permissions", headers=admin_headers)
        ).json()
        assert not any(u["role"] == "super_admin" for u in users)

        # Belt-and-braces: even if a super_admin row somehow existed locally,
        # deleting it must still be refused.
        super_admin_row = next((u for u in users if u["role"] == "super_admin"), None)
        if super_admin_row:
            del_resp = await test_client.delete(
                f"/api/v1/auth/users/{super_admin_row['id']}", headers=admin_headers
            )
            assert del_resp.status_code == 403

    async def test_admin_cannot_delete_own_account(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        admin_token = await register_school_admin(test_client, "admin_self@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        me = (await test_client.get("/api/v1/auth/me", headers=admin_headers)).json()
        del_resp = await test_client.delete(
            f"/api/v1/auth/users/{me['user_id']}", headers=admin_headers
        )
        assert del_resp.status_code == 403

    async def test_super_admin_can_delete_school_admin(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        await register_school_admin(test_client, "admin_deletable@school.com")

        from app.core.config import SUPER_ADMIN_BOOTSTRAP_CODE

        sa_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "root_sa@desk.com",
                "password": "pass",
                "role": "super_admin",
                "invite_code": SUPER_ADMIN_BOOTSTRAP_CODE,
            },
        )
        # A super_admin has no home tenant, so a tenant-scoped endpoint needs an
        # explicit X-Tenant-ID. This used to work without one only because
        # unresolved tenants silently defaulted to tenant_a -- which happens to be
        # where register_school_admin puts its admin, so the test passed by
        # coincidence rather than by asking for the right school. Tenant selection
        # is now stated outright.
        sa_headers = {
            "Authorization": f"Bearer {sa_reg.json()['access_token']}",
            "X-Tenant-ID": "tenant_a",
        }

        users = (await test_client.get("/api/v1/auth/users-permissions", headers=sa_headers)).json()
        target = next(u for u in users if u["email"] == "admin_deletable@school.com")

        del_resp = await test_client.delete(
            f"/api/v1/auth/users/{target['id']}", headers=sa_headers
        )
        assert del_resp.status_code == 200

    async def test_non_admin_forbidden_from_deleting_users(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        student_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "student_del@school.com",
                "password": "pass",
                "role": "student",
                "tenant_id": "tenant_a",
                "invite_code": "regester123",
            },
        )
        student_headers = {"Authorization": f"Bearer {student_reg.json()['access_token']}"}

        del_resp = await test_client.delete("/api/v1/auth/users/1", headers=student_headers)
        assert del_resp.status_code == 403

    async def test_delete_nonexistent_user_returns_404(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        admin_token = await register_school_admin(test_client, "admin_del404@school.com")
        del_resp = await test_client.delete(
            "/api/v1/auth/users/999999", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert del_resp.status_code == 404


class TestManagerAcademicHubBlockedUnlessGranted:
    """Regression suite for the Manage Users page and the Academic
    Administration Hub (grades, class sections, curriculum wizard).

    As of this session, level:create/level:manage/class:create/class:update/
    user:create are DEFAULT manager permissions (COMPOSITE_ROLE_PERMISSIONS
    in dependencies.py, store.js, and school_policy.rego), not a per-manager
    grant -- confirmed with the user after they reported that granting these
    to one manager via the matrix had no visible effect (root cause was a
    separate frontend bug, since fixed; this default-promotion is a distinct,
    explicit follow-up request). A bare manager now succeeds on all of those
    without any Manage Permissions step. Only two things remain gated behind
    an explicit grant or a stricter role: user:link (never made a default --
    the user's own escape-hatch grant example never included it) and
    school_admin creation, which stays hard-coded to school_admin/super_admin
    with no escape hatch at all, by design, to prevent a delegated permission
    from ever minting a peer-level admin account. The "granted X unlocks only
    X" tests below now run against `teacher` instead of `manager`, since
    teacher is the role that still starts with none of these -- exercising
    the actual escape-hatch grant mechanism, which manager no longer needs
    for this specific permission set.
    """

    async def _register_manager(self, test_client: AsyncClient, email: str) -> str:
        admin_token = await register_school_admin(test_client, f"admin_for_{email}")
        reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "managerpass123",
                "role": "manager",
                "tenant_id": "tenant_a",
                "invite_code": "SCHOOL-STAFF-2026",
            },
        )
        assert reg.status_code == 200, reg.text
        return admin_token, reg.json()["access_token"]

    async def _register_teacher(self, test_client: AsyncClient, email: str) -> str:
        from app.core.config import TEACHER_INVITE_CODE

        admin_token = await register_school_admin(test_client, f"admin_for_{email}")
        reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "teacherpass123",
                "role": "teacher",
                "tenant_id": "tenant_a",
                "invite_code": TEACHER_INVITE_CODE,
            },
        )
        assert reg.status_code == 200, reg.text
        return admin_token, reg.json()["access_token"]

    async def _grant_permission(
        self, test_client, admin_token, email, permission, role="manager", password="managerpass123"
    ):
        list_resp = await test_client.get(
            "/api/v1/auth/users-permissions",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        target = next(u for u in list_resp.json() if u["email"] == email)
        update_resp = await test_client.put(
            f"/api/v1/auth/users/{target['id']}/permissions",
            json={"role": role, "roles": [role], "permissions": [permission]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert update_resp.status_code == 200, update_resp.text
        # Fresh login to pick up the newly-granted permission -- CurrentUser.roles
        # is resolved from the DB at login/token-mint time, not re-read per request.
        login_resp = await test_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password, "tenant_id": "tenant_a"},
        )
        assert login_resp.status_code == 200
        return login_resp.json()["access_token"]

    async def test_manager_default_now_succeeds_on_academic_hub_writes_but_never_school_admin_creation(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """A bare manager -- no Manage Permissions grant at all -- now
        succeeds on level/class create+update and manager creation, since
        those became role defaults. school_admin creation stays blocked
        unconditionally: it has no escape hatch by design, so a delegated
        permission (present or future) can never mint a peer-level admin
        account."""
        admin_token, mgr_token = await self._register_manager(
            test_client, "mgr_baseline@school.com"
        )
        mgr_headers = {"Authorization": f"Bearer {mgr_token}"}

        create_level_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 7"}, headers=mgr_headers
        )
        assert create_level_resp.status_code == 200, create_level_resp.text
        lvl_id = create_level_resp.json()["level_id"]

        create_class_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "7A", "level_id": lvl_id},
            headers=mgr_headers,
        )
        assert create_class_resp.status_code == 200, create_class_resp.text
        class_id = create_class_resp.json()["id"]

        update_class_resp = await test_client.put(
            f"/api/v1/students/classes/{class_id}",
            json={"name": "7A Renamed"},
            headers=mgr_headers,
        )
        assert update_class_resp.status_code == 200, update_class_resp.text
        assert update_class_resp.json()["name"] == "7A Renamed"

        create_manager_resp = await test_client.post(
            "/api/v1/students/managers",
            json={"email": "another_manager@school.com", "password": "pass"},
            headers=mgr_headers,
        )
        assert create_manager_resp.status_code == 200, create_manager_resp.text

        create_admin_resp = await test_client.post(
            "/api/v1/students/school-admins",
            json={"email": "rogue_admin@school.com", "password": "pass"},
            headers=mgr_headers,
        )
        assert create_admin_resp.status_code == 403

    async def test_teacher_granted_level_create_can_create_a_level_only(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """teacher (unlike manager, as of this session) still starts with
        none of the Academic Hub permissions, so it's the role that actually
        exercises the granular escape-hatch grant mechanism."""
        admin_token, _ = await self._register_teacher(test_client, "tch_level_create@school.com")
        granted_token = await self._grant_permission(
            test_client,
            admin_token,
            "tch_level_create@school.com",
            "level:create",
            role="teacher",
            password="teacherpass123",
        )
        granted_headers = {"Authorization": f"Bearer {granted_token}"}

        lvl_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 8"}, headers=granted_headers
        )
        assert lvl_resp.status_code == 200
        lvl_id = lvl_resp.json()["level_id"]

        # The grant is scoped to level:create only -- class:create is a
        # different permission, so this must still 403.
        cls_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "8A", "level_id": lvl_id},
            headers=granted_headers,
        )
        assert cls_resp.status_code == 403

    async def test_teacher_granted_class_create_can_create_a_class_only(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        admin_token, _ = await self._register_teacher(test_client, "tch_class_create@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        lvl = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 9"}, headers=admin_headers
        )
        lvl_id = lvl.json()["level_id"]

        granted_token = await self._grant_permission(
            test_client,
            admin_token,
            "tch_class_create@school.com",
            "class:create",
            role="teacher",
            password="teacherpass123",
        )
        granted_headers = {"Authorization": f"Bearer {granted_token}"}

        cls_resp = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "9A", "level_id": lvl_id},
            headers=granted_headers,
        )
        assert cls_resp.status_code == 200
        class_id = cls_resp.json()["id"]

        # class:create does not imply class:update.
        update_resp = await test_client.put(
            f"/api/v1/students/classes/{class_id}",
            json={"name": "9A Renamed"},
            headers=granted_headers,
        )
        assert update_resp.status_code == 403

    async def test_teacher_granted_class_update_can_update_a_class_only(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        admin_token, _ = await self._register_teacher(test_client, "tch_class_update@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        lvl = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 10"}, headers=admin_headers
        )
        cls = await test_client.post(
            "/api/v1/students/classes",
            json={"name": "10A", "level_id": lvl.json()["level_id"]},
            headers=admin_headers,
        )
        class_id = cls.json()["id"]

        granted_token = await self._grant_permission(
            test_client,
            admin_token,
            "tch_class_update@school.com",
            "class:update",
            role="teacher",
            password="teacherpass123",
        )
        granted_headers = {"Authorization": f"Bearer {granted_token}"}

        update_resp = await test_client.put(
            f"/api/v1/students/classes/{class_id}",
            json={"name": "10A Renamed"},
            headers=granted_headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["name"] == "10A Renamed"

    async def test_manager_granted_user_create_can_create_manager_but_not_school_admin(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """user:create is deliberately narrower than the admin role itself --
        it opens create_manager (a lesser-privileged staff account) but NOT
        create_school_admin, which stays strictly school_admin/super_admin so
        a delegated grant can never mint a peer-level admin account."""
        admin_token, _ = await self._register_manager(test_client, "mgr_user_create@school.com")
        granted_token = await self._grant_permission(
            test_client, admin_token, "mgr_user_create@school.com", "user:create"
        )
        granted_headers = {"Authorization": f"Bearer {granted_token}"}

        create_manager_resp = await test_client.post(
            "/api/v1/students/managers",
            json={"email": "delegated_new_manager@school.com", "password": "pass"},
            headers=granted_headers,
        )
        assert create_manager_resp.status_code == 200
        assert create_manager_resp.json()["role"] == "manager"

        create_admin_resp = await test_client.post(
            "/api/v1/students/school-admins",
            json={"email": "delegated_new_admin@school.com", "password": "pass"},
            headers=granted_headers,
        )
        assert create_admin_resp.status_code == 403

    async def test_manager_granted_user_link_can_link_a_parent_and_student(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """user:link is the real, cataloged permission behind the sidebar's
        "System Admin" link (whose actual feature is parent-student linking,
        see ManageAdminView.vue) -- it existed in COMPOSITE_ROLE_PERMISSIONS
        but was never checked anywhere until now, so granting it previously
        did nothing."""
        admin_token, mgr_token = await self._register_manager(
            test_client, "mgr_user_link@school.com"
        )
        mgr_headers = {"Authorization": f"Bearer {mgr_token}"}

        student_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "student_for_link@school.com",
                "password": "pass",
                "role": "student",
                "tenant_id": "tenant_a",
                "invite_code": "regester123",
            },
        )
        assert student_reg.status_code == 200
        student_id = int(
            (
                await test_client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {student_reg.json()['access_token']}"},
                )
            ).json()["user_id"]
        )

        parent_reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "parent_for_link@school.com",
                "password": "pass",
                "role": "parent",
                "tenant_id": "tenant_a",
                "invite_code": "regester123",
            },
        )
        assert parent_reg.status_code == 200
        parent_id = int(
            (
                await test_client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {parent_reg.json()['access_token']}"},
                )
            ).json()["user_id"]
        )

        # Baseline: this same bare manager cannot link.
        baseline_resp = await test_client.post(
            "/api/v1/students/link-parent",
            json={"student_id": student_id, "parent_id": parent_id},
            headers=mgr_headers,
        )
        assert baseline_resp.status_code == 403

        granted_token = await self._grant_permission(
            test_client, admin_token, "mgr_user_link@school.com", "user:link"
        )
        granted_headers = {"Authorization": f"Bearer {granted_token}"}

        link_resp = await test_client.post(
            "/api/v1/students/link-parent",
            json={"student_id": student_id, "parent_id": parent_id},
            headers=granted_headers,
        )
        assert link_resp.status_code == 200
