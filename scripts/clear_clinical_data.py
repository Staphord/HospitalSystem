#!/usr/bin/env python3
import sys
from sqlalchemy import create_engine, text

def clear_tenant_data(db_url, db_name):
    print(f"\nConnecting to database: {db_name}...")
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            # Let's truncate the clinical and queue data tables
            tables_to_truncate = [
                "triage_assessments",
                "queues",
                "visits",
                "patient_insurance",
                "patients",
                "appointments",
                "patient_number_sequences",
                "visit_number_sequences",
                "queue_number_sequences"
            ]
            
            print(f"Truncating tables: {', '.join(tables_to_truncate)}...")
            # Using CASCADE to handle any remaining foreign keys
            conn.execute(text(f"TRUNCATE TABLE {', '.join(tables_to_truncate)} CASCADE;"))
            conn.commit()
            print(f"[OK] Cleaned all clinical data and reset tracking sequences in '{db_name}'.")
    except Exception as e:
        print(f"[ERROR] Failed to clean database '{db_name}': {e}")

def main():
    base_connection_str = "postgresql://postgres:postgres@localhost:5432/"
    tenants = ["tenant_hosp-citygeneral", "tenant_hosp-mercy"]
    
    print("=== CLEARING CLINICAL DATA & RESETTING QUEUES ===")
    for tenant_db in tenants:
        db_url = f"{base_connection_str}{tenant_db}"
        clear_tenant_data(db_url, tenant_db)
    print("\n=== SYSTEM CLEANED AND READY FOR DEMO ===")

if __name__ == "__main__":
    main()
