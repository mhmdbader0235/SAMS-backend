import asyncio
import asyncpg
import os
import sys

# Ensure app is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.keycloak_admin import sync_user_to_keycloak

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5433"))
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "secure_local_password")
DB_NAME = os.getenv("DB_NAME", "user_service_db")

async def main():
    print("Starting DB to Keycloak Synchronization...")
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
    )
    
    try:
        # Get all schemas
        schemas = await conn.fetch("SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'tenant_%'")
        
        for schema_record in schemas:
            tenant_id = schema_record["schema_name"]
            print(f"\nProcessing Tenant: {tenant_id}")
            
            # Fetch all users in this tenant's schema
            await conn.execute(f'SET search_path TO "{tenant_id}", public;')
            users = await conn.fetch("SELECT email, role FROM users")
            
            for user in users:
                email = user["email"]
                role = user["role"]
                # First/last name can be derived from email for aesthetics if not in DB
                parts = email.split("@")[0].split(".")
                first_name = parts[0].capitalize() if len(parts) > 0 else ""
                last_name = parts[1].capitalize() if len(parts) > 1 else ""
                
                print(f"  Syncing user: {email} | Role: {role}")
                try:
                    sync_user_to_keycloak(
                        email=email,
                        password="password123", # dummy password if creating new
                        role=role,
                        tenant_id=tenant_id,
                        first_name=first_name,
                        last_name=last_name
                    )
                except Exception as e:
                    print(f"    Failed to sync {email}: {e}")
                    
        print("\nSynchronization Complete!")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
