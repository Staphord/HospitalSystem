"""Check Keycloak client mapper configs."""
import httpx

KEYCLOAK_URL = "http://host.docker.internal:8080"
r = httpx.post(
    f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
    data={"grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": "admin"},
)
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

r2 = httpx.get(
    f"{KEYCLOAK_URL}/admin/realms/hospital-realm/clients?clientId=hospital-api",
    headers=headers,
)
cid = r2.json()[0]["id"]

r3 = httpx.get(
    f"{KEYCLOAK_URL}/admin/realms/hospital-realm/clients/{cid}/protocol-mappers/models",
    headers=headers,
)
for m in r3.json():
    print(f"name={m['name']}  claim.name={m['config'].get('claim.name','?')}  user.attribute={m['config'].get('user.attribute','?')}")
