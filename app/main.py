"""
FastAPI application entry point.

Uses lifespan context manager for startup/shutdown lifecycle events.
Registers all API routers (Auth, Events, Students, Analytics).
"""

import logging
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.dependencies import CurrentUser, get_current_user
from app.core.database import db_manager, get_control_plane_pool
from app.core.errors import AppError
from app.core.keycloak_jwt import start_jwks_refresh_loop, stop_jwks_refresh_loop
from app.core.observability import CorrelationIdMiddleware, configure_logging, get_correlation_id
from app.domains.analytics.router import router as analytics_router
from app.domains.audit.router import router as audit_router
from app.domains.auth.router import router as auth_router
from app.domains.auth.router import router_gated as auth_gated_router
from app.domains.events.router import router as events_router
from app.domains.family.router import router as family_router
from app.domains.invitations.router import router as invitation_router
from app.domains.notifications.router import router as notifications_router
from app.domains.school.router import router as school_router
from app.domains.students.router import (
    router as students_router,
)
from app.domains.students.router import (
    router_gated as students_gated_router,
)

configure_logging()
logger = logging.getLogger("app.main")


async def event_reminders_scheduler():
    """Background task to poll and send event reminders every 10 seconds."""
    import asyncio

    from app.domains.tenant.service import TenantService

    logger.info("Event Reminders Scheduler loop started.")
    while True:
        try:
            await TenantService.check_and_send_reminders()
        except Exception:
            logger.exception("Error in check_and_send_reminders")
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(application: FastAPI):  # noqa: ARG001
    """Startup / shutdown lifecycle hook."""
    import asyncio

    logger.info("SAMS backend initialised — multi-tenant mode active.")
    try:
        # Initialize Control-Plane DB and seed default tenants
        await get_control_plane_pool()
        logger.info("Control-Plane database connected and initialized.")
        from app.core.keycloak_admin import ensure_keycloak_frontend_redirect_uris

        ensure_keycloak_frontend_redirect_uris()

        # Every tenant needs a Keycloak Organization aliased to its tenant_id --
        # that alias is what the `organization` claim carries, and the claim is how
        # a request resolves to a tenant. Organizations cannot live in
        # SAMS-realm.json (realm export omits them entirely), so this reconcile is
        # the provisioning path for tenants that predate the change, or whose
        # organization was removed. Best-effort: it must not stop the API booting.
        from app.core.keycloak_admin import ensure_keycloak_organizations
        from app.domains.tenant.control_plane_repository import ControlPlaneRepository

        cp_pool = await get_control_plane_pool()
        tenant_rows = await ControlPlaneRepository(cp_pool).get_all_tenants()
        tenant_ids = [t.get("tenant_id") for t in tenant_rows if t.get("tenant_id")]
        org_summary = ensure_keycloak_organizations(tenant_ids)
        logger.info(
            "Keycloak organizations: %d existing, %d created, %d failed.",
            len(org_summary["existing"]),
            len(org_summary["created"]),
            len(org_summary["failed"]),
        )
    except Exception:
        logger.exception("Warning: could not initialize Control-Plane DB")

    await start_jwks_refresh_loop()
    logger.info("Keycloak JWKS fetch/refresh loop started.")

    # event_reminders_scheduler() is disabled: TenantService.check_and_send_reminders()
    # is an unimplemented no-op, so running the loop was just polling every 10
    # seconds for nothing. Re-enable this once reminder-sending is actually
    # implemented (see app/domains/tenant/service.py::check_and_send_reminders).
    scheduler_task = None

    yield

    if scheduler_task is not None:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task

    await stop_jwks_refresh_loop()
    await db_manager.disconnect_all()
    logger.info("All tenant/control-plane connection pools closed.")


app = FastAPI(
    title="SAMS Backend API",
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

# ─── Request correlation / logging ──────────────────────────────────────────
app.add_middleware(CorrelationIdMiddleware)


# ─── Error taxonomy ──────────────────────────────────────────────────────────
# AppError subclasses carry a status+message that is safe to show a client.
# Anything else (a bare, unexpected exception) must never leak its message --
# stack traces, SQL fragments, file paths -- to the caller; log it in full
# server-side and return a generic 500 with only the correlation id, so a
# support conversation can reference one concrete request without exposing
# internals.
@app.exception_handler(AppError)
async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "correlation_id": get_correlation_id()},
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:  # noqa: ARG001
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal error", "correlation_id": get_correlation_id()},
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
app.include_router(audit_router)
app.include_router(family_router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Simple liveness probe for Docker health checks."""
    return {"status": "ok"}


@app.get("/ready", tags=["health"])
async def ready() -> JSONResponse:
    """Readiness probe -- unlike /health (which the Docker healthcheck
    depends on and must stay a fixed, dependency-free response), this
    actually exercises the control-plane database, Keycloak's JWKS endpoint,
    and OPA, so a caller can tell "the process is up" from "the process can
    actually serve a real request" -- returns 200 only if every dependency
    answered."""
    import asyncio

    import httpx

    from app.core.keycloak_jwt import KEYCLOAK_JWKS_URL

    async def _check_database() -> bool:
        try:
            pool = await asyncio.wait_for(get_control_plane_pool(), timeout=2)
            await asyncio.wait_for(pool.fetchval("SELECT 1"), timeout=2)
            return True
        except Exception:
            return False

    async def _check_keycloak() -> bool:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                resp = await client.get(KEYCLOAK_JWKS_URL)
                return resp.status_code == 200
        except Exception:
            return False

    async def _check_opa() -> bool:
        try:
            from app.core.config import OPA_URL

            opa_base = OPA_URL.split("/v1/")[0]
            async with httpx.AsyncClient(timeout=2) as client:
                resp = await client.get(f"{opa_base}/health")
                return resp.status_code == 200
        except Exception:
            return False

    database_ok, keycloak_ok, opa_ok = await asyncio.gather(
        _check_database(), _check_keycloak(), _check_opa()
    )
    body = {"database": database_ok, "keycloak": keycloak_ok, "opa": opa_ok}
    return JSONResponse(status_code=200 if all(body.values()) else 503, content=body)


@app.get("/api/v1/health/schema-status", tags=["health"])
async def schema_status(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Check migration status across control plane and all tenant schemas.
    Restricted strictly to super_admin."""
    if not current_user.has_role("super_admin"):
        raise HTTPException(status_code=403, detail="Forbidden: super_admin role required")

    from app.core.database import get_control_plane_pool, get_db_pool
    from app.domains.tenant.control_plane_repository import ControlPlaneRepository

    cp_pool = await get_control_plane_pool()
    cp_repo = ControlPlaneRepository(cp_pool)

    # Check control-plane alembic_version
    cp_rev = None
    try:
        cp_rev = await cp_pool.fetchval("SELECT version_num FROM public.alembic_version LIMIT 1")
    except Exception as exc:
        cp_rev = f"error: {exc}"

    # Query all registered tenants
    tenant_statuses = []
    all_healthy = True
    try:
        tenants = await cp_repo.get_all_tenants()
        for t in tenants:
            tid = t.get("tenant_id") or t.get("id")
            if not tid:
                continue
            try:
                t_pool = await get_db_pool(tid)
                t_rev = await t_pool.fetchval(
                    f'SELECT version_num FROM "{tid}".alembic_version LIMIT 1'
                )
                tenant_statuses.append({
                    "tenant_id": tid,
                    "current_revision": t_rev,
                    "status": "ok" if t_rev else "unmigrated",
                })
            except Exception as exc:
                all_healthy = False
                tenant_statuses.append({
                    "tenant_id": tid,
                    "current_revision": None,
                    "status": f"error: {exc}",
                })
    except Exception:
        all_healthy = False

    return {
        "control_plane": {
            "current_revision": cp_rev,
            "status": "ok" if cp_rev and not str(cp_rev).startswith("error") else "error",
        },
        "tenants": tenant_statuses,
        "all_healthy": all_healthy and bool(cp_rev) and not str(cp_rev).startswith("error"),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)

# Hot reload trigger
