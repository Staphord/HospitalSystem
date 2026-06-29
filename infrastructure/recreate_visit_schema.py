"""Drop and recreate visit-service tables in tenant database."""
import os
import sys

sys.path.insert(0, "/app")
os.environ["DATABASE_URL"] = "postgresql://postgres:nasr@postgres-master:5432/master_db"

from app.dependencies import resolve_tenant_db_url
from app.db.base import Base
from sqlalchemy import create_engine

dsn = resolve_tenant_db_url("hosp-43be392c")
print("DSN resolved")
engine = create_engine(dsn)
Base.metadata.create_all(bind=engine)
engine.dispose()
print("Tables created successfully")
