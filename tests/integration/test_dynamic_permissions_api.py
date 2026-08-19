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
    async def test_get_roles_and_capabilities_catalog(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        # Register user
        reg_payload = {
            "email": "teacher_cat@school.com",
            "password": "pass",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        reg_resp = await test_client.post("/api/v1/auth/register", json=reg_payload)
        assert reg_resp.status_code == 200
        token = reg_resp.json()["access_token"]

        catalog_resp = await test_client.get(
            "/api/v1/auth/roles-catalog",
            headers={"Authorization": f"Bearer {token}"}
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
        assert "finance" in role_ids

    async def test_school_admin_can_view_and_modify_user_permissions(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        # 1. Register School Admin (via a real invitation — required since
        # school_admin can no longer self-register with a generic passphrase)
        admin_token = await register_school_admin(test_client, "admin_perm@school.com")

        # 2. Register Target Teacher
        teacher_reg = {
            "email": "target_teacher@school.com",
            "password": "teacherpass123",
            "role": "teacher",
            "tenant_id": "tenant_a",
            "invite_code": TEACHER_INVITE_CODE
        }
        teacher_resp = await test_client.post("/api/v1/auth/register", json=teacher_reg)
        assert teacher_resp.status_code == 200

        # 3. Admin lists users in tenant
        list_resp = await test_client.get(
            "/api/v1/auth/users-permissions",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert list_resp.status_code == 200
        users_list = list_resp.json()
        assert len(users_list) >= 2

        target_user = next((u for u in users_list if u["email"] == "target_teacher@school.com"), None)
        assert target_user is not None
        target_id = target_user["id"]

        # 4. Admin assigns multi-role (Teacher + Parent) and custom permissions (billing:refund, event:publish)
        update_payload = {
            "role": "teacher",
            "roles": ["teacher", "parent"],
            "permissions": ["billing:refund", "event:publish"]
        }
        update_resp = await test_client.put(
            f"/api/v1/auth/users/{target_id}/permissions",
            json=update_payload,
            headers={"Authorization": f"Bearer {admin_token}"}
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
            json={"email": "target_teacher@school.com", "password": "teacherpass123", "tenant_id": "tenant_a"}
        )
        assert t_login_resp.status_code == 200
        t_token = t_login_resp.json()["access_token"]

        t_me_resp = await test_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {t_token}"}
        )
        assert t_me_resp.status_code == 200
        me_data = t_me_resp.json()
        # Roles list should include the assigned multi-roles and permissions
        assert "teacher" in me_data["roles"]
        assert "parent" in me_data["roles"]
        assert "billing:refund" in me_data["roles"]
        assert "event:publish" in me_data["roles"]

    async def test_non_admin_forbidden_from_permissions_management(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        # Register a student
        student_reg = {
            "email": "student_hacker@school.com",
            "password": "studentpass123",
            "role": "student",
            "tenant_id": "tenant_a",
            "invite_code": "regester123"
        }
        resp = await test_client.post("/api/v1/auth/register", json=student_reg)
        assert resp.status_code == 200
        student_token = resp.json()["access_token"]

        # Attempt to list permissions matrix -> 403
        list_resp = await test_client.get(
            "/api/v1/auth/users-permissions",
            headers={"Authorization": f"Bearer {student_token}"}
        )
        assert list_resp.status_code == 403

        # Attempt to modify permissions -> 403
        update_resp = await test_client.put(
            "/api/v1/auth/users/1/permissions",
            json={"role": "super_admin", "roles": ["super_admin"], "permissions": ["*"]},
            headers={"Authorization": f"Bearer {student_token}"}
        )
        assert update_resp.status_code == 403

    async def test_update_non_existent_user_returns_404(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        # Register admin
        admin_token = await register_school_admin(test_client, "admin_404@school.com")

        update_resp = await test_client.put(
            "/api/v1/auth/users/999999/permissions",
            json={"role": "teacher", "roles": ["teacher"], "permissions": []},
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert update_resp.status_code == 404


class TestDeleteUser:
    async def test_school_admin_can_delete_tenant_user(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        admin_token = await register_school_admin(test_client, "admin_del@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        t_reg = await test_client.post("/api/v1/auth/register", json={
            "email": "victim_teacher@school.com", "password": "pass", "role": "teacher",
            "tenant_id": "tenant_a", "invite_code": TEACHER_INVITE_CODE
        })
        assert t_reg.status_code == 200

        users = (await test_client.get("/api/v1/auth/users-permissions", headers=admin_headers)).json()
        target = next(u for u in users if u["email"] == "victim_teacher@school.com")

        del_resp = await test_client.delete(f"/api/v1/auth/users/{target['id']}", headers=admin_headers)
        assert del_resp.status_code == 200
        assert del_resp.json()["email"] == "victim_teacher@school.com"

        # The account is really gone — a fresh registration with the same
        # email must succeed (it would fail with "Email already registered"
        # if the row were still there).
        re_reg = await test_client.post("/api/v1/auth/register", json={
            "email": "victim_teacher@school.com", "password": "pass", "role": "teacher",
            "tenant_id": "tenant_a", "invite_code": TEACHER_INVITE_CODE
        })
        assert re_reg.status_code == 200

    async def test_school_admin_cannot_delete_super_admin(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        # register_school_admin's bootstrap super_admin gets JIT-provisioned
        # into tenant_a's users table as a side effect of calling
        # POST /auth/invitations — giving us a real super_admin row to target.
        admin_token = await register_school_admin(test_client, "admin_vs_sa@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        users = (await test_client.get("/api/v1/auth/users-permissions", headers=admin_headers)).json()
        super_admin_row = next(u for u in users if u["role"] == "super_admin")

        del_resp = await test_client.delete(f"/api/v1/auth/users/{super_admin_row['id']}", headers=admin_headers)
        assert del_resp.status_code == 403

    async def test_admin_cannot_delete_own_account(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        admin_token = await register_school_admin(test_client, "admin_self@school.com")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        me = (await test_client.get("/api/v1/auth/me", headers=admin_headers)).json()
        del_resp = await test_client.delete(f"/api/v1/auth/users/{me['user_id']}", headers=admin_headers)
        assert del_resp.status_code == 403

    async def test_super_admin_can_delete_school_admin(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        admin_token = await register_school_admin(test_client, "admin_deletable@school.com")

        sa_reg = await test_client.post("/api/v1/auth/register", json={
            "email": "root_sa@desk.com", "password": "pass", "role": "super_admin", "invite_code": "regester123"
        })
        sa_headers = {"Authorization": f"Bearer {sa_reg.json()['access_token']}"}

        users = (await test_client.get("/api/v1/auth/users-permissions", headers=sa_headers)).json()
        target = next(u for u in users if u["email"] == "admin_deletable@school.com")

        del_resp = await test_client.delete(f"/api/v1/auth/users/{target['id']}", headers=sa_headers)
        assert del_resp.status_code == 200

    async def test_non_admin_forbidden_from_deleting_users(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        student_reg = await test_client.post("/api/v1/auth/register", json={
            "email": "student_del@school.com", "password": "pass", "role": "student",
            "tenant_id": "tenant_a", "invite_code": "regester123"
        })
        student_headers = {"Authorization": f"Bearer {student_reg.json()['access_token']}"}

        del_resp = await test_client.delete("/api/v1/auth/users/1", headers=student_headers)
        assert del_resp.status_code == 403

    async def test_delete_nonexistent_user_returns_404(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        admin_token = await register_school_admin(test_client, "admin_del404@school.com")
        del_resp = await test_client.delete(
            "/api/v1/auth/users/999999", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert del_resp.status_code == 404
