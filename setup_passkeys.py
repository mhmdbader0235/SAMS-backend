"""Configure Keycloak WebAuthn Passwordless (Passkey) authentication for the
SAMS realm -- idempotent, safe to re-run.

    cd back && python setup_passkeys.py                # dry run, prints the plan
    cd back && python setup_passkeys.py --apply         # actually apply it
    cd back && python setup_passkeys.py --apply --prompt-new-users
    cd back && WEBAUTHN_VERIFY_PASSWORD=123321 python setup_passkeys.py --apply --verify-user admin.a@tenanta.com
    cd back && python setup_passkeys.py --apply --verify-user admin.a@tenanta.com   # prompts for the password instead

Mirrors backfill_org_members.py's dry-run-by-default / --apply convention: this
mutates the realm's active login flow, so a silent default-apply is the wrong
default for a script like this one.

What it does, each step re-checking live state first so a re-run always
converges rather than erroring or duplicating:

  1. Sets the realm's 8 `webAuthnPolicyPasswordless*` fields to the spec'd
     values (RP Entity Name "SchoolDesk Identity", RP ID from WEBAUTHN_RP_ID,
     ES256/RS256, Resident Key required, User Verification required,
     Authenticator Attachment not specified, Attestation Conveyance none).
     The remaining 4 standard fields (timeout, avoid-same-authenticator,
     acceptable AAGUIDs, extra origins) have no spec'd value and are left
     untouched.
  2. Ensures a flow aliased "browser-passwordless" exists (copied from the
     realm's current `browser` flow the first time, reused on every re-run),
     containing a top-level `webauthn-authenticator-passwordless` execution
     set to ALTERNATIVE, and binds it as the realm's active `browserFlow`.
  3. Confirms the `webauthn-register-passwordless` required action is
     registered and enabled (it already is on this realm -- verified against
     the live Keycloak 26.7 instance; this step is a no-op confirmation, not
     new configuration). With --prompt-new-users, also sets it as a
     `defaultAction` so every new user is prompted to enrol a passkey.
  4. If --verify-user is given (with a password from WEBAUTHN_VERIFY_PASSWORD
     or an interactive prompt -- never a CLI flag, which would land in shell
     history and be visible to other local users via `ps` while this runs),
     confirms a plain `grant_type=password` token request still succeeds after
     the change -- that flow is `direct grant`, a separate flow untouched by
     this script, so this is a regression check, not a functional dependency
     of the setup.

Env vars (same names/defaults as app/core/keycloak_admin.py, so this script
reads the same environment the backend does -- KEYCLOAK_ADMIN_USER from an
older draft of this task's brief does not exist anywhere else in this
codebase, so KEYCLOAK_ADMIN is used here too, matching every other Keycloak
admin script in this repo):

    KEYCLOAK_URL             default http://localhost:8000
    KEYCLOAK_REALM            default SAMS
    KEYCLOAK_ADMIN            default admin
    KEYCLOAK_ADMIN_PASSWORD   default admin
    WEBAUTHN_RP_ID            default localhost

    WEBAUTHN_RP_ID is a bare hostname (no scheme, no port). It must be a
    registrable-domain match for whatever origin actually performs the
    WebAuthn ceremony -- since this realm's login pages are Keycloak's own
    hosted UI (redirectToKeycloak() in the frontend, not a form posted to the
    SPA's own origin), that is Keycloak's origin, not the frontend's. In dev
    that's "localhost" (KEYCLOAK_URL=http://localhost:8000). In production,
    set it to the public hostname that fronts Keycloak.
"""

import argparse
import getpass
import os
import sys

import httpx

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8000")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "SAMS")
KEYCLOAK_ADMIN = os.getenv("KEYCLOAK_ADMIN", "admin")
KEYCLOAK_ADMIN_PASSWORD = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
WEBAUTHN_RP_ID = os.getenv("WEBAUTHN_RP_ID", "localhost")

PASSWORDLESS_FLOW_ALIAS = "browser-passwordless"
PASSWORDLESS_PROVIDER_ID = "webauthn-authenticator-passwordless"
PASSWORDLESS_REQUIRED_ACTION = "webauthn-register-passwordless"

WEBAUTHN_POLICY_FIELDS = {
    "webAuthnPolicyPasswordlessRpEntityName": "SchoolDesk Identity",
    "webAuthnPolicyPasswordlessSignatureAlgorithms": ["ES256", "RS256"],
    "webAuthnPolicyPasswordlessRpId": WEBAUTHN_RP_ID,
    "webAuthnPolicyPasswordlessAttestationConveyancePreference": "none",
    "webAuthnPolicyPasswordlessAuthenticatorAttachment": "not specified",
    # Both fields drive Keycloak's admin-console "Require Resident Key"
    # toggle -- RequireResidentKey is the older WebAuthn L1 Yes/No field,
    # ResidentKey the newer L2 tri-state one Keycloak now actually reads.
    # Set both so they can never quietly disagree.
    "webAuthnPolicyPasswordlessRequireResidentKey": "Yes",
    "webAuthnPolicyPasswordlessResidentKey": "required",
    "webAuthnPolicyPasswordlessUserVerificationRequirement": "required",
}


def admin_token(client: httpx.Client) -> str:
    resp = client.post(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": KEYCLOAK_ADMIN,
            "password": KEYCLOAK_ADMIN_PASSWORD,
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_realm(client: httpx.Client, token: str) -> dict:
    resp = client.get(
        f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json()


def plan_webauthn_policy(realm: dict) -> dict:
    """Fields that differ from the desired spec -- empty dict means no-op."""
    return {
        k: v for k, v in WEBAUTHN_POLICY_FIELDS.items() if realm.get(k) != v
    }


def apply_webauthn_policy(client: httpx.Client, token: str, realm: dict, diff: dict) -> None:
    updated = dict(realm)
    updated.update(diff)
    resp = client.put(
        f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}",
        headers={"Authorization": f"Bearer {token}"},
        json=updated,
    )
    resp.raise_for_status()


def get_flows(client: httpx.Client, token: str) -> list[dict]:
    resp = client.get(
        f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/authentication/flows",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json()


def get_executions(client: httpx.Client, token: str, flow_alias: str) -> list[dict]:
    resp = client.get(
        f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/authentication/flows/{flow_alias}/executions",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json()


def _find_webauthn_executions(execs: list[dict]) -> list[dict]:
    """Every top-level execution for our provider -- not just the first.

    Keycloak does not itself prevent adding the same provider to a flow
    twice (verified: POSTing the same provider a second time creates a
    second, independent DISABLED execution rather than erroring). Taking
    only the first match would let a manually-duplicated execution (e.g. a
    human double-clicking "Add step" in the admin console) sit there
    unnoticed forever, since the script would keep reporting the flow as
    already-correct based on whichever one happens to come first.
    """
    return [e for e in execs if e.get("providerId") == PASSWORDLESS_PROVIDER_ID]


def plan_passwordless_flow(client: httpx.Client, token: str, realm: dict) -> dict:
    """Inspect live state and describe exactly what needs to happen.

    Returns a dict describing each needed step so `main` can print a plan
    before mutating anything, and `apply_passwordless_flow` can re-derive the
    same facts (both call sites hit the live API fresh -- state can't have
    silently drifted between planning and applying within one run).
    """
    flows = get_flows(client, token)
    flow = next((f for f in flows if f["alias"] == PASSWORDLESS_FLOW_ALIAS), None)

    needs_copy = flow is None
    needs_execution = True
    needs_alternative = True
    duplicate_execution_ids = []
    if flow is not None:
        execs = get_executions(client, token, PASSWORDLESS_FLOW_ALIAS)
        matches = _find_webauthn_executions(execs)
        if matches:
            needs_execution = False
            needs_alternative = matches[0].get("requirement") != "ALTERNATIVE"
            if len(matches) > 1:
                duplicate_execution_ids = [m["id"] for m in matches[1:]]

    needs_bind = realm.get("browserFlow") != PASSWORDLESS_FLOW_ALIAS

    return {
        "needs_copy": needs_copy,
        "needs_execution": needs_execution,
        "needs_alternative": needs_alternative,
        "needs_bind": needs_bind,
        "current_browser_flow": realm.get("browserFlow"),
        "duplicate_execution_ids": duplicate_execution_ids,
    }


def apply_passwordless_flow(client: httpx.Client, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}

    flows = get_flows(client, token)
    flow = next((f for f in flows if f["alias"] == PASSWORDLESS_FLOW_ALIAS), None)

    if flow is None:
        resp = client.post(
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/authentication/flows/browser/copy",
            headers=headers,
            json={"newName": PASSWORDLESS_FLOW_ALIAS},
        )
        resp.raise_for_status()

    execs = get_executions(client, token, PASSWORDLESS_FLOW_ALIAS)
    matches = _find_webauthn_executions(execs)
    if len(matches) > 1:
        print(
            f"  WARNING: {len(matches)} '{PASSWORDLESS_PROVIDER_ID}' executions found in "
            f"'{PASSWORDLESS_FLOW_ALIAS}' (ids: {[m['id'] for m in matches]}) -- this script only "
            f"manages the first and will not delete the rest. Someone likely added the step twice "
            f"by hand; remove the extra one(s) in the admin console if it matters.",
        )
    existing = matches[0] if matches else None

    if existing is None:
        resp = client.post(
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/authentication/flows/{PASSWORDLESS_FLOW_ALIAS}/executions/execution",
            headers=headers,
            json={"provider": PASSWORDLESS_PROVIDER_ID},
        )
        resp.raise_for_status()
        execs = get_executions(client, token, PASSWORDLESS_FLOW_ALIAS)
        existing = _find_webauthn_executions(execs)[0]

    if existing.get("requirement") != "ALTERNATIVE":
        put_body = dict(existing)
        put_body["requirement"] = "ALTERNATIVE"
        resp = client.put(
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/authentication/flows/{PASSWORDLESS_FLOW_ALIAS}/executions",
            headers=headers,
            json=put_body,
        )
        resp.raise_for_status()

    realm = get_realm(client, token)
    if realm.get("browserFlow") != PASSWORDLESS_FLOW_ALIAS:
        realm["browserFlow"] = PASSWORDLESS_FLOW_ALIAS
        resp = client.put(
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}",
            headers=headers,
            json=realm,
        )
        resp.raise_for_status()


def plan_required_action(client: httpx.Client, token: str, prompt_new_users: bool) -> dict:
    resp = client.get(
        f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/authentication/required-actions",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    actions = resp.json()
    action = next(
        (a for a in actions if a.get("alias") == PASSWORDLESS_REQUIRED_ACTION), None
    )
    if action is None:
        return {"registered": False, "needs_enable": True, "needs_default": prompt_new_users}
    return {
        "registered": True,
        "needs_enable": not action.get("enabled", False),
        "needs_default": prompt_new_users and not action.get("defaultAction", False),
    }


def apply_required_action(client: httpx.Client, token: str, prompt_new_users: bool) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get(
        f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/authentication/required-actions",
        headers=headers,
    )
    resp.raise_for_status()
    actions = resp.json()
    action = next(
        (a for a in actions if a.get("alias") == PASSWORDLESS_REQUIRED_ACTION), None
    )
    if action is None:
        raise RuntimeError(
            f"Required action '{PASSWORDLESS_REQUIRED_ACTION}' is not registered on "
            f"this realm -- this Keycloak instance may predate 26.x WebAuthn "
            f"passwordless support. Aborting rather than guessing at a fix."
        )

    changed = False
    if not action.get("enabled", False):
        action["enabled"] = True
        changed = True
    if prompt_new_users and not action.get("defaultAction", False):
        action["defaultAction"] = True
        changed = True

    if changed:
        resp = client.put(
            f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/authentication/required-actions/{PASSWORDLESS_REQUIRED_ACTION}",
            headers=headers,
            json=action,
        )
        resp.raise_for_status()


def verify_password_grant(client: httpx.Client, username: str, password: str) -> bool:
    resp = client.post(
        f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token",
        data={
            "client_id": "frontend",
            "grant_type": "password",
            "username": username,
            "password": password,
        },
    )
    return resp.status_code == 200


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually apply changes (default: dry run / plan only)")
    parser.add_argument("--prompt-new-users", action="store_true", help="Set webauthn-register-passwordless as a default required action for new users")
    parser.add_argument("--verify-user", help="Email of a known-good user to regression-test password grant against, after applying")
    args = parser.parse_args()

    verify_password = None
    if args.verify_user:
        verify_password = os.getenv("WEBAUTHN_VERIFY_PASSWORD")
        if not verify_password:
            # Deliberately no --verify-password flag: a CLI arg lands in shell
            # history and is visible to other local users via `ps` for as long
            # as this process runs. Prompt instead, or set the env var for
            # non-interactive use (CI).
            try:
                verify_password = getpass.getpass(f"Password for {args.verify_user} (regression check, leave blank to skip): ") or None
            except (EOFError, KeyboardInterrupt):
                verify_password = None

    print(f"Keycloak: {KEYCLOAK_URL}   Realm: {KEYCLOAK_REALM}   RP ID: {WEBAUTHN_RP_ID}\n")

    with httpx.Client(timeout=10) as client:
        try:
            token = admin_token(client)
        except httpx.HTTPStatusError as exc:
            print(f"FAILED to obtain admin token: {exc}", file=sys.stderr)
            return 1

        realm = get_realm(client, token)

        policy_diff = plan_webauthn_policy(realm)
        flow_plan = plan_passwordless_flow(client, token, realm)
        action_plan = plan_required_action(client, token, args.prompt_new_users)

        print("Plan:")
        if policy_diff:
            print(f"  [webauthn policy] update {len(policy_diff)} field(s):")
            for k, v in policy_diff.items():
                print(f"      {k}: {realm.get(k)!r} -> {v!r}")
        else:
            print("  [webauthn policy] already matches spec -- no-op")

        if not any([flow_plan["needs_copy"], flow_plan["needs_execution"], flow_plan["needs_alternative"], flow_plan["needs_bind"]]):
            print(f"  [browser flow]    '{PASSWORDLESS_FLOW_ALIAS}' already exists, configured, and bound -- no-op")
        else:
            print(f"  [browser flow]    current browserFlow: {flow_plan['current_browser_flow']!r}")
            if flow_plan["needs_copy"]:
                print(f"      copy 'browser' -> '{PASSWORDLESS_FLOW_ALIAS}'")
            if flow_plan["needs_execution"]:
                print(f"      add execution '{PASSWORDLESS_PROVIDER_ID}'")
            if flow_plan["needs_alternative"]:
                print(f"      set its requirement to ALTERNATIVE")
            if flow_plan["needs_bind"]:
                print(f"      bind browserFlow -> '{PASSWORDLESS_FLOW_ALIAS}'")
        if flow_plan["duplicate_execution_ids"]:
            print(
                f"      WARNING: {len(flow_plan['duplicate_execution_ids'])} extra duplicate "
                f"'{PASSWORDLESS_PROVIDER_ID}' execution(s) found (ids: {flow_plan['duplicate_execution_ids']}) "
                f"-- not managed by this script, remove by hand if unwanted"
            )

        if not action_plan["registered"]:
            print(f"  [required action] '{PASSWORDLESS_REQUIRED_ACTION}' NOT REGISTERED on this realm -- will abort on apply")
        elif not action_plan["needs_enable"] and not action_plan["needs_default"]:
            print(f"  [required action] '{PASSWORDLESS_REQUIRED_ACTION}' already enabled" + (" and default" if args.prompt_new_users else "") + " -- no-op")
        else:
            if action_plan["needs_enable"]:
                print(f"      enable '{PASSWORDLESS_REQUIRED_ACTION}'")
            if action_plan["needs_default"]:
                print(f"      set '{PASSWORDLESS_REQUIRED_ACTION}' as a default action (prompt new users)")

        if not args.apply:
            print("\nDRY RUN -- nothing changed. Re-run with --apply to write these changes.")
            return 0

        print("\nApplying...")
        if policy_diff:
            apply_webauthn_policy(client, token, realm, policy_diff)
            print("  [webauthn policy] applied")
        apply_passwordless_flow(client, token)
        print("  [browser flow] applied")
        try:
            apply_required_action(client, token, args.prompt_new_users)
            print("  [required action] applied")
        except RuntimeError as exc:
            print(f"  [required action] FAILED: {exc}", file=sys.stderr)
            return 1

        realm_after = get_realm(client, token)
        print(f"\nVerification: browserFlow = {realm_after['browserFlow']!r}")
        print(f"              webAuthnPolicyPasswordlessRpEntityName = {realm_after['webAuthnPolicyPasswordlessRpEntityName']!r}")
        print(f"              webAuthnPolicyPasswordlessRpId = {realm_after['webAuthnPolicyPasswordlessRpId']!r}")

        if args.verify_user and verify_password:
            ok = verify_password_grant(client, args.verify_user, verify_password)
            print(f"\nRegression check: password grant for {args.verify_user} -> {'OK' if ok else 'FAILED'}")
            if not ok:
                print("  This flow (direct grant) is separate from the browser flow this script", file=sys.stderr)
                print("  changed, so a failure here means something else is wrong -- investigate", file=sys.stderr)
                print("  before assuming this script broke it.", file=sys.stderr)
                return 1
        else:
            print("\nNo --verify-user given (or no password supplied) -- skipped the password-grant regression check.")
            print("Recommended: re-run with those flags, or confirm manually.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
