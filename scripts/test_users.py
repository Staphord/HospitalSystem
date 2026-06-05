import httpx, json, base64

url = "http://127.0.0.1:8080"
realm = "hospital-realm"
secret = "HuqlMwVdGchYya4l3qRJwOhgwWQ1z5mL"

# Check user state first
token_r = httpx.post(f"{url}/realms/master/protocol/openid-connect/token", data={
    "grant_type": "password", "client_id": "admin-cli",
    "username": "admin", "password": "admin",
})
token = token_r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

for uid, name in [("90ad4842-0643-4632-9f0c-2efc23674be3", "hospitaladmin"), ("f8e9861a-873c-4292-82c2-5a36f5d6c6b6", "nurse2")]:
    u = httpx.get(f"{url}/admin/realms/{realm}/users/{uid}", headers=headers).json()
    print(f"{name}: email={u.get('email')} enabled={u.get('enabled')} emailVerified={u.get('emailVerified')} attrs={u.get('attributes')}")

print()

# Test login
for user in ["superadmin", "hospitaladmin", "nurse2"]:
    r = httpx.post(f"{url}/realms/{realm}/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "hospital-api",
        "client_secret": secret, "username": user, "password": "Nassir_05",
    })
    if r.status_code == 200:
        t = r.json()["access_token"]
        parts = t.split(".")
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        decoded = json.loads(base64.b64decode(padded))
        tid = decoded.get("tenant_id", "N/A")
        roles = decoded.get("realm_access", {}).get("roles", [])
        print(f"{user}: OK tenant_id={tid} roles={roles}")
    else:
        print(f"{user}: FAILED {r.json().get('error_description', r.text)}")
