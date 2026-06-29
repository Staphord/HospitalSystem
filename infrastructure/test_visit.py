"""Test visit creation through gateway."""
import httpx
import sys

GATEWAY = "http://api-gateway:8000"
VISIT = "http://visit-service:8006"

# Login as hospital admin
r = httpx.post(f"{GATEWAY}/api/v1/auth/login", json={
    "username": "hadmin1",
    "password": "admin12345",
    "tenant_id": "hosp-43be392c"
}, timeout=15)
print("Login:", r.status_code)
if r.status_code != 200:
    print(r.text[:500])
    sys.exit(1)

token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Create a visit
r2 = httpx.post(
    f"{VISIT}/api/v1/visits",
    headers=headers,
    json={
        "patient_id": "cd775925-0c9a-40b5-9374-7841bbd3ff3f",
        "visit_type": "inpatient",
        "payment_type": "cash",
        "insurer_name": "john",
        "policy_number": "1515161617"
    },
    timeout=15
)
print("Create visit:", r2.status_code)
if r2.status_code == 201:
    data = r2.json()
    print(f"Visit ID: {data['visit']['visit_id']}")
    print(f"Queue: {data['queue_number']}")
else:
    print(r2.text[:1000])
