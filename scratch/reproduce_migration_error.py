import os, sys, subprocess
from sqlalchemy import create_engine, text

admin_engine = create_engine("postgresql://postgres:postgres@localhost:5432/postgres", isolation_level="AUTOCOMMIT")
db_name = "tenant_hosp_test"

with admin_engine.connect() as conn:
    conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    conn.execute(text(f'CREATE DATABASE "{db_name}"'))
admin_engine.dispose()

tenant_url = f"postgresql://postgres:postgres@localhost:5432/{db_name}"
env = {**dict(os.environ), "TENANT_DB_URL": tenant_url}
result = subprocess.run(
    [sys.executable, "-m", "alembic", "-c", "migrations/tenant/alembic.ini", "upgrade", "head"],
    env=env, capture_output=True, text=True, cwd=os.getcwd(),
)

print("Return code:", result.returncode)
print("Stdout:")
print(result.stdout)
print("Stderr:")
print(result.stderr)
