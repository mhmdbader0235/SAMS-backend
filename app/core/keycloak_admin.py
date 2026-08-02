"""
Keycloak Admin REST API Synchronization Helper.

Automatically provisions and syncs newly registered/created users from SchoolDesk
into the Keycloak realm so that users can seamlessly authenticate via both internal JWT
and Keycloak OIDC SSO.
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8000")
KEYCLOAK_ADMIN = os.getenv("KEYCLOAK_ADMIN", "admin")
KEYCLOAK_ADMIN_PASSWORD = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "schooldesk")


def sync_user_to_keycloak(email: str, password: str, role: str) -> bool:
    """Sync a user account to Keycloak realm via Admin REST API."""
    try:
        # 1. Obtain Keycloak Admin Access Token
        token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
        body = urllib.parse.urlencode({
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": KEYCLOAK_ADMIN,
            "password": KEYCLOAK_ADMIN_PASSWORD,
        }).encode()
        req = urllib.request.Request(
            token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            admin_token = json.loads(resp.read().decode())["access_token"]

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {admin_token}",
        }

        # 2. Search for user by email in Keycloak
        search_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users?email={urllib.parse.quote(email)}"
        req = urllib.request.Request(search_url, headers=headers, method="GET")
        kc_user_id = None
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                users = json.loads(resp.read().decode())
                if users and isinstance(users, list) and len(users) > 0:
                    kc_user_id = users[0]["id"]
        except Exception:
            pass

        # 3. Create user if not existing in Keycloak
        if not kc_user_id:
            create_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users"
            user_payload = {
                "username": email,
                "email": email,
                "enabled": True,
                "emailVerified": True,
                "credentials": [{
                    "type": "password",
                    "value": password,
                    "temporary": False,
                }],
            }
            req = urllib.request.Request(
                create_url,
                data=json.dumps(user_payload).encode(),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                loc = resp.headers.get("Location")
                if loc:
                    kc_user_id = loc.split("/")[-1]

        # 4. Map role to user in Keycloak if user ID exists
        if kc_user_id and role:
            role_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/roles/{urllib.parse.quote(role)}"
            req = urllib.request.Request(role_url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=3) as resp:
                    role_obj = json.loads(resp.read().decode())
                    if role_obj and "id" in role_obj:
                        assign_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{kc_user_id}/role-mappings/realm"
                        req_assign = urllib.request.Request(
                            assign_url,
                            data=json.dumps([role_obj]).encode(),
                            headers=headers,
                            method="POST",
                        )
                        with urllib.request.urlopen(req_assign, timeout=3):
                            pass
            except Exception as e:
                logger.warning(f"Could not assign role {role} in Keycloak for {email}: {e}")

        return True
    except Exception as exc:
        logger.warning(f"Keycloak user sync skipped for {email}: {exc}")
        return False
