import httpx, json, base64

url = "http://127.0.0.1:8080"
realm = "hospital-realm"
secret = "HuqlMwVdGchYya4l3qRJwOhgwWQ1z5mL"

r = httpx.post(f"{url}/realms/master/protocol/openid-connect/token", data={
    "grant_type": "password", "client_id": "admin-cli",
    "username": "admin", "password": "admin",
})
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

for uid in ["a0f4069b-f124-4fe6-985c-8376e99781a7", "d3e70485-ecd6-4205-b38e-800a4879e75f"]:
    u = httpx.get(f"{url}/admin/realms/{realm}/users/{uid}", headers=headers).json()

    # Build full payload preserving everything
    update_payload = {
        "username": u["username"],
        "email": u.get("email"),
        "firstName": u.get("firstName", ""),
        "lastName": u.get("lastName", ""),
        "enabled": u.get("enabled", True),
        "emailVerified": u.get("emailVerified", True),
        "requiredActions": u.get("requiredActions", []),
        "attributes": {"tenant_id": ["hosp-001"]},
    }

    # Remove None values
    update_payload = {k: v for k, v in update_payload.items() if v is not None}

    print(f"Updating {u['username']} with: {json.dumps(update_payload, indent=2)}")
    resp = httpx.put(f"{url}/admin/realms/{realm}/users/{uid}", json=update_payload, headers=headers)
    print(f"  HTTP {resp.status_code}")

    # Verify user state
    u2 = httpx.get(f"{url}/admin/realms/{realm}/users/{uid}", headers=headers).json()
    print(f"  email={u2.get('email')} attrs={u2.get('attributes')}")

    # Test login
    r3 = httpx.post(f"{url}/realms/{realm}/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "hospital-api",
        "client_secret": secret, "username": u["username"], "password": "Nassir_05",
    })
    if r3.status_code == 200:
        t = r3.json()["access_token"]
        parts = t.split(".")
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        decoded = json.loads(base64.b64decode(padded))
        tid = decoded.get("tenant_id", "N/A")
        print(f"  LOGIN OK tenant_id={tid}")
    else:
        print(f"  LOGIN FAILED: {r3.json().get('error_description', r3.text)}")
