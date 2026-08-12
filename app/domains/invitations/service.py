"""
Invitation Service Layer.

Production-grade system brain for managing invitation-only pre-provisioned user registrations.
Orchestrates Keycloak Admin REST API provisioning (user shell creation, group assignment,
organization membership) and coordinates audit persistence with InvitationRepository.
"""

import logging
import os
import httpx
from fastapi import HTTPException, status

from app.core.dependencies import CurrentUser
from app.domains.invitations.repository import InvitationRepository
from app.schemas.invitation import InvitationCreateRequest

logger = logging.getLogger(__name__)

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8000")
KEYCLOAK_ADMIN = os.getenv("KEYCLOAK_ADMIN", "admin")
KEYCLOAK_ADMIN_PASSWORD = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "SAMS")


class InvitationService:
    @staticmethod
    async def _get_admin_token(client: httpx.AsyncClient) -> str:
        """Obtain Keycloak Admin Access Token using shared HTTP client."""
        token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
        data = {
            "client_id": "admin-cli",
            "username": KEYCLOAK_ADMIN,
            "password": KEYCLOAK_ADMIN_PASSWORD,
            "grant_type": "password",
        }
        resp = await client.post(token_url, data=data)
        if resp.status_code >= 300:
            raise RuntimeError(f"Failed to authenticate with Keycloak Admin API: {resp.text}")
        return resp.json()["access_token"]

    @staticmethod
    async def _get_group_id_by_path(client: httpx.AsyncClient, headers: dict, realm: str, target_path: str) -> str | None:
        """Recursively search Keycloak groups for matching path or name."""
        clean_path = target_path.strip().lower()
        resp = await client.get(f"{KEYCLOAK_URL}/admin/realms/{realm}/groups", headers=headers)
        if resp.status_code >= 300:
            return None
        groups = resp.json()
        if not isinstance(groups, list):
            return None

        def search_groups(grp_list: list) -> str | None:
            for g in grp_list:
                g_path = g.get("path", "").strip().lower()
                g_name = g.get("name", "").strip().lower()
                if g_path == clean_path or g_name == clean_path.lstrip("/") or g_path.endswith(clean_path):
                    return g.get("id")
                sub_grps = g.get("subGroups", [])
                if sub_grps:
                    res = search_groups(sub_grps)
                    if res:
                        return res
            return None

        return search_groups(groups)

    @staticmethod
    async def _find_or_create_user_shell(
        client: httpx.AsyncClient,
        headers: dict,
        email: str,
        tenant_id: str,
        role: str,
    ) -> str | None:
        """Look up existing Keycloak user shell or pre-create a new user shell."""
        search_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users?email={email}"
        resp = await client.get(search_url, headers=headers)
        if resp.status_code < 300:
            users = resp.json()
            if isinstance(users, list) and len(users) > 0:
                return users[0]["id"]

        # Pre-create user shell
        user_payload = {
            "username": email,
            "email": email,
            "enabled": True,
            "emailVerified": True,
            "attributes": {
                "tenant_id": [tenant_id],
                "role": [role],
            },
        }
        create_resp = await client.post(
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users",
            headers=headers,
            json=user_payload,
        )
        if create_resp.status_code in (200, 201, 204):
            loc = create_resp.headers.get("Location")
            if loc:
                return loc.split("/")[-1]

        # Fallback search if Location header was missing
        resp = await client.get(search_url, headers=headers)
        if resp.status_code < 300:
            users = resp.json()
            if isinstance(users, list) and len(users) > 0:
                return users[0]["id"]

        logger.warning(f"Could not pre-create Keycloak user shell for {email}: {create_resp.text}")
        return None

    @staticmethod
    async def _assign_user_group(
        client: httpx.AsyncClient,
        headers: dict,
        admin_token: str,
        kc_user_id: str,
        tenant_id: str,
        role: str,
    ) -> None:
        """Assign Keycloak user to matching group by tenant/role path."""
        target_paths = [f"/{tenant_id}/{role}", f"/{role}"]
        group_id = None
        for path in target_paths:
            group_id = await InvitationService._get_group_id_by_path(client, headers, KEYCLOAK_REALM, path)
            if group_id:
                break

        if group_id:
            grp_assign_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{kc_user_id}/groups/{group_id}"
            await client.put(grp_assign_url, headers=headers)

    @staticmethod
    async def _assign_user_organization(
        client: httpx.AsyncClient,
        headers: dict,
        kc_user_id: str,
        tenant_id: str,
    ) -> None:
        """Add Keycloak user to matching tenant Organization."""
        if not tenant_id:
            return
        try:
            orgs_resp = await client.get(
                f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations",
                headers=headers,
            )
            org_id = None
            if orgs_resp.status_code < 300:
                orgs_all = orgs_resp.json()
                if isinstance(orgs_all, list):
                    tid_val = tenant_id.lower()
                    for org in orgs_all:
                        alias_val = (org.get("alias") or "").lower()
                        name_val = (org.get("name") or "").lower()
                        if alias_val == tid_val or name_val == tid_val or tid_val in alias_val or tid_val in name_val:
                            org_id = org.get("id")
                            break
            if org_id:
                add_mem_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations/{org_id}/members"
                await client.post(add_mem_url, headers=headers, json=kc_user_id)
        except Exception as exc:
            logger.warning(f"Could not add invited user to Keycloak Organization {tenant_id}: {exc}")

    @staticmethod
    async def send_user_invitation(
        cp_pool,
        payload: InvitationCreateRequest,
        current_user: CurrentUser,
    ) -> dict:
        """Process pre-provisioned user invitation request cleanly and persistently."""
        repo = InvitationRepository(cp_pool)

        # 1. Enforce business rule: avoid duplicate pending invitation
        existing = await repo.get_invitation_by_email(payload.email, payload.tenant_id)
        if existing:
            raise ValueError(f"A pending invitation already exists for {payload.email} under tenant {payload.tenant_id}")

        # 2. Provision in Keycloak using a single, reusable HTTP client context
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                admin_token = await InvitationService._get_admin_token(client)
            except Exception as exc:
                logger.error(f"Keycloak admin auth failed: {exc}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Keycloak administration service unavailable: {exc}",
                )

            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            # 3. Find or create user shell in Keycloak
            kc_user_id = await InvitationService._find_or_create_user_shell(
                client=client,
                headers=headers,
                email=payload.email,
                tenant_id=payload.tenant_id,
                role=payload.role,
            )

            # 4. Map user to Keycloak Group and Organization side-effects
            if kc_user_id:
                await InvitationService._assign_user_group(
                    client=client,
                    headers=headers,
                    admin_token=admin_token,
                    kc_user_id=kc_user_id,
                    tenant_id=payload.tenant_id,
                    role=payload.role,
                )
                await InvitationService._assign_user_organization(
                    client=client,
                    headers=headers,
                    kc_user_id=kc_user_id,
                    tenant_id=payload.tenant_id,
                )

        # 5. Persist audit log record & control plane user-tenant mapping
        inv_record = await repo.create_invitation_record(
            email=payload.email,
            tenant_id=payload.tenant_id,
            role=payload.role,
            inviter_id=str(current_user.id),
        )
        await repo.upsert_user_tenant_map(
            email=payload.email,
            tenant_id=payload.tenant_id,
            role=payload.role,
        )

        try:
            from app.utils.email import send_invitation_email
            await send_invitation_email(
                to_email=payload.email,
                invite_code=str(inv_record["id"]),
                role=payload.role
            )
        except Exception as exc:
            logger.warning(f"Could not send invitation email to {payload.email}: {exc}")

        return {
            "id": str(inv_record["id"]),
            "email": inv_record["email"],
            "tenant_id": inv_record["tenant_id"],
            "role": inv_record["role"],
            "status": inv_record["status"],
            "created_at": inv_record["created_at"],
            "message": f"Pre-provisioned user invitation created successfully for {payload.email}",
        }
