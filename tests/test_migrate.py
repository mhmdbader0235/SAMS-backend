import pytest
import asyncio
from app.core.database import db_manager

@pytest.mark.asyncio
async def test_migrate():
    cp_pool = await db_manager.get_control_plane_pool()
    tenants = await cp_pool.fetch("SELECT tenant_id FROM tenants")
    print(f"Found {len(tenants)} tenants to migrate.")
    for row in tenants:
        tenant_id = row["tenant_id"]
        pool = await db_manager.get_pool(tenant_id)
        
        # Move resource_planning to proposed
        res1 = await pool.execute("UPDATE event SET status = 'proposed' WHERE status = 'resource_planning'")
        print(f"[{tenant_id}] resource_planning -> proposed: {res1}")
        
        # Move finance_approval and final_review to approved
        res2 = await pool.execute("UPDATE event SET status = 'approved' WHERE status IN ('finance_approval', 'final_review')")
        print(f"[{tenant_id}] finance_approval/final_review -> approved: {res2}")
