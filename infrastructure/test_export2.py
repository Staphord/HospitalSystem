"""Test the tenant data export endpoint and inspect structure."""
import httpx
import sys
import json

GATEWAY = "http://api-gateway:8000"
MASTER = "http://master-service:8002"

r = httpx.post(f"{GATEWAY}/api/v1/auth/superadmin/login", json={
    "username": "superadmin",
    "password": "superadmin123"
}, timeout=15)
if r.status_code != 200:
    print("Login failed:", r.text[:500])
    sys.exit(1)

token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

r2 = httpx.get(
    f"{MASTER}/api/v1/superadmin/tenants/hosp-43be392c/export",
    headers=headers,
    timeout=30
)
print("Status:", r2.status_code)
data = r2.json()
print("Top-level keys:", list(data.keys()))
if "data" in data:
    inner = data["data"]
    for table, rows in inner.items():
        print(f"  {table}: {len(rows)} rows")
        if rows:
            print(f"    Sample: {list(rows[0].keys())}")
else:
    for table, rows in data.items():
        if isinstance(rows, list):
            print(f"  {table}: {len(rows)} rows")
