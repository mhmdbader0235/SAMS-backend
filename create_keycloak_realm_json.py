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

# ─── Pure Authentication Users Seed (AuthN Only) ──────────────────────────────
USERS = [
    {"email": "sa@desk.com", "username": "sa@desk.com", "firstName": "Super", "lastName": "Admin", "role": "super_admin", "tenant_id": "tenant_a"},
]

parents_keys = [
    ("parent.alrashid", "Parent", "Al-Rashid"),
    ("parent.bennour",  "Parent", "Ben-Nour"),
    ("parent.chami",    "Parent", "Chami"),
    ("parent.darwish",  "Parent", "Darwish"),
    ("parent.elsayed",  "Parent", "El-Sayed"),
    ("parent.farouk",   "Parent", "Farouk"),
]

students_list = [
    "Ahmed", "Mariam", "Khalid", "Fatima", "Youssef", "Nour",
    "Ziad", "Layla", "Tariq", "Hana", "Sami", "Rana",
    "Karim", "Dalia", "Rami", "Heba", "Adel", "Salma"
]

teachers_list = [
    ("ali.hassan", "Ali", "Hassan"),
    ("sara.karim", "Sara", "Karim"),
    ("omar.nasser", "Omar", "Nasser"),
    ("lina.farouk", "Lina", "Farouk"),
    ("hassan.mahmoud", "Hassan", "Mahmoud"),
    ("dina.rabie", "Dina", "Rabie")
]

for tid in ["tenant_a", "tenant_b"]:
    domain = "schoola.com" if tid == "tenant_a" else "schoolb.com"

    # Admin & Manager
    USERS.append({"email": f"admin@{domain}", "username": f"admin@{domain}", "firstName": "School", "lastName": f"Admin ({tid})", "role": "school_admin", "tenant_id": tid})
    USERS.append({"email": f"manager@{domain}", "username": f"manager@{domain}", "firstName": "Manager", "lastName": f"({tid})", "role": "manager", "tenant_id": tid})

    if tid == "tenant_a":
        USERS.append({"email": "admin@school.com", "username": "admin@school.com", "firstName": "Admin", "lastName": "School", "role": "school_admin", "tenant_id": "tenant_a"})
        USERS.append({"email": "manager@school.com", "username": "manager@school.com", "firstName": "Manager", "lastName": "School", "role": "manager", "tenant_id": "tenant_a"})

    # Teachers
    for prefix, fname, lname in teachers_list:
        u_email = f"{prefix}@{domain}"
        USERS.append({"email": u_email, "username": u_email, "firstName": fname, "lastName": f"{lname} ({tid})", "role": "teacher", "tenant_id": tid})

    # Parents
    for prefix, fname, lname in parents_keys:
        u_email = f"{prefix}@{domain}"
        USERS.append({"email": u_email, "username": u_email, "firstName": fname, "lastName": f"{lname} ({tid})", "role": "parent", "tenant_id": tid})

    # Students
    for sname in students_list:
        u_email = f"{sname.lower()}@{domain}"
        USERS.append({"email": u_email, "username": u_email, "firstName": sname, "lastName": f"({tid})", "role": "student", "tenant_id": tid})


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
            "realm": [],
            "client": {}
        },
        "groups": [],
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
                "groups": [],
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
                "redirectUris": ["http://localhost:5173", "http://localhost:5173/", "http://localhost:5173/*", "http://127.0.0.1:5173", "http://127.0.0.1:5173/", "http://127.0.0.1:5173/*", "http://localhost:5174", "http://localhost:5174/", "http://localhost:5174/*", "http://127.0.0.1:5174", "http://127.0.0.1:5174/", "http://127.0.0.1:5174/*", "http://localhost:3000", "http://localhost:3000/", "http://localhost:3000/*", "http://localhost:9080", "http://localhost:9080/", "http://localhost:9080/*", "http://localhost:8000", "http://localhost:8000/", "http://localhost:8000/*", "http://127.0.0.1:3000", "http://127.0.0.1:3000/", "http://127.0.0.1:3000/*", "http://127.0.0.1:9080", "http://127.0.0.1:9080/", "http://127.0.0.1:9080/*", "http://127.0.0.1:8000", "http://127.0.0.1:8000/", "http://127.0.0.1:8000/*", "http://localhost:*", "http://localhost:*/*", "http://127.0.0.1:*", "http://127.0.0.1:*/*", "*"],
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

print(f"\n[OK] Generated SAMS Realm file:")
print(f"  - '{sams_path}'")
print(f"  - {len(USERS)} multi-tenant users across tenant_a & tenant_b")
print(f"  - Keycloak Organizations: tenant_a, tenant_b")
