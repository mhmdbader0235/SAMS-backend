"""Audit business rules: computing what's safe to store, never what to leak.

The no-PII contract is enforced HERE, not by the schema (a JSONB column
cannot stop a caller from putting an email in it) -- record() only accepts
`metadata` keys from a per-action allowlist, and strips (does not persist)
anything else. See tests/unit/test_audit_no_pii.py.
"""

import hashlib
import hmac as hmac_module
from datetime import UTC, datetime, timedelta

import asyncpg

from app.core.config import ENCRYPTION_KEY
from app.domains.audit.repository import AuditRepository

# 24 months -- health-record access and permission changes may warrant longer
# in a future pass; keep this a single constant rather than hardcoding inline
# so that decision has one place to change.
RETENTION_DAYS = 730

# Per-action allowlist of metadata keys. A key not listed here for the given
# action is silently dropped, not stored -- metadata is for non-PII scalars
# (counts, statuses, ids) that help investigate an entry, never names/emails/
# DOB/free-text user input.
_METADATA_ALLOWLIST: dict[str, set[str]] = {
    "student.health.read": set(),
    "student.health.write": {"fields_written"},
    "user.permissions.update": {"new_primary_role", "role_count", "permission_count"},
    "user.delete": {"deleted_role", "was_super_admin"},
    "class.delete": {"had_enrollment_history"},
    "level.delete": {"class_count"},
    "payment.state_change": {"from_state", "to_state"},
    "resource.cost.update": set(),
    "ticket_price.update": set(),
    "subsidy.update": set(),
    "rollover.commit": {"line_count", "from_year_id", "to_year_id"},
    "tenant_override.cross_tenant_access": {"target_tenant_id"},
}


def _actor_email_hmac(email: str | None) -> str | None:
    if not email:
        return None
    return hmac_module.new(
        ENCRYPTION_KEY.encode(), email.strip().lower().encode(), hashlib.sha256
    ).hexdigest()


def _sanitize_metadata(action: str, metadata: dict | None) -> dict:
    if not metadata:
        return {}
    allowed = _METADATA_ALLOWLIST.get(action, set())
    return {k: v for k, v in metadata.items() if k in allowed}


class AuditService:
    @staticmethod
    async def record(
        conn: asyncpg.Connection,
        *,
        actor,
        action: str,
        entity_type: str,
        entity_id: str | int | None,
        outcome: str = "allow",
        changed_fields: list[str] | None = None,
        metadata: dict | None = None,
        correlation_id: str | None = None,
    ) -> int:
        """`actor` is a CurrentUser (or None for a system/background action).
        `conn` must be the SAME connection the caller's own write is running
        on, so a rolled-back outer transaction rolls this back too."""
        occurred_at = datetime.now(UTC)
        retention_until = (occurred_at + timedelta(days=RETENTION_DAYS)).date()

        actor_user_id = None
        actor_role = "system"
        actor_email = None
        if actor is not None:
            actor_role = actor.role or "unknown"
            actor_email = getattr(actor, "email", None)
            raw_id = getattr(actor, "id", None)
            if raw_id is not None and str(raw_id).isdigit():
                actor_user_id = int(raw_id)

        return await AuditRepository.record(
            conn,
            correlation_id=correlation_id,
            actor_user_id=actor_user_id,
            actor_email_hmac=_actor_email_hmac(actor_email),
            actor_role=actor_role,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            outcome=outcome,
            changed_fields=changed_fields,
            metadata=_sanitize_metadata(action, metadata),
            retention_until=retention_until,
        )

    @staticmethod
    async def query(
        tenant_id: str,
        *,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        actor_user_id: int | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        before_id: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        from app.core.database import get_db_pool

        pool = await get_db_pool(tenant_id)
        repo = AuditRepository(pool)
        return await repo.list_entries(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            before_id=before_id,
            limit=min(limit, 200),
        )
