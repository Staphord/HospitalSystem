from sqlalchemy import create_engine, text
engine = create_engine('postgresql://postgres:postgres@localhost:5432/hospital_master')
with engine.connect() as conn:
    cols = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tenants' ORDER BY ordinal_position")).all()
    print('Columns:', [c[0] for c in cols])
    rows = conn.execute(text('SELECT tenant_id, hospital_name, status, is_active, db_connection_string FROM tenants')).all()
    from cryptography.fernet import Fernet
    import os
    key = "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0="
    cipher = Fernet(key.encode())
    for r in rows:
        dsn = "None"
        if r[4]:
            try:
                dsn = cipher.decrypt(r[4].encode()).decode()
            except Exception as e:
                dsn = f"Error decrypting: {e}"
        print(f'  {r[0]:20s} | {r[1]:20s} | {r[2]:10s} | active={r[3]} | DSN={dsn}')
    # Check if tenant DB exists
    print()
    dbs = conn.execute(text("SELECT datname FROM pg_database WHERE datname LIKE 'tenant_%'")).all()
    print('Tenant DBs:', [d[0] for d in dbs])
    print()
    print('Super Admins:')
    admins = conn.execute(text("SELECT super_admin_id, username, email, is_active FROM super_admins")).all()
    for a in admins:
         print(f"  ID={a[0]} | username={a[1]} | email={a[2]} | active={a[3]}")
engine.dispose()
