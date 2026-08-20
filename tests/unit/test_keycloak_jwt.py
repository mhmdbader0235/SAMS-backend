"""
Unit tests for Keycloak JWT verification (app.core.keycloak_jwt).

Runs fully offline: builds an in-memory RSA keypair and a fake JWKS
response, so no network calls and no live Keycloak instance are needed.
Each test gets its own KeycloakVerifier + PyJWKClient (fetch_data
monkeypatched) so key material never leaks between tests.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClient
from jwt.algorithms import RSAAlgorithm

from app.core.keycloak_jwt import KeycloakTokenError, KeycloakVerifier

ISSUER = "https://kc.test/realms/SAMS"
AUDIENCE = ["apisix"]
KID = "test-kid"


def _rsa_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks_client_for(private_key, kid=KID):
    """A PyJWKClient whose HTTP fetch is replaced with a static JWKS."""
    jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk["kid"] = kid
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    client = PyJWKClient("https://kc.test/realms/SAMS/protocol/openid-connect/certs")
    client.fetch_data = lambda: {"keys": [jwk]}
    return client


def _make_token(private_key, kid=KID, **claim_overrides):
    now = int(time.time())
    payload = {
        "sub": "user-1",
        "email": "teacher@example.com",
        "iss": ISSUER,
        "aud": "apisix",
        "iat": now,
        "exp": now + 300,
        **claim_overrides,
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def signed():
    """(private_key, verifier) — verifier trusts only this key's JWKS entry."""
    private_key = _rsa_keypair()
    verifier = KeycloakVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_client=_jwks_client_for(private_key),
    )
    return private_key, verifier


class TestKeycloakTokenVerification:
    async def test_validly_signed_token_is_accepted(self, signed):
        private_key, verifier = signed
        token = _make_token(private_key)

        payload = await verifier.verify(token)

        assert payload["sub"] == "user-1"
        assert payload["email"] == "teacher@example.com"

    async def test_token_signed_with_wrong_key_is_rejected(self, signed):
        _, verifier = signed
        wrong_key = _rsa_keypair()
        # Same kid as the trusted JWKS entry, but signed with different key
        # material — this is the actual signature-forgery scenario.
        forged = _make_token(wrong_key, kid=KID)

        with pytest.raises(KeycloakTokenError):
            await verifier.verify(forged)

    async def test_expired_token_is_rejected(self, signed):
        private_key, verifier = signed
        token = _make_token(private_key, exp=int(time.time()) - 60)

        with pytest.raises(KeycloakTokenError):
            await verifier.verify(token)

    async def test_mismatched_issuer_is_rejected(self, signed):
        private_key, verifier = signed
        token = _make_token(private_key, iss="https://evil.example/realms/other")

        with pytest.raises(KeycloakTokenError):
            await verifier.verify(token)

    async def test_mismatched_audience_is_rejected(self, signed):
        private_key, verifier = signed
        token = _make_token(private_key, aud="some-other-client")

        with pytest.raises(KeycloakTokenError):
            await verifier.verify(token)

    async def test_unsigned_verify_signature_false_style_token_is_rejected(self, signed):
        """Regression guard for the bug being fixed: a token that only an
        unverified `verify_signature: False` decode would have accepted."""
        _, verifier = signed
        # No real signature at all — encoded with a key the verifier never saw.
        bogus = jwt.encode(
            {
                "sub": "attacker",
                "email": "attacker@example.com",
                "iss": ISSUER,
                "aud": "apisix",
                "exp": int(time.time()) + 300,
            },
            "not-the-real-key",
            algorithm="HS256",
        )

        with pytest.raises(KeycloakTokenError):
            await verifier.verify(bogus)
