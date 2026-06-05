import httpx, json, base64

base_url = "http://127.0.0.1:8000/api/v1"

# === 1. Login as superadmin ===
r = httpx.post(f"{base_url}/auth/login", json={
    "username": "superadmin", "password": "Nassir_05",
})
sa_token = r.json()["access_token"]
print("=== superadmin login ===")
print(f"  scope={r.json().get('scope')} tenant_id={r.json().get('tenant_id')}")

# === 2. /me as superadmin ===
r2 = httpx.get(f"{base_url}/me", headers={"Authorization": f"Bearer {sa_token}"})
print(f"\n=== /me (superadmin) ===")
print(f"  {json.dumps(r2.json(), indent=2)}")

# === 3. Impersonate hospitaladmin ===
r3 = httpx.post(f"{base_url}/auth/impersonate", json={
    "target_tenant_id": "hosp-001",
    "target_user_sub": "90ad4842-0643-4632-9f0c-2efc23674be3",
}, headers={"Authorization": f"Bearer {sa_token}"})
print(f"\n=== Impersonation ===")
if r3.status_code == 200:
    imp = r3.json()
    print(f"  scope={imp['scope']}")
    parts = imp["access_token_stub"].split(".")
    padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
    decoded = json.loads(base64.b64decode(padded))
    print(f"  JWT: tenant_id={decoded.get('tenant_id')} scope={decoded.get('scope')} is_super_admin={decoded.get('is_super_admin')} impersonator={decoded.get('impersonator')}")
else:
    print(f"  FAILED: HTTP {r3.status_code} {r3.text}")

# === 4. Use impersonation token ===
if r3.status_code == 200:
    imp_token = imp["access_token_stub"]
    r4 = httpx.get(f"{base_url}/me", headers={"Authorization": f"Bearer {imp_token}"})
    print(f"\n=== /me with impersonation token ===")
    if r4.status_code == 200:
        print(f"  {json.dumps(r4.json(), indent=2)}")
    else:
        print(f"  FAILED: HTTP {r4.status_code} {r4.text}")

# === 5. Login as hospitaladmin ===
r5 = httpx.post(f"{base_url}/auth/login", json={
    "username": "hospitaladmin", "password": "Nassir_05",
})
ha_token = r5.json()["access_token"]
print(f"\n=== hospitaladmin login ===")
print(f"  scope={r5.json().get('scope')} tenant_id={r5.json().get('tenant_id')}")

# === 6. /me as hospitaladmin ===
r6 = httpx.get(f"{base_url}/me", headers={"Authorization": f"Bearer {ha_token}"})
print(f"\n=== /me (hospitaladmin) ===")
print(f"  {json.dumps(r6.json(), indent=2)}")
