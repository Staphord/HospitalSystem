import httpx, json

url = "http://127.0.0.1:8080"
realm = "hospital-realm"

r = httpx.post(f"{url}/realms/master/protocol/openid-connect/token", data={
    "grant_type": "password", "client_id": "admin-cli",
    "username": "admin", "password": "admin",
})
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Get current profile
r2 = httpx.get(f"{url}/admin/realms/{realm}/users/profile", headers=headers)
profile = r2.json()
print(f"Current profile has {len(profile.get('attributes', []))} attributes")

# Find if tenant_id already exists
existing_names = [a["name"] for a in profile["attributes"]]
if "tenant_id" not in existing_names:
    # Add tenant_id as an admin-only unmanaged attribute
    profile["attributes"].append({
        "name": "tenant_id",
        "displayName": "Tenant ID",
        "validations": {},
        "permissions": {
            "view": ["admin", "user"],
            "edit": ["admin"],
        },
        "multivalued": False,
    })
    print("Added tenant_id to profile")
else:
    print("tenant_id already in profile")

# Update profile
r3 = httpx.put(f"{url}/admin/realms/{realm}/users/profile", json=profile, headers=headers)
print(f"PUT profile: HTTP {r3.status_code} {r3.text}")

# Now try setting tenant_id on a user
uid = "a0f4069b-f124-4fe6-985c-8376e99781a7"
resp = httpx.put(f"{url}/admin/realms/{realm}/users/{uid}", json={
    "attributes": {"tenant_id": ["hosp-001"]},
}, headers=headers)
print(f"Set tenant_id on user: HTTP {resp.status_code}")

# Verify
u = httpx.get(f"{url}/admin/realms/{realm}/users/{uid}", headers=headers).json()
print(f"User attributes after profile fix: {u.get('attributes')}")

# Also do nurse2
uid2 = "d3e70485-ecd6-4205-b38e-800a4879e75f"
httpx.put(f"{url}/admin/realms/{realm}/users/{uid2}", json={
    "attributes": {"tenant_id": ["hosp-001"]},
}, headers=headers)
u2 = httpx.get(f"{url}/admin/realms/{realm}/users/{uid2}", headers=headers).json()
print(f"nurse2 attributes: {u2.get('attributes')}")

# Test login with JWT claim
print("\n--- Testing JWT claim ---")
for user in ["hospitaladmin", "nurse2"]:
    r4 = httpx.post(f"{url}/realms/{realm}/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "hospital-api",
        "client_secret": "HuqlMwVdGchYya4l3qRJwOhgwWQ1z5mL",
        "username": user, "password": "Nassir_05",
    })
    if r4.status_code == 200:
        t = r4.json()["access_token"]
        import base64
        parts = t.split(".")
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        decoded = json.loads(base64.b64decode(padded))
        tid = decoded.get("tenant_id", "N/A")
        print(f"{user}: OK tenant_id={tid}")
    else:
        print(f"{user}: FAILED {r4.text}")
