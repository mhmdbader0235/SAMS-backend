"""
Unit tests for AuthService.

No database, no FastAPI — pure Python logic only.
All assertions test the AuthService methods in isolation.
"""

import time
from uuid import uuid4

from app.domains.auth.service import AuthService


class TestPasswordHashing:
    def test_hash_returns_non_empty_string(self):
        hashed = AuthService.hash_password("my-secret-password")
        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_hash_does_not_equal_plaintext(self):
        plain = "my-secret-password"
        assert AuthService.hash_password(plain) != plain

    def test_verify_correct_password_returns_true(self):
        plain = "correct-horse-battery-staple"
        hashed = AuthService.hash_password(plain)
        assert AuthService.verify_password(plain, hashed) is True

    def test_verify_wrong_password_returns_false(self):
        hashed = AuthService.hash_password("correct-password")
        assert AuthService.verify_password("wrong-password", hashed) is False

    def test_two_hashes_of_same_password_differ(self):
        """Bcrypt uses random salts — identical inputs must produce different hashes."""
        plain = "same-password"
        hash1 = AuthService.hash_password(plain)
        hash2 = AuthService.hash_password(plain)
        assert hash1 != hash2
        # But both must verify correctly
        assert AuthService.verify_password(plain, hash1)
        assert AuthService.verify_password(plain, hash2)


class TestJWTTokens:
    def test_create_token_returns_string(self):
        token = AuthService.create_access_token(uuid4(), "tenant_a", "teacher")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_valid_token_returns_payload(self):
        user_id = uuid4()
        token = AuthService.create_access_token(user_id, "tenant_b", "parent")
        payload = AuthService.decode_access_token(token)

        assert payload is not None
        assert payload["sub"] == str(user_id)
        assert payload["tenant_id"] == "tenant_b"
        assert payload["role"] == "parent"

    def test_decode_invalid_token_returns_none(self):
        result = AuthService.decode_access_token("this.is.not.a.jwt")
        assert result is None

    def test_decode_tampered_token_returns_none(self):
        token = AuthService.create_access_token(uuid4(), "tenant_a", "teacher")
        tampered = token[:-5] + "XXXXX"
        assert AuthService.decode_access_token(tampered) is None

    def test_token_contains_expiry(self):
        token = AuthService.create_access_token(uuid4(), "tenant_a", "teacher")
        payload = AuthService.decode_access_token(token)
        assert "exp" in payload
        assert payload["exp"] > int(time.time())

    def test_tokens_for_different_users_differ(self):
        token_a = AuthService.create_access_token(uuid4(), "tenant_a", "teacher")
        token_b = AuthService.create_access_token(uuid4(), "tenant_a", "teacher")
        assert token_a != token_b
