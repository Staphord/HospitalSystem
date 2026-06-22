import os
import sys
from dotenv import load_dotenv
import httpx
import asyncio
import json

load_dotenv()

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "hospital-realm")
KEYCLOAK_ADMIN_USER = os.getenv("KEYCLOAK_ADMIN_USERNAME", "admin")
KEYCLOAK_ADMIN_PASS = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")

async def _get_admin_token() -> str:
    url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "client_id": "admin-cli",
        "username": KEYCLOAK_ADMIN_USER,
        "password": KEYCLOAK_ADMIN_PASS,
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(url, data=data)
        r.raise_for_status()
        return r.json()["access_token"]

async def main():
    token = await _get_admin_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.get(f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users/profile", headers=headers)
        print("User profile config:")
        print(json.dumps(resp.json(), indent=2))

asyncio.run(main())
