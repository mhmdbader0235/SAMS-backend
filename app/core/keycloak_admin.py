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
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "SAMS")


def sync_user_to_keycloak(email: str, password: str, role: str, tenant_id: str | None = None, first_name: str | None = None, last_name: str | None = None) -> bool:
    """Sync a user account to Keycloak realm via Admin REST API."""
    try:
        # 1. Obtain Keycloak Admin Access Token
        token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
        data = urllib.parse.urlencode({
            "client_id": "admin-cli",
            "username": KEYCLOAK_ADMIN,
            "password": KEYCLOAK_ADMIN_PASSWORD,
            "grant_type": "password"
        }).encode()
        req = urllib.request.Request(token_url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            token_res = json.loads(resp.read().decode())
            admin_token = token_res["access_token"]

        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json"
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
                "firstName": first_name or "",
                "lastName": last_name or "",
                "enabled": True,
                "emailVerified": True,
                "attributes": {
                    "tenant_id": [tenant_id],
                    "role": [role]
                },
                "credentials": [{
                    "type": "password",
                    "value": password,
                    "temporary": False,
                }]
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

        # Always update user attributes (tenant_id and role)
        if kc_user_id:
            try:
                update_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{kc_user_id}"
                update_payload = {
                    "email": email,
                    "firstName": first_name or "",
                    "lastName": last_name or "",
                    "attributes": {
                        "tenant_id": [tenant_id],
                        "role": [role]
                    }
                }
                req_up = urllib.request.Request(
                    update_url,
                    data=json.dumps(update_payload).encode(),
                    headers=headers,
                    method="PUT"
                )
                with urllib.request.urlopen(req_up, timeout=3):
                    pass
            except Exception as e:
                logger.warning(f"Could not update attributes for user {email}: {e}")

        return True
    except Exception as exc:
        logger.warning(f"Keycloak user sync skipped for {email}: {exc}")
        return False


def update_user_role_in_keycloak(email: str, new_role: str, tenant_id: str) -> bool:
    """Update an existing user's role and tenant_id in Keycloak."""
    try:
        # 1. Obtain Keycloak Admin Access Token
        token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
        data = urllib.parse.urlencode({
            "client_id": "admin-cli",
            "username": KEYCLOAK_ADMIN,
            "password": KEYCLOAK_ADMIN_PASSWORD,
            "grant_type": "password"
        }).encode()
        req = urllib.request.Request(token_url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            token_res = json.loads(resp.read().decode())
            admin_token = token_res["access_token"]

        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json"
        }

        # 2. Search for user by email in Keycloak
        search_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users?email={urllib.parse.quote(email)}"
        req = urllib.request.Request(search_url, headers=headers, method="GET")
        kc_user_id = None
        with urllib.request.urlopen(req, timeout=3) as resp:
            users = json.loads(resp.read().decode())
            if users and isinstance(users, list) and len(users) > 0:
                kc_user_id = users[0]["id"]
                user_obj = users[0]

        if not kc_user_id:
            logger.warning(f"Cannot update role: User {email} not found in Keycloak.")
            return False

        # 3. Update user attributes
        update_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{kc_user_id}"
        update_payload = {
            "email": email,
            "firstName": user_obj.get("firstName", ""),
            "lastName": user_obj.get("lastName", ""),
            "attributes": {
                "tenant_id": [tenant_id],
                "role": [new_role]
            }
        }
        req_up = urllib.request.Request(
            update_url,
            data=json.dumps(update_payload).encode(),
            headers=headers,
            method="PUT"
        )
        with urllib.request.urlopen(req_up, timeout=3):
            pass

        # 4. Map new role
        return True
    except Exception as exc:
        logger.warning(f"Keycloak role update failed for {email}: {exc}")
        return False
        return False


def ensure_keycloak_frontend_redirect_uris():
    """Ensure Keycloak 'frontend' client has all valid redirect URIs for ports 3000, 9080, 8000."""
    try:
        token_url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
        data = urllib.parse.urlencode({
            "client_id": "admin-cli",
            "username": KEYCLOAK_ADMIN,
            "password": KEYCLOAK_ADMIN_PASSWORD,
            "grant_type": "password"
        }).encode()
        req = urllib.request.Request(token_url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            token_res = json.loads(resp.read().decode())
            admin_token = token_res["access_token"]

        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json"
        }

        # Search for frontend client
        req_c = urllib.request.Request(f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/clients?clientId=frontend", headers=headers)
        with urllib.request.urlopen(req_c, timeout=3) as resp_c:
            clients = json.loads(resp_c.read().decode())
            if clients and isinstance(clients, list) and len(clients) > 0:
                client = clients[0]
                c_id = client["id"]
                current_uris = client.get("redirectUris", [])
                needed_uris = [
                    "http://localhost:5173",
                    "http://localhost:5173/",
                    "http://localhost:5173/*",
                    "http://127.0.0.1:5173",
                    "http://127.0.0.1:5173/",
                    "http://127.0.0.1:5173/*",
                    "http://localhost:5174",
                    "http://localhost:5174/",
                    "http://localhost:5174/*",
                    "http://127.0.0.1:5174",
                    "http://127.0.0.1:5174/",
                    "http://127.0.0.1:5174/*",
                    "http://localhost:3000",
                    "http://localhost:3000/",
                    "http://localhost:3000/*",
                    "http://127.0.0.1:3000",
                    "http://127.0.0.1:3000/",
                    "http://127.0.0.1:3000/*",
                    "http://localhost:9080",
                    "http://localhost:9080/",
                    "http://localhost:9080/*",
                    "http://127.0.0.1:9080",
                    "http://127.0.0.1:9080/",
                    "http://127.0.0.1:9080/*",
                    "http://localhost:8000",
                    "http://localhost:8000/",
                    "http://localhost:8000/*",
                    "http://127.0.0.1:8000",
                    "http://127.0.0.1:8000/",
                    "http://127.0.0.1:8000/*",
                    "http://localhost:*",
                    "http://localhost:*/*",
                    "http://localhost:*?*",
                    "http://localhost:*/*?*",
                    "http://127.0.0.1:*",
                    "http://127.0.0.1:*/*",
                    "http://127.0.0.1:*?*",
                    "http://127.0.0.1:*/*?*",
                    "*"
                ]
                updated_uris = list(set(current_uris + needed_uris))
                if set(updated_uris) != set(current_uris):
                    client["redirectUris"] = updated_uris
                    req_up = urllib.request.Request(
                        f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/clients/{c_id}",
                        data=json.dumps(client).encode(),
                        headers=headers,
                        method="PUT"
                    )
                    with urllib.request.urlopen(req_up, timeout=3):
                        logger.info("Successfully updated Keycloak frontend client valid redirect URIs.")
    except Exception as exc:
        logger.warning(f"Could not update Keycloak frontend client redirect URIs: {exc}")

