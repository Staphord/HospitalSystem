"""Debug script to check a Keycloak user's required actions."""
import asyncio
import os
import httpx

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "hospital-realm")
ADMIN_USER = os.getenv("KEYCLOAK_ADMIN_USERNAME", "admin")
ADMIN_PASS = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")


async def get_admin_token() -> str:
    url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "client_id": "admin-cli",
        "username": ADMIN_USER,
        "password": ADMIN_PASS,
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(url, data=data)
        r.raise_for_status()
        return r.json()["access_token"]


async def check_user(username: str):
    token = await get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    base = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}"

    async with httpx.AsyncClient(timeout=10.0) as c:
        # Search user
        r = await c.get(f"{base}/users?username={username}", headers=headers)
        r.raise_for_status()
        users = r.json()
        if not users:
            print(f"User '{username}' not found")
            return
        user = users[0]
        print(f"User: {user.get('username')}")
        print(f"  ID: {user.get('id')}")
        print(f"  Enabled: {user.get('enabled')}")
        print(f"  EmailVerified: {user.get('emailVerified')}")
        print(f"  RequiredActions: {user.get('requiredActions', [])}")
        print(f"  Attributes: {user.get('attributes', {})}")

        # Check realm authentication flows
        r2 = await c.get(f"{base}/authentication/flows", headers=headers)
        if r2.is_success:
            print(f"\nAuthentication flows:")
            for flow in r2.json():
                if "direct" in flow.get("alias", "").lower():
                    print(f"  {flow.get('alias')}: built-in={flow.get('builtIn')}, providerId={flow.get('providerId')}, description={flow.get('description')}")

        # Check realm browser security headers
        r3 = await c.get(f"{base}/users/profile", headers=headers)
        if r3.is_success:
            print(f"\nUser Profile config retrieved")


if __name__ == "__main__":
    import sys
    username = sys.argv[1] if len(sys.argv) > 1 else "nurse2"
    asyncio.run(check_user(username))
