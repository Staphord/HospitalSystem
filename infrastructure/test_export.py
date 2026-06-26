"""Test the tenant data export endpoint in master-service."""
import httpx
import sys

GATEWAY = "http://api-gateway:8000"
MASTER = "http://master-service:8002"

# Super admin login
r = httpx.post(f"{GATEWAY}/api/v1/auth/superadmin/login", json={
    "username": "superadmin",
    "password": "superadmin123"
}, timeout=15)
print("Login:", r.status_code)
if r.status_code != 200:
    print(r.text[:500])
    sys.exit(1)

token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Export via master-service directly
r2 = httpx.get(
    f"{MASTER}/api/v1/superadmin/tenants/hosp-43be392c/export",
    headers=headers,
    timeout=30
)
print("Export:", r2.status_code)
if r2.status_code == 200:
    data = r2.json()
    for table, rows in data.items():
        print(f"  {table}: {len(rows)} rows")
else:
    print(r2.text[:1000])
