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

            # Assign user to matching Keycloak Group if group exists
            group_name_map = {
                "super_admin": "Super Admins",
                "school_admin": "School Admins",
                "manager": "Managers",
                "teacher": "Teachers",
                "parent": "Parents",
                "student": "Students",
            }
            group_name = group_name_map.get(role)
            if group_name:
                try:
                    groups_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/groups?search={urllib.parse.quote(group_name)}"
                    req_grp = urllib.request.Request(groups_url, headers=headers, method="GET")
                    with urllib.request.urlopen(req_grp, timeout=3) as resp:
                        grps = json.loads(resp.read().decode())
                        if grps and len(grps) > 0:
                            grp_id = grps[0]["id"]
                            join_grp_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{kc_user_id}/groups/{grp_id}"
                            req_join = urllib.request.Request(join_grp_url, headers=headers, method="PUT")
                            with urllib.request.urlopen(req_join, timeout=3):
                                pass
                except Exception as e:
                    logger.warning(f"Could not assign group {group_name} in Keycloak for {email}: {e}")


        # 5. Add user to Organization
        if kc_user_id and tenant_id:
            org_search_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations?search={urllib.parse.quote(tenant_id)}"
            req = urllib.request.Request(org_search_url, headers=headers, method="GET")
            org_id = None
            try:
                with urllib.request.urlopen(req, timeout=3) as resp:
                    orgs = json.loads(resp.read().decode())
                    if orgs and isinstance(orgs, list) and len(orgs) > 0:
                        # Find matching org by alias or name
                        for org in orgs:
                            alias_val = (org.get("alias") or "").lower()
                            name_val = (org.get("name") or "").lower()
                            tid_val = tenant_id.lower()
                            if alias_val == tid_val or name_val == tid_val or tid_val in alias_val or tid_val in name_val:
                                org_id = org["id"]
                                break
            except Exception as e:
                logger.warning(f"Could not fetch organizations in Keycloak: {e}")

            if not org_id:
                # Search all organizations without filter to match alias or name
                all_orgs_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations"
                req_all = urllib.request.Request(all_orgs_url, headers=headers, method="GET")
                try:
                    with urllib.request.urlopen(req_all, timeout=3) as resp:
                        orgs_all = json.loads(resp.read().decode())
                        if orgs_all and isinstance(orgs_all, list):
                            for org in orgs_all:
                                alias_val = (org.get("alias") or "").lower()
                                name_val = (org.get("name") or "").lower()
                                tid_val = tenant_id.lower()
                                if alias_val == tid_val or name_val == tid_val or tid_val in alias_val or tid_val in name_val:
                                    org_id = org["id"]
                                    break
                except Exception as e:
                    logger.warning(f"Could not fetch all organizations in Keycloak: {e}")

            if not org_id:
                # Create Organization in Keycloak
                create_org_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations"
                org_name = f"Organization {tenant_id.replace('tenant_', '').upper()}"
                domain_name = f"school{tenant_id.replace('tenant_', '').upper()}.com"
                org_payload = {
                    "name": org_name,
                    "alias": tenant_id,
                    "enabled": True,
                    "domains": [
                        {"name": domain_name, "verified": True}
                    ]
                }
                req = urllib.request.Request(create_org_url, data=json.dumps(org_payload).encode(), headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        loc = resp.headers.get("Location")
                        if loc:
                            org_id = loc.split("/")[-1]
                except Exception as e:
                    logger.warning(f"Could not create organization {tenant_id}: {e}")

            if org_id:
                # Assign user to organization in Keycloak (Keycloak 26 requires JSON string of kc_user_id)
                add_member_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations/{org_id}/members"
                req = urllib.request.Request(
                    add_member_url,
                    data=json.dumps(kc_user_id).encode(),
                    headers=headers,
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req, timeout=3):
                        logger.info(f"Successfully added user {email} ({kc_user_id}) to Keycloak Organization {tenant_id} ({org_id})")
                except urllib.error.HTTPError as e:
                    if e.code in (409, 204):
                        pass # User already in org
                    else:
                        logger.warning(f"Could not add user {email} to organization {tenant_id} (HTTP {e.code}): {e}")
                except Exception as e:
                    logger.warning(f"Could not add user {email} to organization {tenant_id}: {e}")

        return True
    except Exception as exc:
        logger.warning(f"Keycloak user sync skipped for {email}: {exc}")
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

