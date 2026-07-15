import os
from sqlalchemy import create_engine, text
from cryptography.fernet import Fernet

def main():
    master_db_url = os.getenv("MASTER_DB_URL", "postgresql://postgres:12345678@localhost:5432/hospital_master")
    encryption_key = os.getenv("TENANT_DB_ENCRYPTION_KEY", "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0=")
    cipher = Fernet(encryption_key.encode())

    engine = create_engine(master_db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tenant_id, db_connection_string, is_active FROM tenants")
        ).fetchall()

    for row in rows:
        tenant_id, enc_dsn, is_active = row[0], row[1], row[2]
        if enc_dsn:
            try:
                dsn = cipher.decrypt(enc_dsn.encode()).decode()
                print(f"Tenant: {tenant_id}, Active: {is_active}, DSN: {dsn}")
            except Exception as e:
                print(f"Failed to decrypt: {e}")
        else:
            print(f"Tenant: {tenant_id}, Active: {is_active}, DSN: None")

if __name__ == "__main__":
    main()
