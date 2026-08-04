"""
Generates a complete Keycloak realm import file 'SAMS-realm.json' and 'schooldesk-realm.json'.

Single Realm: SAMS
Tenants: Segregated as Keycloak Organizations (tenant_a, tenant_b)
Password for ALL users: 123456 / 123321
"""

import json
import hashlib
import base64
import os
import secrets

PASSWORD_PLAIN = "123456"
ITERATIONS = 27500

def make_credential(password: str = PASSWORD_PLAIN):
    salt_bytes = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_bytes, ITERATIONS)
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

# Generate users for both tenant_a and tenant_b in the single SAMS realm
USERS = [
    {"email": "superadmin@schooldesk.com", "username": "superadmin", "firstName": "Super", "lastName": "Admin", "group": "super_admins", "role": "super_admin", "tenant_id": "tenant_a"},
]

for tid in ["tenant_a", "tenant_b"]:
    USERS.extend([
        {"email": f"admin.{tid}@school.com", "username": f"admin_{tid}", "firstName": "Admin", "lastName": tid.upper(), "group": "school_admins", "role": "school_admin", "tenant_id": tid},
        {"email": f"manager.{tid}@school.com", "username": f"manager_{tid}", "firstName": "Manager", "lastName": tid.upper(), "group": "managers", "role": "manager", "tenant_id": tid},
    ])
    if tid == "tenant_a":
        USERS.append({"email": "admin@school.com", "username": "admin", "firstName": "Admin", "lastName": "A", "group": "school_admins", "role": "school_admin", "tenant_id": "tenant_a"})
        USERS.append({"email": "manager@school.com", "username": "manager", "firstName": "Manager", "lastName": "A", "group": "managers", "role": "manager", "tenant_id": "tenant_a"})
    else:
        USERS.append({"email": "admin_b@school.com", "username": "admin_b", "firstName": "Admin", "lastName": "B", "group": "school_admins", "role": "school_admin", "tenant_id": "tenant_b"})

    for i, tname in enumerate(["Ali Hassan", "Sara Karim", "Omar Nasser", "Lina Farouk", "Hassan Mahmoud", "Dina Rabie"]):
        u_email = f"{tname.lower().replace(' ', '.')}.{tid}@school.com"
        u_name = f"teacher_{tid}_{i+1}"
        USERS.append({"email": u_email, "username": u_name, "firstName": tname.split()[0], "lastName": tname.split()[1], "group": "teachers", "role": "teacher", "tenant_id": tid})

    for p in ["al1", "bn2", "cm3", "da4", "el5", "fa6"]:
        USERS.append({"email": f"parent.{p}.{tid}@school.com", "username": f"parent_{p}_{tid}", "firstName": "Parent", "lastName": p.upper(), "group": "parents", "role": "parent", "tenant_id": tid})

    for idx, sname in enumerate(["Ahmed", "Mariam", "Khalid", "Fatima", "Youssef", "Nour", "Ziad", "Layla", "Tariq", "Hana", "Sami", "Rana", "Karim", "Dalia", "Rami", "Heba", "Adel", "Salma"]):
        USERS.append({"email": f"{sname.lower()}.s{idx+1}.{tid}@school.com", "username": f"{sname.lower()}_{tid}", "firstName": sname, "lastName": f"Student{idx+1}", "group": "students", "role": "student", "tenant_id": tid})


def build_realm(realm_name="SAMS"):
    return {
        "id": realm_name,
        "realm": realm_name,
        "displayName": realm_name,
        "enabled": True,
        "registrationAllowed": True,
        "resetPasswordAllowed": True,
        "loginWithEmailAllowed": True,
        "duplicateEmailsAllowed": False,
        "verifyEmail": False,
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
        "organizations": [
            {"name": "tenant_a", "domains": []},
            {"name": "tenant_b", "domains": []}
        ],
        "users": [
            {
                "username": u["username"],
                "email": u["email"],
                "enabled": True,
                "emailVerified": True,
                "firstName": u["firstName"],
                "lastName": u["lastName"],
                "credentials": [make_credential("123456"), make_credential("123321")],
                "realmRoles": [],
                "groups": [f"/{u['group']}"],
                "attributes": {
                    "role": [u["role"]],
                    "tenant_id": [u["tenant_id"]]
                }
            }
            for u in USERS
        ],
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

base_dir = os.path.dirname(os.path.abspath(__file__))

# Write SAMS-realm.json
sams_realm = build_realm("SAMS")
sams_path = os.path.join(base_dir, "SAMS-realm.json")
with open(sams_path, "w", encoding="utf-8") as f:
    json.dump(sams_realm, f, indent=2, ensure_ascii=False)

# Write schooldesk-realm.json for backwards compatibility
sd_realm = build_realm("schooldesk")
sd_path = os.path.join(base_dir, "schooldesk-realm.json")
with open(sd_path, "w", encoding="utf-8") as f:
    json.dump(sd_realm, f, indent=2, ensure_ascii=False)

print(f"\n[OK] Generated SAMS Realm files:")
print(f"  - '{sams_path}'")
print(f"  - '{sd_path}'")
print(f"  - {len(USERS)} multi-tenant users across tenant_a & tenant_b")
print(f"  - Keycloak Organizations: tenant_a, tenant_b")
