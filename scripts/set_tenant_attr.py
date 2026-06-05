import httpx, os, json, base64

KEYCLOAK_URL = "http://127.0.0.1:8080"
KEYCLOAK_REALM = "hospital-realm"
ADMIN_USER = "admin"
ADMIN_PASS = "admin"
CLIENT_SECRET = "HuqlMwVdGchYya4l3qRJwOhgwWQ1z5mL"

r = httpx.post(f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token", data={
    "grant_type": "password", "client_id": "admin-cli",
    "username": ADMIN_USER, "password": ADMIN_PASS,
})
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

for uid, hid in [
    ("8997ee73-2094-4edf-9444-fecb25a31daf", "hosp-001"),  # hospitaladmin
    ("c5294089-739f-4a13-bcb9-d9f35b556825", "hosp-001"),  # nurse2
]:
    resp = httpx.put(f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{uid}", json={"attributes": {"tenant_id": [hid]}}, headers=headers)
    print(f"{uid}: HTTP {resp.status_code}")
    resp2 = httpx.get(f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/{uid}", headers=headers)
    u = resp2.json()
    print(f"  attributes: {u.get('attributes')}")

# Test login for each user
for username in ["hospitaladmin", "nurse2"]:
    resp3 = httpx.post(f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "hospital-api",
        "client_secret": CLIENT_SECRET,
        "username": username, "password": "Nassir_05",
    })
    if resp3.status_code == 200:
        t = resp3.json()["access_token"]
        parts = t.split(".")
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        decoded = json.loads(base64.b64decode(padded))
        print(f"{username}: LOGIN OK, tenant_id={decoded.get('tenant_id')}")
    else:
        print(f"{username}: LOGIN FAILED: {resp3.text}")
