import sys
from sqlalchemy import create_engine, text

# Try postgres as password first, then nasr
passwords = ["postgres", "nasr"]
engine = None
for pw in passwords:
    try:
        eng = create_engine(f'postgresql://postgres:{pw}@localhost:5432/hospital_master')
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine = eng
        print(f"Connected successfully with password: {pw}")
        break
    except Exception as e:
        print(f"Failed connection with password {pw}: {e}")

if not engine:
    print("Could not connect to database with either password.")
    sys.exit(1)

with engine.connect() as conn:
    # 1. Print all column names of tenants table
    cols = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tenants' ORDER BY ordinal_position")).all()
    print('Columns:', [c[0] for c in cols])
    
    # 2. Print all records in tenants table
    rows = conn.execute(text('SELECT tenant_id, hospital_name, status, is_active, db_connection_string FROM tenants')).all()
    print("\nTenants:")
    for r in rows:
        print(f'  {r[0]:20s} | {r[1]:20s} | {r[2]:10s} | active={r[3]} | DSN={r[4][:30]}...')
    
    # 3. Check if tenant DB exists
    print()
    dbs = conn.execute(text("SELECT datname FROM pg_database WHERE datname LIKE 'tenant_%'")).all()
    print('Tenant DBs:', [d[0] for d in dbs])
engine.dispose()
