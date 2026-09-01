"""EventRepository — raw SQL for the small slice of event data that is not
already covered by TenantRepository (see events/service.py for why this
domain wraps TenantService/TenantRepository instead of replacing them).

STRICT RULE: the only layer in this domain permitted to execute raw SQL.
"""

import asyncpg


class EventRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def update_ticket_prices(self, event_id: int, items: list[dict]) -> None:
        for item in items:
            await self.pool.execute(
                "UPDATE event_class_map SET ticket_price = $1 WHERE id = $2 AND event_id = $3",
                item["ticket_price"],
                item["class_map_id"],
                event_id,
            )

    async def update_subsidy(self, event_id: int, subsidy: float) -> None:
        await self.pool.execute(
            "UPDATE event SET school_subsidy = $1 WHERE id = $2",
            subsidy,
            event_id,
        )
