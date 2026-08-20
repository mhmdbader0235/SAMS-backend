"""
Integration tests for the School Setup (Day-1 onboarding) domain.

Covers: Stage-1 (School Information) commit validation, Stage-2 (Academic
Structure) has_structure semantics, curriculum-system locking after
activation, legacy-tenant grandfathering, and the require_tenant_live hard
gate that blocks every other domain until setup is complete.

tenant_a ships pre-activated via init.sql (see back/init.sql) so existing
tests never see the onboarding wizard. These tests explicitly reset that
activation state to simulate a brand-new, not-yet-onboarded tenant.
"""

from datetime import UTC, datetime

import asyncpg
from httpx import AsyncClient

from tests.integration._helpers import register_school_admin


async def _reset_tenant_to_setup_state(db_pool: asyncpg.Pool) -> None:
    """Undo init.sql's pre-activation so tenant_a behaves like a fresh tenant."""
    await db_pool.execute(
        """
        UPDATE school_profile
        SET activated_at = NULL,
            profile_committed_at = NULL,
            curriculum_locked_at = NULL,
            structure_committed_at = NULL
        """
    )


async def _register(test_client: AsyncClient, email: str, role: str, invite_code: str = "regester123") -> dict:
    # school_admin can no longer self-register with a generic passphrase — it
    # requires a real, targeted invitation (see AuthService.register_user).
    if role == "school_admin":
        token = await register_school_admin(test_client, email)
        return {"Authorization": f"Bearer {token}"}
    if role == "super_admin":
        from app.core.config import SUPER_ADMIN_BOOTSTRAP_CODE

        invite_code = SUPER_ADMIN_BOOTSTRAP_CODE

    payload = {
        "email": email,
        "password": "pass1234",
        "role": role,
        "tenant_id": "tenant_a",
        "invite_code": invite_code,
    }
    resp = await test_client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


VALID_STRUCTURE_PAYLOAD = {
    "system": "UK",
    "levels": [
        {
            "name": "Year 1",
            "isced_level": 1,
            "age_band_min": 5,
            "age_band_max": 6,
            "ordinal": 1,
            "is_active": True,
            "sections": [{"name": "Year 1 - A", "capacity": 25}],
        }
    ],
    "calendar": {"academic_year": "2026-2027", "start_month": 9, "weekend_days": ["Saturday", "Sunday"]},
    "blackout_dates": [],
}

CALENDAR_ONLY_STRUCTURE_PAYLOAD = {
    "system": "UK",
    "levels": [],
    "calendar": {"academic_year": "2026-2027", "start_month": 9, "weekend_days": ["Saturday", "Sunday"]},
    "blackout_dates": [],
}


class TestSetupState:
    async def test_setup_state_reports_setup_for_fresh_tenant(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        await _reset_tenant_to_setup_state(db_pool)
        headers = await _register(test_client, "admin_fresh@school.com", "school_admin")

        resp = await test_client.get("/api/v1/school/setup-state", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "setup"
        assert body["steps"]["profile_committed"] is False
        assert body["steps"]["structure_committed"] is False
        assert len(body["blocking"]) == 4

    async def test_profile_update_requires_admin_role(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        from app.core.config import TEACHER_INVITE_CODE

        await _reset_tenant_to_setup_state(db_pool)
        headers = await _register(test_client, "teacher_noadmin@school.com", "teacher", TEACHER_INVITE_CODE)

        resp = await test_client.put(
            "/api/v1/school/profile",
            json={"legal_name": "Sneaky School"},
            headers=headers,
        )
        assert resp.status_code == 403


class TestHasStructureSemantics:
    async def test_has_structure_false_when_only_calendar_saved(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        headers = await _register(test_client, "admin_calendar@school.com", "school_admin")

        setup_resp = await test_client.post(
            "/api/v1/students/structure/setup", json=CALENDAR_ONLY_STRUCTURE_PAYLOAD, headers=headers
        )
        assert setup_resp.status_code == 200

        structure_resp = await test_client.get("/api/v1/students/structure", headers=headers)
        assert structure_resp.status_code == 200
        assert structure_resp.json()["has_structure"] is False

    async def test_has_structure_true_with_levels_and_sections(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        headers = await _register(test_client, "admin_structure@school.com", "school_admin")

        setup_resp = await test_client.post(
            "/api/v1/students/structure/setup", json=VALID_STRUCTURE_PAYLOAD, headers=headers
        )
        assert setup_resp.status_code == 200

        structure_resp = await test_client.get("/api/v1/students/structure", headers=headers)
        assert structure_resp.json()["has_structure"] is True


class TestOnboardingFlowAndCurriculumLock:
    async def test_full_onboarding_flow_activates_and_locks_curriculum(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        await _reset_tenant_to_setup_state(db_pool)
        headers = await _register(test_client, "admin_flow@school.com", "school_admin")

        # Activation must fail before anything is configured.
        early_activate = await test_client.post("/api/v1/school/setup/activate", headers=headers)
        assert early_activate.status_code == 400

        # Stage 1: commit-profile fails until identity + campus + emergency contact exist.
        early_commit = await test_client.post("/api/v1/school/setup/commit-profile", headers=headers)
        assert early_commit.status_code == 400

        profile_resp = await test_client.put(
            "/api/v1/school/profile",
            json={
                "legal_name": "Alnoor International School",
                "display_name": "Alnoor",
                "school_code": "ALNOOR",
                "country": "Jordan",
                "timezone": "Asia/Amman",
                "currency": "JOD",
            },
            headers=headers,
        )
        assert profile_resp.status_code == 200

        still_missing_campus = await test_client.post("/api/v1/school/setup/commit-profile", headers=headers)
        assert still_missing_campus.status_code == 400

        campus_resp = await test_client.post(
            "/api/v1/school/campuses",
            json={"name": "Main Campus", "address_line1": "Street 1", "city": "Amman", "country": "Jordan"},
            headers=headers,
        )
        assert campus_resp.status_code == 200

        still_missing_contact = await test_client.post("/api/v1/school/setup/commit-profile", headers=headers)
        assert still_missing_contact.status_code == 400

        contact_resp = await test_client.post(
            "/api/v1/school/contacts",
            json={
                "role_title": "Principal",
                "name": "Dr. Layla",
                "phone": "+962790000000",
                "is_emergency_contact": True,
                "escalation_order": 1,
            },
            headers=headers,
        )
        assert contact_resp.status_code == 200

        # Stage 1 complete.
        commit_resp = await test_client.post("/api/v1/school/setup/commit-profile", headers=headers)
        assert commit_resp.status_code == 200
        assert commit_resp.json()["profile_committed_at"] is not None

        # Activation still blocked — Stage 2 (structure) not done yet.
        activate_before_structure = await test_client.post("/api/v1/school/setup/activate", headers=headers)
        assert activate_before_structure.status_code == 400

        # Stage 2: Curriculum Wizard.
        structure_resp = await test_client.post(
            "/api/v1/students/structure/setup", json=VALID_STRUCTURE_PAYLOAD, headers=headers
        )
        assert structure_resp.status_code == 200

        # Activate.
        activate_resp = await test_client.post("/api/v1/school/setup/activate", headers=headers)
        assert activate_resp.status_code == 200
        activated = activate_resp.json()
        assert activated["activated_at"] is not None
        assert activated["curriculum_locked_at"] is not None

        state_resp = await test_client.get("/api/v1/school/setup-state", headers=headers)
        assert state_resp.json()["status"] == "live"

        # Curriculum system is now locked: changing it must be rejected...
        locked_payload = dict(VALID_STRUCTURE_PAYLOAD, system="International")
        locked_resp = await test_client.post("/api/v1/students/structure/setup", json=locked_payload, headers=headers)
        assert locked_resp.status_code == 403

        # ...but editing grades/sections under the SAME system must still work.
        edited_payload = {
            "system": "UK",
            "levels": [
                {
                    "name": "Year 1",
                    "isced_level": 1,
                    "age_band_min": 5,
                    "age_band_max": 6,
                    "ordinal": 1,
                    "is_active": True,
                    "sections": [
                        {"name": "Year 1 - A", "capacity": 25},
                        {"name": "Year 1 - B", "capacity": 25},
                    ],
                }
            ],
            "calendar": {"academic_year": "2026-2027", "start_month": 9, "weekend_days": ["Saturday", "Sunday"]},
            "blackout_dates": [],
        }
        edited_resp = await test_client.post("/api/v1/students/structure/setup", json=edited_payload, headers=headers)
        assert edited_resp.status_code == 200

        final_structure = await test_client.get("/api/v1/students/structure", headers=headers)
        sections = final_structure.json()["levels"][0]["sections"]
        assert len(sections) == 2


class TestLegacyGrandfathering:
    async def test_legacy_tenant_with_existing_structure_is_grandfathered_live(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        await _reset_tenant_to_setup_state(db_pool)
        headers = await _register(test_client, "admin_legacy@school.com", "school_admin")

        # Simulate a tenant that configured its structure through the OLD
        # (pre-onboarding) flow: has_structure becomes true, but the school
        # was never explicitly "activated" through the new wizard.
        structure_resp = await test_client.post(
            "/api/v1/students/structure/setup", json=VALID_STRUCTURE_PAYLOAD, headers=headers
        )
        assert structure_resp.status_code == 200

        state_resp = await test_client.get("/api/v1/school/setup-state", headers=headers)
        assert state_resp.status_code == 200
        assert state_resp.json()["status"] == "live"
        assert state_resp.json()["activated_at"] is not None

    async def test_grandfathering_triggers_inline_from_require_tenant_live(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        """require_tenant_live must self-heal a legacy tenant even if the
        caller never hits GET /school/setup-state first — a raw API client
        (or a future frontend that forgets to bootstrap) must not get
        permanently locked out of an already-configured tenant."""
        await _reset_tenant_to_setup_state(db_pool)
        headers = await _register(test_client, "admin_legacy2@school.com", "school_admin")

        structure_resp = await test_client.post(
            "/api/v1/students/structure/setup", json=VALID_STRUCTURE_PAYLOAD, headers=headers
        )
        assert structure_resp.status_code == 200

        # Go straight to a gated endpoint — never call /school/setup-state.
        classes_resp = await test_client.get("/api/v1/students/classes", headers=headers)
        assert classes_resp.status_code == 200

        row = await db_pool.fetchval("SELECT activated_at FROM school_profile ORDER BY id ASC LIMIT 1")
        assert row is not None


class TestRequireTenantLiveGate:
    async def test_gate_blocks_other_domains_until_activated_then_allows(
        self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db
    ):
        await _reset_tenant_to_setup_state(db_pool)
        headers = await _register(test_client, "admin_gate@school.com", "school_admin")

        # The school-setup and academic-structure endpoints stay reachable
        # while the tenant is still in "setup" status.
        assert (await test_client.get("/api/v1/school/profile", headers=headers)).status_code == 200
        assert (await test_client.get("/api/v1/students/structure", headers=headers)).status_code == 200

        student_payload = {"email": "student_gate@school.com", "password": "pass", "name": "Gate Student"}
        invitation_payload = {"tenant_id": "tenant_a", "role": "teacher"}
        event_payload = {"title": "Blocked Trip", "date": datetime.now(UTC).isoformat()}

        # Every other domain is off-limits before activation.
        assert (await test_client.post("/api/v1/students", json=student_payload, headers=headers)).status_code == 403
        assert (await test_client.post("/api/v1/auth/invitations", json=invitation_payload, headers=headers)).status_code == 403
        assert (await test_client.post("/api/v1/events", json=event_payload, headers=headers)).status_code == 403

        # Finish setup.
        await test_client.put(
            "/api/v1/school/profile",
            json={
                "legal_name": "Gate School",
                "display_name": "Gate School",
                "school_code": "GATE",
                "country": "Jordan",
                "timezone": "Asia/Amman",
                "currency": "JOD",
            },
            headers=headers,
        )
        await test_client.post(
            "/api/v1/school/campuses",
            json={"name": "Main Campus", "address_line1": "Street 1", "city": "Amman", "country": "Jordan"},
            headers=headers,
        )
        await test_client.post(
            "/api/v1/school/contacts",
            json={"role_title": "Principal", "name": "P", "phone": "+962700000000", "is_emergency_contact": True},
            headers=headers,
        )
        commit_resp = await test_client.post("/api/v1/school/setup/commit-profile", headers=headers)
        assert commit_resp.status_code == 200

        structure_resp = await test_client.post(
            "/api/v1/students/structure/setup", json=VALID_STRUCTURE_PAYLOAD, headers=headers
        )
        assert structure_resp.status_code == 200

        activate_resp = await test_client.post("/api/v1/school/setup/activate", headers=headers)
        assert activate_resp.status_code == 200

        # Now every gated action succeeds.
        classes_resp = await test_client.get("/api/v1/students/classes", headers=headers)
        assert classes_resp.status_code == 200
        class_id = classes_resp.json()[0]["id"]

        create_student_resp = await test_client.post(
            "/api/v1/students", json={**student_payload, "class_id": class_id}, headers=headers
        )
        assert create_student_resp.status_code == 200

        create_invite_resp = await test_client.post(
            "/api/v1/auth/invitations", json=invitation_payload, headers=headers
        )
        assert create_invite_resp.status_code == 200

        create_event_resp = await test_client.post("/api/v1/events", json=event_payload, headers=headers)
        assert create_event_resp.status_code == 200

    async def test_super_admin_bypasses_the_gate(self, test_client: AsyncClient, db_pool: asyncpg.Pool, clean_db):
        await _reset_tenant_to_setup_state(db_pool)
        headers = await _register(test_client, "sa_gate@desk.com", "super_admin")

        resp = await test_client.get("/api/v1/students/classes", headers=headers)
        assert resp.status_code == 200
