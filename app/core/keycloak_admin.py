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


def sync_user_to_keycloak(email: str, password: str, role: str, tenant_id: str = "tenant_a") -> bool:
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
                "enabled": True,
                "emailVerified": True,
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
                    if orgs and len(orgs) > 0:
                        org_id = orgs[0]["id"]
            except Exception:
                pass

            if not org_id:
                # Create Organization
                create_org_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations"
                org_payload = {"name": tenant_id}
                req = urllib.request.Request(create_org_url, data=json.dumps(org_payload).encode(), headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        loc = resp.headers.get("Location")
                        if loc:
                            org_id = loc.split("/")[-1]
                except Exception as e:
                    logger.warning(f"Could not create organization {tenant_id}: {e}")

            if org_id:
                # Assign user to organization
                add_member_url = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations/{org_id}/members"
                member_payload = [{"id": kc_user_id}]
                req = urllib.request.Request(add_member_url, data=json.dumps(member_payload).encode(), headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=3):
                        pass
                except Exception as e:
                    logger.warning(f"Could not add user {email} to organization {tenant_id}: {e}")

        return True
    except Exception as exc:
        logger.warning(f"Keycloak user sync skipped for {email}: {exc}")
        return False
