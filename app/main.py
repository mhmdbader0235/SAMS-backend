"""
FastAPI application entry point.

Uses lifespan context manager for startup/shutdown lifecycle events.
Registers all API routers (Auth, Events, Students, Analytics).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import db_manager, get_control_plane_pool
from .routers.analytics import router as analytics_router
from .routers.auth import router as auth_router
from .routers.events import router as events_router
from .routers.notes import router as notes_router
from .routers.students import router as students_router
from .routers.notifications import router as notifications_router


async def event_reminders_scheduler():
    """Background task to poll and send event reminders every 10 seconds."""
    import asyncio
    from .services.tenant_service import TenantService
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
    except Exception as exc:
        print(f"[startup] Warning: could not initialize Control-Plane DB: {exc}")
    
    # Start the event reminders scheduler in the background
    scheduler_task = asyncio.create_task(event_reminders_scheduler())
    
    yield
    
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
        
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
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ─────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(events_router)
app.include_router(students_router)
app.include_router(analytics_router)
app.include_router(notes_router)
app.include_router(notifications_router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Simple liveness probe for Docker health checks."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
