from sqlalchemy import create_engine, text

engine = create_engine('postgresql://postgres:postgres@localhost:5432/tenant_hosp-1d306fee')
try:
    with engine.connect() as conn:
        tables = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")).all()
        print('Tables in tenant_hosp-1d306fee:', [t[0] for t in tables])
        
        cols = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'patients' ORDER BY ordinal_position")).all()
        print('Patients columns in hosp-1d306fee:')
        for name, dtype in cols:
            print(f'  {name:25s} | {dtype}')
except Exception as e:
    print("Error:", e)
engine.dispose()
