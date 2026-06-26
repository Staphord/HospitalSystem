"""Test patient DB connection from patient service context."""
import sys
sys.path.insert(0, "/app")

from app.dependencies import get_tenant_session, resolve_tenant_db_url

url = resolve_tenant_db_url("hosp-43be392c")
print("Resolved URL:", url[:50] + "...")

db = get_tenant_session(url)

# Check patients table columns
from sqlalchemy import text
cols = db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'patients' ORDER BY ordinal_position")).all()
print("Patients columns:", [c[0] for c in cols])

# Query patients
from app.models.patient import TenantPatient
count = db.query(TenantPatient).count()
print(f"Patient count: {count}")

patients = db.query(TenantPatient).limit(5).all()
for p in patients:
    print(f"  {p.id} | {p.full_name} | {p.patient_number}")

db.close()
print("SUCCESS")
