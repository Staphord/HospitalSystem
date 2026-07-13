import os
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

db_url = "postgresql://postgres:12345678@postgres-master:5432/hospital_master"
key = "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0="
f = Fernet(key.encode())

engine = create_engine(db_url)
with engine.connect() as conn:
    trans = conn.begin()
    try:
        # 1. Fetch all tenants
        result = conn.execute(text("SELECT id, tenant_id, db_connection_string FROM tenants"))
        tenants = result.fetchall()
        
        for t_id, tenant_id, encrypted_conn in tenants:
            # 2. Decrypt connection string
            decrypted = f.decrypt(encrypted_conn.encode()).decode()
            print(f"Original for {tenant_id}: {decrypted}")
            
            # 3. Replace nasr with 12345678
            updated = decrypted.replace(":nasr@", ":12345678@")
            
            # 4. Encrypt again
            encrypted_updated = f.encrypt(updated.encode()).decode()
            
            # 5. Update database record
            conn.execute(
                text("UPDATE tenants SET db_connection_string = :conn WHERE id = :id"),
                {"conn": encrypted_updated, "id": t_id}
            )
            print(f"Updated connection string for {tenant_id} to new password.")
            
        trans.commit()
    except Exception as e:
        trans.rollback()
        print(f"Error occurred: {e}")
        raise e
