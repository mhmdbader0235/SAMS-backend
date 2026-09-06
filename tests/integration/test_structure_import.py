"""Integration tests for the grades+classes bulk import endpoints
(/api/v1/students/structure/import/preview and /commit)."""

import asyncpg
from httpx import AsyncClient

from tests.integration._helpers import register_school_admin


def _csv_file(content: str, filename: str = "import.csv"):
    return {"file": (filename, content.encode("utf-8"), "text/csv")}


class TestStructureImportPreview:
    async def test_preview_validates_without_writing_anything(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        token = await register_school_admin(test_client, "admin_preview@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        content = (
            "grade_name,class_name,class_capacity\n"
            "Grade 7,Grade 7 - A,28\n"
            "Grade 7,Grade 7 - B,28\n"
        )
        resp = await test_client.post(
            "/api/v1/students/structure/import/preview",
            files=_csv_file(content),
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_rows"] == 2
        assert body["valid_rows"] == 2
        assert body["error_rows"] == 0
        assert all(r["status"] == "valid" for r in body["rows"])

        # Nothing was actually written.
        listing = await test_client.get("/api/v1/students/levels", headers=headers)
        assert all(lvl["name"] != "Grade 7" for lvl in listing.json())

    async def test_preview_reports_a_row_level_error_without_aborting_the_rest(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        token = await register_school_admin(test_client, "admin_preview_err@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        content = (
            "grade_name,grade_ordinal,class_name\n"
            "Grade 7,not-a-number,Grade 7 - A\n"
            "Grade 8,8,Grade 8 - A\n"
        )
        resp = await test_client.post(
            "/api/v1/students/structure/import/preview",
            files=_csv_file(content),
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["valid_rows"] == 1
        assert body["error_rows"] == 1
        error_row = next(r for r in body["rows"] if r["status"] == "error")
        assert error_row["grade_name"] == "Grade 7"
        assert "grade_ordinal" in error_row["errors"][0]


class TestStructureImportCommit:
    async def test_commit_creates_grades_and_classes(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        token = await register_school_admin(test_client, "admin_commit@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        content = (
            "grade_name,grade_ordinal,class_name,class_capacity\n"
            "Grade 7,7,Grade 7 - A,28\n"
            "Grade 7,7,Grade 7 - B,28\n"
            "Grade 8,8,,\n"
        )
        resp = await test_client.post(
            "/api/v1/students/structure/import/commit",
            files=_csv_file(content),
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["applied_rows"] == 3
        assert body["created_grades"] == 2
        assert body["created_classes"] == 2

        levels = (await test_client.get("/api/v1/students/levels", headers=headers)).json()
        names = {lvl["name"] for lvl in levels}
        assert {"Grade 7", "Grade 8"} <= names

        classes = (await test_client.get("/api/v1/students/classes", headers=headers)).json()
        class_names = {c["name"] for c in classes}
        assert {"Grade 7 - A", "Grade 7 - B"} <= class_names

    async def test_reimporting_a_corrected_file_updates_the_class_not_a_duplicate(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """The confirmed fix: a class-name collision now updates capacity
        instead of silently keeping the stale value -- this is what makes
        "fix a typo in your file and re-upload" actually work."""
        token = await register_school_admin(test_client, "admin_reimport@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        first = "grade_name,class_name,class_capacity\nGrade 7,Grade 7 - A,20\n"
        r1 = await test_client.post(
            "/api/v1/students/structure/import/commit", files=_csv_file(first), headers=headers
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["created_classes"] == 1

        corrected = "grade_name,class_name,class_capacity\nGrade 7,Grade 7 - A,30\n"
        r2 = await test_client.post(
            "/api/v1/students/structure/import/commit", files=_csv_file(corrected), headers=headers
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["created_classes"] == 0
        assert r2.json()["updated_classes"] == 1

        classes = (await test_client.get("/api/v1/students/classes", headers=headers)).json()
        matching = [c for c in classes if c["name"] == "Grade 7 - A"]
        assert len(matching) == 1  # no duplicate row created
        assert matching[0]["capacity"] == 30  # correction actually applied

    async def test_import_never_deletes_a_class_the_file_does_not_mention(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """The correctness-critical regression test: import must be
        additive-only, unlike save_academic_structure (which deletes any
        class row not present in the payload it's given)."""
        token = await register_school_admin(test_client, "admin_no_delete@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        setup = (
            "grade_name,class_name\n"
            "Grade 7,Grade 7 - A\n"
            "Grade 7,Grade 7 - B\n"
            "Grade 7,Grade 7 - C\n"
        )
        r1 = await test_client.post(
            "/api/v1/students/structure/import/commit", files=_csv_file(setup), headers=headers
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["created_classes"] == 3

        # Import a file mentioning only ONE of Grade 7's three sections.
        followup = "grade_name,class_name\nGrade 7,Grade 7 - D\n"
        r2 = await test_client.post(
            "/api/v1/students/structure/import/commit", files=_csv_file(followup), headers=headers
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["created_classes"] == 1

        classes = (await test_client.get("/api/v1/students/classes", headers=headers)).json()
        class_names = {c["name"] for c in classes}
        assert {"Grade 7 - A", "Grade 7 - B", "Grade 7 - C", "Grade 7 - D"} <= class_names

    async def test_works_before_tenant_activation_unlike_the_gated_single_class_endpoint(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """The routing correction from the plan: import sits on the ungated
        router (like /structure/setup) specifically so a brand-new,
        not-yet-activated tenant can use it for initial setup -- unlike
        POST /classes (router_gated), which require_tenant_live blocks.

        clean_db truncates levels/class/etc. but not school_profile, so an
        earlier test's own require_tenant_live grandfathering side effect
        (activated_at gets stamped the first time a prior test finds
        has_structure=True on an unactivated tenant) can leak across tests
        in this file since they all share tenant_a. Reset it explicitly so
        this test's premise -- "tenant_a starts out not activated" -- holds
        regardless of what ran before it, rather than relying on the shared
        clean_db fixture to guarantee something it doesn't actually cover.
        """
        async with db_pool.acquire() as conn:
            await conn.execute('SET search_path TO "tenant_a", public;')
            await conn.execute("UPDATE school_profile SET activated_at = NULL")

        token = await register_school_admin(test_client, "admin_preactivation@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        # Confirm the gated single-class endpoint really is blocked pre-activation.
        gated_resp = await test_client.post(
            "/api/v1/students/levels", json={"name": "Grade 1"}, headers=headers
        )
        assert gated_resp.status_code == 403, gated_resp.text

        content = "grade_name,class_name\nGrade 1,Grade 1 - A\n"
        import_resp = await test_client.post(
            "/api/v1/students/structure/import/commit", files=_csv_file(content), headers=headers
        )
        assert import_resp.status_code == 200, import_resp.text
        assert import_resp.json()["created_classes"] == 1

    async def test_missing_head_teacher_is_a_warning_not_a_blocking_error(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        token = await register_school_admin(test_client, "admin_missing_teacher@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        content = (
            "grade_name,class_name,head_teacher_email\n" "Grade 7,Grade 7 - A,nobody@nowhere.com\n"
        )
        resp = await test_client.post(
            "/api/v1/students/structure/import/commit", files=_csv_file(content), headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created_classes"] == 1
        row = body["row_results"][0]
        assert row["status"] == "valid"
        assert any("nobody@nowhere.com" in w for w in row["warnings"])

    async def test_teacher_without_the_escape_hatch_permission_is_forbidden(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        from app.core.config import TEACHER_INVITE_CODE

        reg = await test_client.post(
            "/api/v1/auth/register",
            json={
                "email": "teacher_import@school.com",
                "password": "teacherpass",
                "role": "teacher",
                "tenant_id": "tenant_a",
                "invite_code": TEACHER_INVITE_CODE,
            },
        )
        assert reg.status_code == 200, reg.text
        headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

        content = "grade_name,class_name\nGrade 7,Grade 7 - A\n"
        resp = await test_client.post(
            "/api/v1/students/structure/import/commit", files=_csv_file(content), headers=headers
        )
        assert resp.status_code == 403, resp.text

    async def test_malformed_file_returns_400(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        token = await register_school_admin(test_client, "admin_malformed@school.com")
        headers = {"Authorization": f"Bearer {token}"}

        resp = await test_client.post(
            "/api/v1/students/structure/import/commit",
            files={"file": ("import.pdf", b"%PDF-1.4 not a real csv", "application/pdf")},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
