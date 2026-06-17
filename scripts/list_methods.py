import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

def main():
    engine = create_engine(os.getenv("DATABASE_URL"))
    with engine.connect() as conn:
        tenants = conn.execute(text("SELECT tenant_id, name, db_dsn_encrypted, is_active FROM tenants")).fetchall()
        print("Tenants in master DB:")
        for t in tenants:
            print(f"  - Tenant ID: {t[0]}")
            print(f"    Name: {t[1]}")
            print(f"    DSN Encrypted: {t[2][:20]}... (len: {len(t[2])})")
            print(f"    Is Active: {t[3]}")
            
            # Now let's try to decrypt and connect
            try:
                from cryptography.fernet import Fernet
                key = os.getenv("TENANT_DB_ENCRYPTION_KEY")
                cipher = Fernet(key.encode())
                dsn = cipher.decrypt(t[2].encode()).decode()
                print(f"    Decrypted DSN: {dsn[:40]}...")
                
                # Check tables inside this tenant DB
                tenant_engine = create_engine(dsn)
                with tenant_engine.connect() as t_conn:
                    tables = t_conn.execute(
                        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
                    ).fetchall()
                    print(f"    Tables in tenant DB:")
                    for table in tables:
                        print(f"      - {table[0]}")
            except Exception as e:
                print(f"    Failed to decrypt or connect: {e}")
            print("-" * 30)

if __name__ == "__main__":
    main()
