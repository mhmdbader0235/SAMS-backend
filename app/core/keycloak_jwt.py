"""
Keycloak JWT verification.

Fetches and caches Keycloak's JWKS (public signing keys) for the SAMS realm,
then verifies every Keycloak-issued access token's signature, expiry,
issuer, and audience against it. There is intentionally no fallback path:
any verification failure raises KeycloakTokenError, and the caller
(app.core.dependencies.get_current_user) must treat that as an
unauthenticated request (401) rather than trying another decoding strategy.

This module only performs authentication (proving the token is a genuine,
unexpired Keycloak token for this realm). It deliberately does not extract
role/authorization claims — see get_current_user's comment on why Keycloak
role claims are discarded in favor of the local `users` table.
"""

import asyncio
import contextlib
import logging
import os

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from app.core.keycloak_admin import KEYCLOAK_REALM, KEYCLOAK_URL

logger = logging.getLogger(__name__)

# Derived from KEYCLOAK_URL/KEYCLOAK_REALM by default. Override KEYCLOAK_ISSUER
# directly if the backend's KEYCLOAK_URL (used for server-to-server JWKS
# fetches) differs from the public hostname Keycloak stamps into `iss` when
# issuing tokens to the frontend (e.g. behind a different reverse-proxy
# hostname in production) — confirm against a real token before deploying.
KEYCLOAK_ISSUER: str = os.getenv("KEYCLOAK_ISSUER", f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}")
KEYCLOAK_JWKS_URL: str = os.getenv(
    "KEYCLOAK_JWKS_URL", f"{KEYCLOAK_ISSUER}/protocol/openid-connect/certs"
)

# Acceptable `aud` values for end-user access tokens. Defaults to Keycloak's
# built-in "account" audience plus both realm clients (frontend, apisix),
# since which one actually ends up in `aud` has not been confirmed against a
# live token yet (SAMS-realm.json defines no audience mapper, so Keycloak's
# default resolution applies). Narrow this to the single real value — via
# KEYCLOAK_AUDIENCE — once confirmed against the live Keycloak instance.
KEYCLOAK_AUDIENCE: list[str] = [
    a.strip()
    for a in os.getenv("KEYCLOAK_AUDIENCE", "account,apisix,frontend").split(",")
    if a.strip()
]

KEYCLOAK_JWKS_REFRESH_SECONDS: int = int(os.getenv("KEYCLOAK_JWKS_REFRESH_SECONDS", "300"))


class KeycloakTokenError(Exception):
    """Raised for any Keycloak token verification failure (bad signature,
    expired, wrong issuer/audience, or an unreachable JWKS endpoint).
    Never includes the raw token in its message."""


class KeycloakVerifier:
    """Verifies Keycloak-issued JWTs against a cached JWKS.

    Instantiated once as the module-level default below; tests construct
    their own instances (with an injected `jwks_client`) for isolation.
    """

    def __init__(
        self,
        jwks_url: str | None = None,
        issuer: str | None = None,
        audience: list[str] | None = None,
        jwks_refresh_seconds: int = 300,
        jwks_client: PyJWKClient | None = None,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.jwks_refresh_seconds = jwks_refresh_seconds
        self._jwks_client = jwks_client or PyJWKClient(
            jwks_url, cache_keys=True, lifespan=jwks_refresh_seconds
        )
        self._refresh_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Fetch the JWKS once, then start the periodic background refresh.

        Keycloak rotates its signing keys; PyJWKClient already re-fetches
        on a cache-miss (unknown `kid`), but this loop refreshes the cache
        proactively so key rotation doesn't rely on that fallback alone.
        A failed initial fetch (e.g. Keycloak not up yet) is logged, not
        raised — the app still starts and every Keycloak-token verification
        will keep retrying the fetch until it succeeds.
        """
        try:
            await asyncio.to_thread(self._jwks_client.get_signing_keys)
            logger.info("Keycloak JWKS fetched from %s", self._jwks_client.uri)
        except Exception as exc:
            logger.warning(
                "Initial Keycloak JWKS fetch from %s failed (will keep retrying): %s",
                self._jwks_client.uri,
                exc,
            )
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.jwks_refresh_seconds)
            try:
                await asyncio.to_thread(self._jwks_client.get_signing_keys, True)
                logger.debug("Keycloak JWKS refreshed from %s", self._jwks_client.uri)
            except Exception as exc:
                logger.warning(
                    "Keycloak JWKS refresh from %s failed, keeping previous cache: %s",
                    self._jwks_client.uri,
                    exc,
                )

    async def verify(self, token: str) -> dict:
        """Verify signature, exp, iss, and aud. Raises KeycloakTokenError on any failure."""

        def _verify() -> dict:
            try:
                signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            except PyJWKClientError as exc:
                raise KeycloakTokenError(f"Unable to resolve signing key: {exc}") from exc

            try:
                return jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=self.audience,
                    issuer=self.issuer,
                    options={"require": ["exp", "iss", "aud"]},
                )
            except jwt.PyJWTError as exc:
                raise KeycloakTokenError(str(exc)) from exc

        return await asyncio.to_thread(_verify)


_default_verifier = KeycloakVerifier(
    jwks_url=KEYCLOAK_JWKS_URL,
    issuer=KEYCLOAK_ISSUER,
    audience=KEYCLOAK_AUDIENCE,
    jwks_refresh_seconds=KEYCLOAK_JWKS_REFRESH_SECONDS,
)


async def start_jwks_refresh_loop() -> None:
    """Called from the FastAPI lifespan startup hook."""
    await _default_verifier.start()


async def stop_jwks_refresh_loop() -> None:
    """Called from the FastAPI lifespan shutdown hook."""
    await _default_verifier.stop()


async def verify_keycloak_token(token: str) -> dict:
    """Verify a Keycloak-issued access token. Raises KeycloakTokenError on failure."""
    return await _default_verifier.verify(token)
