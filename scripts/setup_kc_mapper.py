"""Configure Keycloak client mapper for tenant_id user attribute."""
import httpx

KEYCLOAK_URL = "http://host.docker.internal:8080"

# Get admin token
r = httpx.post(
    f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
    data={"grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": "admin"},
)
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Find the hospital-api client in hospital-realm
r2 = httpx.get(
    f"{KEYCLOAK_URL}/admin/realms/hospital-realm/clients?clientId=hospital-api",
    headers=headers,
)
clients = r2.json()
if not clients:
    print("hospital-api client not found in hospital-realm")
    exit(1)

client_id = clients[0]["id"]
print(f"hospital-api client ID: {client_id}")

# Check existing mappers
r3 = httpx.get(
    f"{KEYCLOAK_URL}/admin/realms/hospital-realm/clients/{client_id}/protocol-mappers/models",
    headers=headers,
)
mappers = r3.json()
print(f"Existing mappers: {[m['name'] for m in mappers]}")

# Check if we already have the mapper
if any(m["name"] == "tenant_id" for m in mappers):
    print("tenant_id mapper already exists")
else:
    # Create mapper for tenant_id user attribute
    mapper_payload = {
        "name": "tenant_id",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-usermodel-attribute-mapper",
        "config": {
            "user.attribute": "tenant_id",
            "claim.name": "tenant_id",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true",
            "jsonType.label": "String",
        },
    }
    r4 = httpx.post(
        f"{KEYCLOAK_URL}/admin/realms/hospital-realm/clients/{client_id}/protocol-mappers/models",
        json=mapper_payload,
        headers=headers,
    )
    if r4.status_code in (201, 204):
        print("tenant_id mapper created successfully")
    else:
        print(f"Failed ({r4.status_code}): {r4.text[:300]}")
