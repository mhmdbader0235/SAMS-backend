"""Regression test: an unhandled exception must never leak its message to
the client.

invariant: `school/router.py` and `students/router.py` used to catch every
exception with `except Exception as exc: raise HTTPException(500,
detail=str(exc))`, which puts the raw Python exception message -- stack
internals, SQL fragments, file paths -- directly in the HTTP response body.
That pattern is gone (module roadmap Wave A3.2); unexpected exceptions now
propagate to app.main's global `Exception` handler, which returns a fixed,
safe message plus a correlation id, and logs the real exception server-side.
"""

from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.integration._helpers import register_school_admin


async def test_unhandled_exception_in_school_router_does_not_leak_message(
    test_client: AsyncClient, db_pool, clean_db
):
    """Uses a locally-built AsyncClient with raise_app_exceptions=False,
    rather than the shared `test_client` fixture, because Starlette's
    ServerErrorMiddleware deliberately re-raises an unhandled exception AFTER
    sending the correct response (so it still reaches a production logger/
    Sentry) -- httpx's ASGITransport default (raise_app_exceptions=True)
    surfaces that re-raise as a test failure even though the response the
    client actually received was already correct. This does not indicate a
    bug in the app; it is the transport's default re-raise behavior."""
    token = await register_school_admin(test_client, "admin_leak_test@school.com")
    headers = {"Authorization": f"Bearer {token}"}

    secret_looking_message = "psycopg2.OperationalError: password authentication failed for user X"

    local_client = AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    )
    try:
        with patch(
            "app.domains.school.service.SchoolService.get_setup_state",
            new=AsyncMock(side_effect=RuntimeError(secret_looking_message)),
        ):
            resp = await local_client.get("/api/v1/school/setup-state", headers=headers)
    finally:
        await local_client.aclose()

    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Internal error"
    assert "correlation_id" in body
    assert secret_looking_message not in resp.text
    assert "RuntimeError" not in resp.text
