"""Shared helpers for integration tests (not collected by pytest — leading underscore)."""

from httpx import AsyncClient

from app.core.config import SUPER_ADMIN_ALLOWED_EMAIL, SUPER_ADMIN_BOOTSTRAP_CODE

# The platform allows exactly one super_admin identity (SUPER_ADMIN_ALLOWED_EMAIL);
# register_user rejects any other email regardless of bootstrap code. Every test
# helper/file that needs a super_admin actor must go through get_super_admin_token
# below and share this password, rather than registering its own with a throwaway
# email — the whole point of the lockdown this enforces.
SUPER_ADMIN_TEST_PASSWORD = "sapass123"


async def get_super_admin_token(test_client: AsyncClient) -> str:
    """Return an access token for the platform's one super_admin account.

    clean_db truncates super_admins before every test, so the first call in a
    test registers it fresh; a test that needs the token more than once (e.g.
    via register_school_admin below, then again for its own use) would hit
    "Email already registered" on the second register — fall back to login
    instead, since by then the account already exists for this test.
    """
    resp = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": SUPER_ADMIN_ALLOWED_EMAIL,
            "password": SUPER_ADMIN_TEST_PASSWORD,
            "role": "super_admin",
            "invite_code": SUPER_ADMIN_BOOTSTRAP_CODE,
        },
    )
    if resp.status_code == 200:
        return resp.json()["access_token"]

    login_resp = await test_client.post(
        "/api/v1/auth/login",
        json={"email": SUPER_ADMIN_ALLOWED_EMAIL, "password": SUPER_ADMIN_TEST_PASSWORD},
    )
    assert login_resp.status_code == 200, (resp.text, login_resp.text)
    return login_resp.json()["access_token"]


async def register_school_admin(
    test_client: AsyncClient,
    email: str,
    tenant_id: str = "tenant_a",
    password: str = "adminpass123",
) -> str:
    """Register a school_admin the way production actually requires it: via a
    real, targeted invitation from a super_admin — not a generic fallback
    passphrase (AuthService.register_user rejects school_admin registration
    without a matched invitation record). Returns the resulting access token.
    """
    sa_token = await get_super_admin_token(test_client)

    invite_resp = await test_client.post(
        "/api/v1/auth/invitations",
        json={"tenant_id": tenant_id, "role": "school_admin", "target_email": email},
        headers={"Authorization": f"Bearer {sa_token}"},
    )
    assert invite_resp.status_code == 200, invite_resp.text
    invite_code = invite_resp.json()["code"]

    reg_resp = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "role": "school_admin",
            "tenant_id": tenant_id,
            "invite_code": invite_code,
        },
    )
    assert reg_resp.status_code == 200, reg_resp.text
    return reg_resp.json()["access_token"]
