"""
Unit test for app.main's startup lifespan.

Regression test for: event_reminders_scheduler() used to be started
unconditionally on every app startup, polling every 10 seconds forever, even
though the reminder logic it calls (TenantService.check_and_send_reminders)
is an unimplemented no-op. It must stay disabled until that logic is
actually implemented, so it isn't silently burning a poll cycle for nothing.

Heavy startup dependencies (control-plane DB connect, Keycloak redirect URI
sync, JWKS refresh loop, pool teardown) are mocked out so this runs without
any real network or database access.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from app import main


async def test_event_reminders_scheduler_is_not_started_during_lifespan():
    with (
        patch.object(main, "get_control_plane_pool", new=AsyncMock(return_value=None)),
        patch.object(main, "start_jwks_refresh_loop", new=AsyncMock()),
        patch.object(main, "stop_jwks_refresh_loop", new=AsyncMock()),
        patch.object(main.db_manager, "disconnect_all", new=AsyncMock()),
        patch("app.core.keycloak_admin.ensure_keycloak_frontend_redirect_uris"),
        patch.object(main, "event_reminders_scheduler") as mock_scheduler,
    ):
        async with main.lifespan(main.app):
            # Give any task scheduled during startup a chance to actually run.
            await asyncio.sleep(0)

        mock_scheduler.assert_not_called()
