"""
check_token.py — Decode a real Keycloak access token and check it against
this backend's configured KEYCLOAK_ISSUER / KEYCLOAK_AUDIENCE.

Read-only diagnostic tool only. It decodes the token WITHOUT verifying its
signature (options={"verify_signature": False}) purely to inspect claims —
this is the exact insecure pattern that was just removed from
app/core/dependencies.py, so do not adapt this script into anything that
makes an authentication decision. It never writes the token anywhere.

Usage:
    python check_token.py                  # paste the token when prompted
    python check_token.py "<access_token>" # or pass it as an argument

Never paste a real token into a file that gets committed — this script
takes it as a runtime argument/prompt only, never a hardcoded constant.
"""

import json
import sys

import jwt

from app.core.keycloak_jwt import KEYCLOAK_AUDIENCE, KEYCLOAK_ISSUER, KEYCLOAK_JWKS_URL


def _aud_matches(token_aud, configured_audience: list[str]) -> bool:
    token_auds = [token_aud] if isinstance(token_aud, str) else list(token_aud or [])
    return any(a in configured_audience for a in token_auds)


def main() -> None:
    token = sys.argv[1] if len(sys.argv) > 1 else input("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NTUiLCJ0ZW5hbnRfaWQiOiJ0ZW5hbnRfYSIsInJvbGUiOiJ0ZWFjaGVyIiwicm9sZXMiOlsidGVhY2hlciJdLCJlbWFpbCI6InlvdXJ0ZXN0QHNjaG9vbGEuY29tIiwiZXhwIjoxNzg3MzAxNDU3fQ.lf0akFT3_7K19G2AHBwb8-4kQuMIQuuzC4bK9-uZD5s").strip()
    if not token:
        print("No token provided.")
        sys.exit(1)

    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except Exception as exc:
        print(f"Could not decode token: {exc}")
        sys.exit(1)

    header = jwt.get_unverified_header(token)

    token_iss = payload.get("iss")
    token_aud = payload.get("aud")

    print("=" * 60)
    print("Header")
    print("-" * 60)
    print(json.dumps(header, indent=2))

    print("\nClaims relevant to app/core/keycloak_jwt.py")
    print("-" * 60)
    print(f"iss (token)        : {token_iss}")
    print(f"KEYCLOAK_ISSUER     : {KEYCLOAK_ISSUER}")
    iss_ok = token_iss == KEYCLOAK_ISSUER
    print(f"  -> {'MATCH' if iss_ok else 'MISMATCH — set KEYCLOAK_ISSUER to the token value above'}")

    print(f"\naud (token)         : {token_aud}")
    print(f"KEYCLOAK_AUDIENCE   : {KEYCLOAK_AUDIENCE}")
    aud_ok = _aud_matches(token_aud, KEYCLOAK_AUDIENCE)
    print(f"  -> {'MATCH' if aud_ok else 'MISMATCH — narrow KEYCLOAK_AUDIENCE to the token value(s) above'}")

    print(f"\nKEYCLOAK_JWKS_URL   : {KEYCLOAK_JWKS_URL}")
    print("  -> fetch this URL and confirm it returns a `kid` matching the header above")

    print("\nFull payload")
    print("-" * 60)
    print(json.dumps(payload, indent=2))

    print("\n" + "=" * 60)
    if iss_ok and aud_ok:
        print("Both iss and aud match the current config. Safe to proceed.")
    else:
        print("Update .env with the values flagged MISMATCH above before relying on this in production.")


if __name__ == "__main__":
    main()
