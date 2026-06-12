import asyncio
import json
import os
import sys

import httpx
import requests
from keycloak import KeycloakAdmin


def _get_kc_admin() -> KeycloakAdmin:
    keycloak_url = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
    admin_user = os.getenv("KEYCLOAK_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")

    try:
        requests.get(f"{keycloak_url}/realms/master/.well-known/openid-configuration", timeout=30)
    except (requests.ConnectionError, requests.Timeout) as e:
        print(f"ERROR: Cannot reach Keycloak at {keycloak_url}: {e}")
        print("Start it with: docker run -p 8080:8080 -e KEYCLOAK_ADMIN=admin -e KEYCLOAK_ADMIN_PASSWORD=admin quay.io/keycloak/keycloak:26.6.2 start-dev")
        sys.exit(1)

    return KeycloakAdmin(
        server_url=keycloak_url,
        username=admin_user,
        password=admin_password,
        realm_name="master",
        verify=False,
    )


async def _get_admin_token() -> str:
    keycloak_url = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
    admin_user = os.getenv("KEYCLOAK_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
    url = f"{keycloak_url}/realms/master/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "client_id": "admin-cli",
        "username": admin_user,
        "password": admin_password,
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(url, data=data)
        r.raise_for_status()
        return r.json()["access_token"]


async def _ensure_client(kc_admin: KeycloakAdmin) -> None:
    client_id = os.getenv("KEYCLOAK_CLIENT_ID", "hospital-api")
    client_secret = os.getenv("KEYCLOAK_CLIENT_SECRET", "hospital-api-secret")
    realm = kc_admin.realm_name
    keycloak_url = os.getenv("KEYCLOAK_URL", "http://localhost:8080")

    token = await _get_admin_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10.0) as c:
        search = await c.get(
            f"{keycloak_url}/admin/realms/{realm}/clients?clientId={client_id}",
            headers=headers,
        )
        existing = search.json()

        app_origin = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")[0].strip()

        payload = {
            "clientId": client_id,
            "name": "Hospital API",
            "protocol": "openid-connect",
            "publicClient": False,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": True,
            "serviceAccountsEnabled": False,
            "redirectUris": [f"{app_origin}/*"],
            "webOrigins": [app_origin],
        }

        if existing:
            client_uuid = existing[0]["id"]
            print(f"  Client '{client_id}' already exists (UUID: {client_uuid})")
        else:
            create_resp = await c.post(
                f"{keycloak_url}/admin/realms/{realm}/clients",
                json=payload,
                headers=headers,
            )
            create_resp.raise_for_status()
            client_uuid = create_resp.headers.get("location", "").rsplit("/", 1)[-1]
            if not client_uuid:
                search2 = await c.get(
                    f"{keycloak_url}/admin/realms/{realm}/clients?clientId={client_id}",
                    headers=headers,
                )
                items = search2.json()
                client_uuid = items[0]["id"] if items else None

        print(f"  Client '{client_id}' ready (UUID: {client_uuid})")

        if client_uuid and not existing:
            gen = await c.post(
                f"{keycloak_url}/admin/realms/{realm}/clients/{client_uuid}/client-secret",
                headers=headers,
            )
            gen.raise_for_status()
            actual_secret = gen.json().get("value", "")
            if actual_secret:
                env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
                if os.path.isfile(env_path):
                    with open(env_path, "r") as f:
                        content = f.read()
                    with open(env_path, "w") as f:
                        for line in content.splitlines(keepends=True):
                            if line.startswith("KEYCLOAK_CLIENT_SECRET="):
                                f.write(f"KEYCLOAK_CLIENT_SECRET={actual_secret}\n")
                            else:
                                f.write(line)
                    print(f"  Updated .env with actual client secret")
                else:
                    print(f"  Actual client secret: {actual_secret}")
                    print(f"  Update KEYCLOAK_CLIENT_SECRET in .env to this value")


def main() -> None:
    kc_admin = _get_kc_admin()
    realm = os.getenv("KEYCLOAK_REALM", "hospital-realm")

    if realm not in [r["realm"] for r in kc_admin.get_realms()]:
        print(f"Creating realm '{realm}'...")
        kc_admin.create_realm(payload={"realm": realm, "enabled": True})
    else:
        print(f"Realm '{realm}' already exists")

    kc_admin.realm_name = realm
    kc_admin.connection.realm_name = realm  # get_users reads this

    for role_name in ("hospital_user", "hospital_admin", "super_admin"):
        try:
            kc_admin.get_realm_role(role_name)
            print(f"  Role '{role_name}' already exists")
        except Exception:
            print(f"  Creating role '{role_name}'...")
            kc_admin.create_realm_role(payload={"name": role_name})

    asyncio.run(_ensure_client(kc_admin))

    test_username = os.getenv("KEYCLOAK_TEST_USERNAME", "testuser")
    test_password = os.getenv("KEYCLOAK_TEST_PASSWORD", "testpassword")
    admin_username = os.getenv("KEYCLOAK_ADMIN_TEST_USERNAME", "adminuser")
    admin_password_user = os.getenv("KEYCLOAK_ADMIN_TEST_PASSWORD", "adminpassword")
    superadmin_username = os.getenv("KEYCLOAK_SUPERADMIN_TEST_USERNAME", "superadmin")
    superadmin_password = os.getenv("KEYCLOAK_SUPERADMIN_TEST_PASSWORD", "superadmin123")

    _ensure_user(kc_admin, test_username, test_password, ["hospital_user"])
    print(f"  User '{test_username}' ready")
    _ensure_user(kc_admin, admin_username, admin_password_user, ["hospital_admin"])
    print(f"  User '{admin_username}' ready")
    _ensure_user(kc_admin, superadmin_username, superadmin_password, ["super_admin"])
    print(f"  User '{superadmin_username}' ready")


def _ensure_user(kc_admin: KeycloakAdmin, username: str, password: str, roles: list[str]) -> None:
    user_id = kc_admin.get_user_id(username)
    if not user_id:
        user_id = kc_admin.create_user(
            {
                "username": username,
                "firstName": username.capitalize(),
                "lastName": "User",
                "enabled": True,
                "email": f"{username}@example.com",
                "emailVerified": True,
                "requiredActions": [],
            }
        )
    else:
        # Existing user may lack firstName/lastName — ensure they are set
        kc_admin.update_user(
            user_id=user_id,
            payload={
                "firstName": username.capitalize(),
                "lastName": "User",
                "emailVerified": True,
                "requiredActions": [],
            },
        )

    kc_admin.set_user_password(user_id=user_id, password=password, temporary=False)

    role_reps = [kc_admin.get_realm_role(role) for role in roles]
    kc_admin.assign_realm_roles(user_id=user_id, roles=role_reps)


if __name__ == "__main__":
    main()
