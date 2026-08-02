"""
Generates a complete Keycloak realm import file 'schooldesk-realm.json'.

Password for ALL users: 123456
Keycloak-compatible PBKDF2-SHA256 hashing with proper encoding.
"""

import json
import hashlib
import base64
import os
import secrets

PASSWORD_PLAIN = "123456"
ITERATIONS = 27500

# Generate a proper random salt per user (Keycloak uses unique salt per user)
def make_credential(password: str = PASSWORD_PLAIN):
    salt_bytes = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_bytes, ITERATIONS)
    # Keycloak stores these as Base64 (standard, with padding)
    secret_data = json.dumps({
        "value": base64.b64encode(dk).decode(),
        "salt": base64.b64encode(salt_bytes).decode(),
        "additionalParameters": {}
    })
    credential_data = json.dumps({
        "hashIterations": ITERATIONS,
        "algorithm": "pbkdf2-sha256",
        "additionalParameters": {}
    })
    return {
        "type": "password",
        "temporary": False,
        "secretData": secret_data,
        "credentialData": credential_data,
    }

# ─── Roles ────────────────────────────────────────────────────────────────────
ROLES = [
    "system:write", "system:read", "tenant:manage", "tenant:view",
    "school:write", "school:read", "academic:direct", "academic:view",
    "user:create", "user:delete", "user:link", "user:view",
    "event:create", "event:edit", "event:delete", "event:propose",
    "event:review", "event:publish", "event:clone", "event:view_draft",
    "resource:create", "resource:price", "resource:view",
    "teacher:write", "teacher:read",
    "enrollment:request", "enrollment:parent_approve", "enrollment:teacher_approve",
    "enrollment:cancel", "enrollment:view_roster",
    "billing:invoice", "billing:pay", "billing:refund", "billing:audit",
    "content:create", "content:publish", "announcement:manage",
    # App-level roles (used by backend RBAC)
    "school_admin", "teacher", "event_teacher", "manager", "finance", "parent", "student", "super_admin"
]

GROUP_MAPPINGS = {
    "super_admins":     ROLES[:],
    "school_admins":    ["school_admin", "school:write", "school:read", "user:create", "user:delete",
                         "user:link", "user:view", "event:review", "event:publish", "teacher:read",
                         "enrollment:cancel", "enrollment:view_roster", "billing:audit", "announcement:manage"],
    "managers":         ["manager", "school:read", "event:review", "event:publish", "event:view_draft",
                         "resource:view", "enrollment:view_roster"],
    "teachers":         ["teacher", "school:read", "user:view", "event:create", "event:edit",
                         "event:delete", "event:propose", "event:clone", "teacher:write", "teacher:read",
                         "enrollment:teacher_approve", "enrollment:view_roster"],
    "event_organizers": ["event_teacher", "school:read", "event:create", "event:edit", "event:delete",
                         "event:propose", "event:clone", "event:view_draft", "resource:create",
                         "resource:view", "enrollment:view_roster"],
    "finance_officers": ["finance", "school:read", "resource:price", "resource:view",
                         "billing:invoice", "billing:pay", "billing:refund", "billing:audit"],
    "parents":          ["parent", "school:read", "enrollment:parent_approve", "enrollment:cancel", "billing:pay"],
    "students":         ["student", "school:read", "enrollment:request"],
}

# ─── Users ────────────────────────────────────────────────────────────────────
USERS = [
    {"email": "superadmin@schooldesk.com", "username": "superadmin",  "firstName": "Super",   "lastName": "Admin",   "group": "super_admins",    "role": "super_admin"},
    {"email": "admin@school.com",           "username": "admin",       "firstName": "School",  "lastName": "Admin",   "group": "school_admins",   "role": "school_admin"},
    {"email": "manager@school.com",         "username": "manager",     "firstName": "Manager", "lastName": "User",    "group": "managers",        "role": "manager"},
    {"email": "finance@school.com",         "username": "finance",     "firstName": "Finance", "lastName": "User",    "group": "finance_officers","role": "finance"},
]

for i in range(1, 7):
    USERS.append({
        "email": f"teacher{i}@teacher.com",
        "username": f"teacher{i}",
        "firstName": "Teacher",
        "lastName": str(i),
        "group": "teachers",
        "role": "teacher"
    })

for p in ["al1", "bn2", "cm3", "da4", "el5", "fa6"]:
    USERS.append({
        "email": f"parent.{p}@parent.com",
        "username": f"parent_{p}",
        "firstName": "Parent",
        "lastName": p.upper(),
        "group": "parents",
        "role": "parent"
    })

for idx, sname in enumerate(["Ahmed", "Mariam", "Khalid", "Fatima", "Youssef", "Nour",
                               "Ziad", "Layla", "Tariq", "Hana", "Sami", "Rana",
                               "Karim", "Dalia", "Rami", "Heba", "Adel", "Salma"]):
    USERS.append({
        "email": f"{sname.lower()}.s{idx+1}@student.com",
        "username": sname.lower(),
        "firstName": sname,
        "lastName": f"Student{idx+1}",
        "group": "students",
        "role": "student"
    })

# ─── Build Realm JSON ─────────────────────────────────────────────────────────
realm = {
    "id": "schooldesk",
    "realm": "schooldesk",
    "displayName": "SchoolDesk",
    "enabled": True,
    # Login settings
    "registrationAllowed": True,
    "resetPasswordAllowed": True,
    "loginWithEmailAllowed": True,
    "duplicateEmailsAllowed": False,
    "verifyEmail": False,
    # Token settings
    "accessTokenLifespan": 86400,
    "ssoSessionMaxLifespan": 86400,
    "roles": {
        "realm": [{"name": r, "description": f"Role: {r}", "composite": False, "clientRole": False} for r in ROLES]
    },
    "groups": [
        {
            "name": gname,
            "path": f"/{gname}",
            "realmRoles": groles,
            "subGroups": []
        }
        for gname, groles in GROUP_MAPPINGS.items()
    ],
    "users": [],
    "clients": [
        {
            "clientId": "frontend",
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": True,
            "directAccessGrantsEnabled": True,
            "standardFlowEnabled": True,
            "implicitFlowEnabled": False,
            "redirectUris": ["http://localhost:3000/*", "http://127.0.0.1:3000/*", "*"],
            "webOrigins": ["*"],
            "attributes": {
                "post.logout.redirect.uris": "+"
            }
        },
        {
            "clientId": "apisix",
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": False,
            "directAccessGrantsEnabled": True,
            "standardFlowEnabled": True,
            "secret": "apisix_client_secret_placeholder",
            "redirectUris": ["*"],
            "webOrigins": ["*"]
        }
    ]
}

# Build user list
for u in USERS:
    realm["users"].append({
        "username": u["username"],
        "email": u["email"],
        "enabled": True,
        "emailVerified": True,
        "firstName": u["firstName"],
        "lastName": u["lastName"],
        "credentials": [make_credential()],
        "realmRoles": [],
        "groups": [f"/{u['group']}"],
        "attributes": {
            "role": [u["role"]],
            "tenant_id": ["tenant_a"]
        }
    })

# Write output
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schooldesk-realm.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(realm, f, indent=2, ensure_ascii=False)

print(f"\n[OK] Generated '{out_path}'")
print(f"  - {len(ROLES)} roles")
print(f"  - {len(GROUP_MAPPINGS)} groups")
print(f"  - {len(realm['users'])} users  (password: 123456)")
print(f"  - 2 clients: frontend (public) + apisix (confidential)")
print("\n[NEXT] In Keycloak Admin Console:")
print("  1. Delete the 'schooldesk' realm if it already exists")
print("  2. Click 'Create realm' -> Import -> select schooldesk-realm.json")
print("  3. All users will be available with password: 123456\n")
