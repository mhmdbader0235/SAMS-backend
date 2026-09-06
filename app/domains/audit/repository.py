"""Audit log repository — the only place that writes/reads `audit_log`.

`record()` deliberately takes an existing asyncpg connection rather than
`self.pool` — it must run on the SAME connection and transaction as the
write it is auditing, so a rolled-back mutation leaves no audit row behind
either (see tests/integration/test_audit_log.py). Every other method here
uses `self.pool` normally, since reads don't need that guarantee.

No repository method here issues UPDATE or DELETE against audit_log -- that
absence is the immutability guarantee at this layer (see
tests/integration/test_audit_immutable.py and ADR 0006 for why a real
REVOKE isn't in place yet).
"""

import json
from datetime import date, datetime

import asyncpg


class AuditRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @staticmethod
    async def record(
        conn: asyncpg.Connection,
        *,
        correlation_id: str | None,
        actor_user_id: int | None,
        actor_email_hmac: str | None,
        actor_role: str,
        action: str,
        entity_type: str,
        entity_id: str | None,
        outcome: str,
        changed_fields: list[str] | None,
        metadata: dict,
        retention_until: date,
    ) -> int:
        return await conn.fetchval(
            """
            INSERT INTO audit_log (
                correlation_id, actor_user_id, actor_email_hmac, actor_role,
                action, entity_type, entity_id, outcome, changed_fields,
                metadata, retention_until
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
            RETURNING id
            """,
            correlation_id,
            actor_user_id,
            actor_email_hmac,
            actor_role,
            action,
            entity_type,
            entity_id,
            outcome,
            changed_fields,
            json.dumps(metadata),
            retention_until,
        )

    async def list_entries(
        self,
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
        conditions = []
        params: list = []

        def _add(condition: str, value) -> None:
            params.append(value)
            conditions.append(condition.format(n=len(params)))

        if action is not None:
            _add("action = ${n}", action)
        if entity_type is not None:
            _add("entity_type = ${n}", entity_type)
        if entity_id is not None:
            _add("entity_id = ${n}", entity_id)
        if actor_user_id is not None:
            _add("actor_user_id = ${n}", actor_user_id)
        if occurred_from is not None:
            _add("occurred_at >= ${n}", occurred_from)
        if occurred_to is not None:
            _add("occurred_at <= ${n}", occurred_to)
        if before_id is not None:
            _add("id < ${n}", before_id)

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = await self.pool.fetch(
            f"""
            SELECT id, occurred_at, correlation_id, actor_user_id, actor_role,
                   action, entity_type, entity_id, outcome, changed_fields, metadata
            FROM audit_log
            {where_sql}
            ORDER BY id DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        results = []
        for r in rows:
            d = dict(r)
            if d.get("correlation_id") is not None:
                d["correlation_id"] = str(d["correlation_id"])
            d["metadata"] = json.loads(d["metadata"]) if d.get("metadata") else {}
            results.append(d)
        return results
