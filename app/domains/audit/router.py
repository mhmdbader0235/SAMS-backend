"""Audit log query surface — read-only. Writes only ever happen from inside
another domain's own transaction via AuditService.record(); there is no
POST/PUT/DELETE here on purpose (see repository.py's immutability note)."""

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.dependencies import CurrentUser, require_permission
from app.core.schemas import AuditLogEntryResponse, AuditLogListResponse
from app.domains.audit.service import AuditService

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=AuditLogListResponse, summary="Query the audit log")
async def query_audit_log(
    current_user: CurrentUser = Depends(require_permission("audit:view")),
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_user_id: int | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    before_id: int | None = None,
    limit: int = Query(default=50, le=200),
) -> AuditLogListResponse:
    entries = await AuditService.query(
        current_user.tenant_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        before_id=before_id,
        limit=limit,
    )
    next_before_id = entries[-1]["id"] if len(entries) == limit else None
    return AuditLogListResponse(
        items=[AuditLogEntryResponse(**e) for e in entries],
        next_before_id=next_before_id,
    )


@router.get("/export.csv", summary="Export the audit log as CSV")
async def export_audit_log_csv(
    current_user: CurrentUser = Depends(require_permission("audit:view")),
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_user_id: int | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: int = Query(default=200, le=200),
) -> StreamingResponse:
    entries = await AuditService.query(
        current_user.tenant_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        limit=limit,
    )
    buffer = io.StringIO()
    fieldnames = [
        "id",
        "occurred_at",
        "correlation_id",
        "actor_user_id",
        "actor_role",
        "action",
        "entity_type",
        "entity_id",
        "outcome",
        "changed_fields",
        "metadata",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for entry in entries:
        writer.writerow(entry)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )
