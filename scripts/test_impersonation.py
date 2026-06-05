import httpx, json, base64

base_url = "http://127.0.0.1:8000/api/v1"

# Login as superadmin
r = httpx.post(f"{base_url}/auth/login", json={
    "username": "superadmin", "password": "Nassir_05",
})
sa_token = r.json()["access_token"]
print("superadmin: LOGIN OK")

# Test impersonation
r2 = httpx.post(f"{base_url}/auth/impersonate", json={
    "target_tenant_id": "hosp-001",
    "target_user_sub": "90ad4842-0643-4632-9f0c-2efc23674be3",
}, headers={"Authorization": f"Bearer {sa_token}"})
print(f"Impersonation: HTTP {r2.status_code}")
if r2.status_code != 200:
    print(f"Response: {r2.text[:2000]}")
    # The server logs the error - let me also check the KC client credentials flow
    kc_url = "http://127.0.0.1:8080"
    r3 = httpx.post(f"{kc_url}/realms/hospital-realm/protocol/openid-connect/token", data={
        "grant_type": "client_credentials",
        "client_id": "hospital-api",
        "client_secret": "HuqlMwVdGchYya4l3qRJwOhgwWQ1z5mL",
    })
    print(f"\nDirect client_credentials: HTTP {r3.status_code}")
    print(f"Response: {r3.text[:500]}")
    
    # Check JWKS
    r4 = httpx.get(f"{kc_url}/realms/hospital-realm/protocol/openid-connect/certs")
    print(f"\nJWKS: HTTP {r4.status_code}")
    jwks = r4.json()
    for k in jwks.get("keys", []):
        kid = k.get("kid", "?")
        use = k.get("use", "?")
        kty = k.get("kty", "?")
        print(f"  kid={kid} use={use} kty={kty}")
