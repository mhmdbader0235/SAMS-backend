"""Shared helpers for integration tests (not collected by pytest — leading underscore)."""

from httpx import AsyncClient


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
    sa_reg = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"bootstrap_sa_{email}",
            "password": "sapass123",
            "role": "super_admin",
            "invite_code": "regester123",
        },
    )
    assert sa_reg.status_code == 200, sa_reg.text
    sa_token = sa_reg.json()["access_token"]

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
