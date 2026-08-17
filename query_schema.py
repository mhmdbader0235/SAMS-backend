import asyncio
import asyncpg

async def run():
    conn = await asyncpg.connect('postgresql://admin:secure_local_password@127.0.0.1:5433/tenant_a_db')
    rows = await conn.fetch("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'event'")
    for r in rows:
        print(dict(r))
    await conn.close()

asyncio.run(run())
