"""Create a hospital admin user in Keycloak hospital-realm with tenant_id attribute."""
import httpx

KEYCLOAK_URL = "http://host.docker.internal:8080"
REALM = "hospital-realm"
TENANT_ID = "hosp-43be392c"

# Get master admin token
r = httpx.post(
    f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
    data={"grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": "admin"},
)
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Check if user already exists
r2 = httpx.get(
    f"{KEYCLOAK_URL}/admin/realms/{REALM}/users?username=hadmin1&exact=true",
    headers=headers,
)
if r2.json():
    uid = r2.json()[0]["id"]
    print(f"User hadmin1 already exists: {uid}")
else:
    # Create hospital admin user
    payload = {
        "username": "hadmin1",
        "email": "hadmin1@hospital.com",
        "firstName": "Hospital",
        "lastName": "Admin",
        "enabled": True,
        "emailVerified": True,
        "requiredActions": [],
        "attributes": {"tenant_id": [TENANT_ID]},
    }
    r3 = httpx.post(f"{KEYCLOAK_URL}/admin/realms/{REALM}/users", json=payload, headers=headers)
    r3.raise_for_status()
    print(f"Created user, status: {r3.status_code}")

    # Get user ID
    r4 = httpx.get(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users?username=hadmin1&exact=true",
        headers=headers,
    )
    uid = r4.json()[0]["id"]
    print(f"User ID: {uid}")

    # Set password
    r5 = httpx.put(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{uid}/reset-password",
        json={"type": "password", "value": "admin12345", "temporary": False},
        headers=headers,
    )
    r5.raise_for_status()
    print(f"Password set: {r5.status_code}")

# Ensure tenant_id attribute is set
r6 = httpx.get(
    f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{uid}",
    headers=headers,
)
user_data = r6.json()
attrs = user_data.get("attributes", {})
if attrs.get("tenant_id") != [TENANT_ID]:
    user_data["attributes"] = {"tenant_id": [TENANT_ID]}
    r7 = httpx.put(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{uid}",
        json=user_data,
        headers=headers,
    )
    r7.raise_for_status()
    print(f"tenant_id attribute set: {r7.status_code}")
else:
    print("tenant_id attribute already correct")

# Assign hospital_admin role
r8 = httpx.get(
    f"{KEYCLOAK_URL}/admin/realms/{REALM}/roles/hospital_admin",
    headers=headers,
)
if r8.status_code == 200:
    role = r8.json()
    r9 = httpx.post(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{uid}/role-mappings/realm",
        json=[role],
        headers=headers,
    )
    r9.raise_for_status()
    print(f"hospital_admin role assigned: {r9.status_code}")
else:
    print(f"Role hospital_admin not found (status={r8.status_code})")

print("Done")
