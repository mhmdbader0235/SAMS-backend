"""Regression guard for a real incident: register_school_admin() (used by
~11 call sites across the integration suite) creates a real, targeted
invitation with a target_email, and POST /api/v1/auth/invitations sends a
genuine email via smtplib.SMTP_SSL with no test-side mocking — every test
run was sending real mail through whatever GMAIL_SMTP_USER/PASSWORD were
set in the environment. conftest.py's autouse _never_send_real_email
fixture patches this out for every test; this asserts that guard actually
holds by making smtplib.SMTP_SSL raise if it's ever reached."""

from unittest.mock import patch

from httpx import AsyncClient

from tests.integration._helpers import register_school_admin


async def test_register_school_admin_never_touches_real_smtp(test_client: AsyncClient, clean_db):
    with patch("smtplib.SMTP_SSL", side_effect=AssertionError("smtplib.SMTP_SSL was called for real!")):
        await register_school_admin(test_client, "verify_guard@school.com")
