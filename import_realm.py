"""
Auto-creates/re-imports the 'schooldesk' realm into Keycloak via Admin REST API.
Usage: python import_realm.py
"""

import json
import sys
import urllib.request
import urllib.parse
import urllib.error
import os

KEYCLOAK_URL  = os.getenv("KEYCLOAK_URL",  "http://localhost:8000")
ADMIN_USER    = os.getenv("KEYCLOAK_ADMIN", "admin")
ADMIN_PASS    = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
# Look for the JSON next to import_realm.py first, then in cwd
_script_dir   = os.path.dirname(os.path.abspath(__file__))
REALM_FILE    = os.path.join(_script_dir, "schooldesk-realm.json")
if not os.path.exists(REALM_FILE):
    REALM_FILE = os.path.join(os.getcwd(), "schooldesk-realm.json")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def http(method, url, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else {}

def get_admin_token():
    url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
    body = urllib.parse.urlencode({
        "grant_type":    "password",
        "client_id":     "admin-cli",
        "username":      ADMIN_USER,
        "password":      ADMIN_PASS,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())["access_token"]
    except urllib.error.HTTPError as e:
        print(f"[ERROR] Could not authenticate to Keycloak: {e.read().decode()}")
        print(f"        Make sure Keycloak is running at {KEYCLOAK_URL}")
        print(f"        and the admin credentials are correct (admin/admin by default).")
        sys.exit(1)

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  SchoolDesk Keycloak Realm Importer")
    print("=" * 55)

    # 1. Authenticate
    print(f"\n[1/4] Authenticating to {KEYCLOAK_URL} ...")
    token = get_admin_token()
    print("      OK — admin token obtained.")

    # 2. Delete existing realm if present
    print("\n[2/4] Checking for existing 'schooldesk' realm ...")
    status, _ = http("GET", f"{KEYCLOAK_URL}/admin/realms/schooldesk", token=token)
    if status == 200:
        print("      Found existing realm — deleting it ...")
        status, _ = http("DELETE", f"{KEYCLOAK_URL}/admin/realms/schooldesk", token=token)
        if status in (200, 204):
            print("      Deleted successfully.")
        else:
            print(f"      [WARNING] Delete returned status {status}. Proceeding anyway.")
    else:
        print("      No existing realm found — proceeding with fresh import.")

    # 3. Load realm JSON
    print(f"\n[3/4] Loading realm file: {REALM_FILE}")
    with open(REALM_FILE, "r", encoding="utf-8") as f:
        realm_data = json.load(f)
    user_count  = len(realm_data.get("users", []))
    role_count  = len(realm_data.get("roles", {}).get("realm", []))
    group_count = len(realm_data.get("groups", []))
    print(f"      Loaded: {user_count} users, {role_count} roles, {group_count} groups")

    # 4. Import realm
    print("\n[4/4] Importing realm into Keycloak ...")
    status, resp = http("POST", f"{KEYCLOAK_URL}/admin/realms", data=realm_data, token=token)

    if status in (200, 201, 204):
        print("\n" + "=" * 55)
        print("  SUCCESS! Realm 'schooldesk' created.")
        print("=" * 55)
        print(f"\n  Users imported : {user_count}")
        print(f"  Roles imported : {role_count}")
        print(f"  Groups imported: {group_count}")
        print(f"\n  Password for all users: 123456")
        print(f"\n  Login URL: {KEYCLOAK_URL}/realms/schooldesk/account")
        print()
    elif status == 409:
        print("\n[ERROR] Realm already exists and could not be deleted.")
        print("        Please manually delete the 'schooldesk' realm in Keycloak Admin Console")
        print(f"        then re-run this script.")
        sys.exit(1)
    else:
        print(f"\n[ERROR] Import failed with status {status}")
        print(f"        Response: {json.dumps(resp, indent=2)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
