from sqlalchemy import create_engine, text
engine = create_engine('postgresql://postgres:nasr@localhost:5432/hospital_master')
with engine.connect() as conn:
    cols = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tenants' ORDER BY ordinal_position")).all()
    print('Columns:', [c[0] for c in cols])
    rows = conn.execute(text('SELECT tenant_id, name, status, is_active FROM tenants')).all()
    for r in rows:
        print(f'  {r[0]:20s} | {r[1]:20s} | {r[2]:10s} | active={r[3]}')
    # Check if tenant DB exists
    print()
    dbs = conn.execute(text("SELECT datname FROM pg_database WHERE datname LIKE 'tenant_%'")).all()
    print('Tenant DBs:', [d[0] for d in dbs])
engine.dispose()
