import httpx, json

url = "http://127.0.0.1:8080"
realm = "hospital-realm"

# Get admin token
r = httpx.post(f"{url}/realms/master/protocol/openid-connect/token", data={
    "grant_type": "password", "client_id": "admin-cli",
    "username": "admin", "password": "admin",
})
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

uid = "a0f4069b-f124-4fe6-985c-8376e99781a7"

# Try setting attribute with different payload format
# Keycloak 26 expects attributes as Map<String, List<String>>
for attempt, payload in [
    ("dict with list", {"attributes": {"tenant_id": ["hosp-001"]}}),
    ("with id", {"id": uid, "attributes": {"tenant_id": ["hosp-001"]}}),
]:
    print(f"\nAttempt: {attempt}")
    print(f"Payload: {json.dumps(payload)}")
    resp = httpx.put(f"{url}/admin/realms/{realm}/users/{uid}", json=payload, headers=headers)
    print(f"PUT: HTTP {resp.status_code}")
    u = httpx.get(f"{url}/admin/realms/{realm}/users/{uid}", headers=headers).json()
    attrs = u.get("attributes")
    print(f"After update - attributes type={type(attrs).__name__} value={json.dumps(attrs) if attrs else attrs}")

# Also check if there's an attribute endpoint
print(f"\n--- Checking attribute endpoint ---")
r2 = httpx.get(f"{url}/admin/realms/{realm}/users/{uid}", headers=headers)
d = r2.json()
print(f"Response keys: {list(d.keys())}")
print(f"Has attributes key: {'attributes' in d}")
print(f"Raw attributes field: {d.get('attributes')!r}")

# Try the 'groups' endpoint
print(f"\n--- Checking unmanaged attributes ---")
r3 = httpx.get(f"{url}/admin/realms/{realm}/users/{uid}/unmanaged-attributes", headers=headers)
print(f"Unmanaged attrs: HTTP {r3.status_code} {r3.text[:200]}")
