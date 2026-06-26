"""Test patients list endpoint through the API gateway."""
import httpx
import json

BASE = "http://api-gateway:8000"

# Login
r = httpx.post(f"{BASE}/api/v1/auth/login", json={
    "username": "admin1",
    "password": "admin12345",
    "tenant_id": "hosp-43be392c"
}, timeout=15)
print("Login:", r.status_code)
if r.status_code != 200:
    print(r.text[:500])
    exit(1)

token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Call patients through gateway
r2 = httpx.get(f"{BASE}/api/v1/patients?limit=20&offset=0", headers=headers, timeout=15)
print("Patients:", r2.status_code)
if r2.status_code == 200:
    data = r2.json()
    print(f"Total: {data['total']}, Patients: {len(data['patients'])}")
    for p in data["patients"]:
        print(f"  {p['id']} | {p['full_name']} | {p['patient_number']}")
else:
    print(r2.text[:500])
