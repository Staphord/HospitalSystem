import os
import sys
from sqlalchemy import create_engine, text

# Add /app path so we can import app modules if needed
sys.path.insert(0, "/Volumes/nshondev/GILGAL/Hospital/HospitalSystem/services/reception-service")
sys.path.insert(0, "/Volumes/nshondev/GILGAL/Hospital/HospitalSystem/services/visit-service")

# Resolve tenant DB URL manually by checking the databases or setting standard URL
# Let's check master_db to see what tenants exist.
master_engine = create_engine("postgresql://postgres:12345678@localhost:5432/hospital_master")
with master_engine.connect() as conn:
    tenants = conn.execute(text("SELECT tenant_id, schema_name, db_name, db_user, db_password, db_host, db_port FROM tenants")).fetchall()
    print("Tenants in Master DB:")
    for t in tenants:
        print(t)
        # We can construct the DSN from tenant info
        # DSN format: postgresql://{user}:{password}@{host}:{port}/{db}
        # But wait, since host in docker is postgres-master, from host machine we should use localhost.
        dsn = f"postgresql://{t[3]}:{t[4]}@localhost:5432/{t[2]}"
        print(f"DSN for {t[0]}: {dsn}")
        
        tenant_engine = create_engine(dsn)
        with tenant_engine.connect() as t_conn:
            print("--- Patients ---")
            pats = t_conn.execute(text("SELECT id, patient_number, full_name, phone_primary FROM patients")).fetchall()
            for p in pats:
                print(p)
            print("--- Queue Entries ---")
            queues = t_conn.execute(text("SELECT queue_id, visit_id, patient_id, queue_type, queue_number, status FROM queues")).fetchall()
            for q in queues:
                print(q)
            print("--- Visits ---")
            visits = t_conn.execute(text("SELECT visit_id, patient_id, visit_number, status FROM visits")).fetchall()
            for v in visits:
                print(v)
        tenant_engine.dispose()

master_engine.dispose()
