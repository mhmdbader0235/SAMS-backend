"""
FastAPI application entry point.

Uses lifespan context manager for startup/shutdown lifecycle events.
Registers all API routers (Auth, Events, Students, Analytics).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import db_manager, get_control_plane_pool
from app.core.keycloak_jwt import start_jwks_refresh_loop, stop_jwks_refresh_loop
from app.domains.analytics.router import router as analytics_router
from app.domains.auth.router import router as auth_router, router_gated as auth_gated_router
from app.domains.events.router import router as events_router
from app.domains.notifications.router import router as notifications_router
from app.domains.school.router import router as school_router
from app.domains.students.router import router as students_router, router_gated as students_gated_router
from app.domains.invitations.router import router as invitation_router


async def event_reminders_scheduler():
    """Background task to poll and send event reminders every 10 seconds."""
    import asyncio

    from app.domains.tenant.service import TenantService
    print("[startup] Event Reminders Scheduler loop started.")
    while True:
        try:
            await TenantService.check_and_send_reminders()
        except Exception as exc:
            print(f"[Reminders Scheduler] Error in check_and_send_reminders: {exc}")
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(application: FastAPI):  # noqa: ARG001
    """Startup / shutdown lifecycle hook."""
    import asyncio
    print("[startup] SchoolDesk backend initialised — multi-tenant mode active.")
    try:
        # Initialize Control-Plane DB and seed default tenants
        await get_control_plane_pool()
        print("[startup] Control-Plane database connected and initialized.")
        from app.core.keycloak_admin import ensure_keycloak_frontend_redirect_uris
        ensure_keycloak_frontend_redirect_uris()
    except Exception as exc:
        print(f"[startup] Warning: could not initialize Control-Plane DB: {exc}")

    await start_jwks_refresh_loop()
    print("[startup] Keycloak JWKS fetch/refresh loop started.")

    # event_reminders_scheduler() is disabled: TenantService.check_and_send_reminders()
    # is an unimplemented no-op, so running the loop was just polling every 10
    # seconds for nothing. Re-enable this once reminder-sending is actually
    # implemented (see app/domains/tenant/service.py::check_and_send_reminders).
    scheduler_task = None

    yield

    if scheduler_task is not None:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass

    await stop_jwks_refresh_loop()
    await db_manager.disconnect_all()
    print("[shutdown] All tenant/control-plane connection pools closed.")


app = FastAPI(
    title="SchoolDesk Backend API",
    version="1.0.0",
    description="Multi-tenant school event and analytics management platform.",
    lifespan=lifespan,
)

# ─── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:9080",
        "http://127.0.0.1:9080",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Routers ─────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(auth_gated_router)
app.include_router(events_router)
app.include_router(students_router)
app.include_router(students_gated_router)
app.include_router(analytics_router)
app.include_router(notifications_router)
app.include_router(invitation_router)
app.include_router(school_router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Simple liveness probe for Docker health checks."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)

# Hot reload trigger
