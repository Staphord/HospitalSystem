"""Check visit service tenant database schema."""
import os
import sys

sys.path.insert(0, "/app")
os.environ["DATABASE_URL"] = "postgresql://postgres:12345678@postgres-master:5432/master_db"

from app.dependencies import resolve_tenant_db_url
from sqlalchemy import create_engine, inspect, text

dsn = resolve_tenant_db_url("hosp-43be392c")
print("DSN:", dsn[:80] if dsn else "None")
if not dsn:
    sys.exit(1)

engine = create_engine(dsn)
insp = inspect(engine)

# Tables and columns
for t in insp.get_table_names():
    cols = [f"{c['name']}:{c['type']}" for c in insp.get_columns(t)]
    print(f"\n{t}:")
    for c in cols:
        print(f"  {c}")

# Enum types
with engine.connect() as conn:
    rows = conn.execute(
        text("SELECT t.typname, ARRAY_AGG(e.enumlabel) FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid GROUP BY t.typname")
    ).fetchall()
    print("\nEnum types:")
    if rows:
        for r in rows:
            print(f"  {r[0]}: {r[1]}")
    else:
        print("  (none)")

engine.dispose()
