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

        if client_uuid:
            await _ensure_user_attribute_mapper(
                c,
                headers,
                keycloak_url,
                realm,
                client_uuid,
                "tenant_id",
            )
            await _ensure_user_profile_attribute(
                c,
                headers,
                keycloak_url,
                realm,
                "tenant_id",
            )

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


async def _ensure_user_profile_attribute(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    keycloak_url: str,
    realm: str,
    attribute_name: str,
) -> None:
    profile_url = f"{keycloak_url}/admin/realms/{realm}/users/profile"
    resp = await client.get(profile_url, headers=headers)
    resp.raise_for_status()
    config = resp.json()

    attributes = config.get("attributes", [])
    for attr in attributes:
        if attr.get("name") == attribute_name:
            return

    new_attr = {
        "name": attribute_name,
        "displayName": attribute_name.replace("_", " ").title(),
        "validations": {},
        "permissions": {
            "view": ["admin", "user"],
            "edit": ["admin"]
        },
        "multivalued": False
    }
    attributes.append(new_attr)
    config["attributes"] = attributes

    put_resp = await client.put(profile_url, json=config, headers=headers)
    put_resp.raise_for_status()
    print(f"  Configured User Profile attribute: '{attribute_name}'")


async def _ensure_user_attribute_mapper(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    keycloak_url: str,
    realm: str,
    client_uuid: str,
    attribute_name: str,
) -> None:
    mappers_url = f"{keycloak_url}/admin/realms/{realm}/clients/{client_uuid}/protocol-mappers/models"
    existing = await client.get(mappers_url, headers=headers)
    existing.raise_for_status()
    for mapper in existing.json():
        if mapper.get("name") == attribute_name:
            return

    payload = {
        "name": attribute_name,
        "protocol": "openid-connect",
        "protocolMapper": "oidc-usermodel-attribute-mapper",
        "config": {
            "user.attribute": attribute_name,
            "claim.name": attribute_name,
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true",
        },
    }
    response = await client.post(mappers_url, json=payload, headers=headers)
    response.raise_for_status()
    print(f"  Added '{attribute_name}' token mapper")


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
    test_tenant_id = os.getenv("KEYCLOAK_TEST_TENANT_ID", "gilgal")

    _ensure_user(kc_admin, test_username, test_password, ["hospital_user"], tenant_id=test_tenant_id)
    print(f"  User '{test_username}' ready")
    _ensure_user(kc_admin, admin_username, admin_password_user, ["hospital_admin"], tenant_id=test_tenant_id)
    print(f"  User '{admin_username}' ready")
    _ensure_user(
        kc_admin,
        superadmin_username,
        superadmin_password,
        ["super_admin"],
        email="triplerainbow07@gmail.com",
    )
    print(f"  User '{superadmin_username}' ready")


def _ensure_user(
    kc_admin: KeycloakAdmin,
    username: str,
    password: str,
    roles: list[str],
    tenant_id: str | None = None,
    email: str | None = None,
) -> None:
    attributes = {"tenant_id": [tenant_id]} if tenant_id else None
    user_id = kc_admin.get_user_id(username)
    if not user_id:
        payload = {
            "username": username,
            "firstName": username.capitalize(),
            "lastName": "User",
            "enabled": True,
            "email": email or f"{username}@example.com",
            "emailVerified": True,
            "requiredActions": [],
        }
        if attributes:
            payload["attributes"] = attributes
        user_id = kc_admin.create_user(payload)
        kc_admin.update_user(user_id=user_id, payload=payload)
    else:
        existing_user = kc_admin.get_user(user_id)
        existing_attributes = existing_user.get("attributes") or {}
        if attributes:
            existing_attributes.update(attributes)

        # Keycloak user updates are safest when the core profile is preserved.
        payload = {
            "username": existing_user.get("username") or username,
            "firstName": username.capitalize(),
            "lastName": "User",
            "email": email or existing_user.get("email") or f"{username}@example.com",
            "enabled": True,
            "emailVerified": True,
            "requiredActions": [],
        }
        if existing_attributes:
            payload["attributes"] = existing_attributes
        kc_admin.update_user(user_id=user_id, payload=payload)

    kc_admin.set_user_password(user_id=user_id, password=password, temporary=False)

    role_reps = [kc_admin.get_realm_role(role) for role in roles]
    kc_admin.assign_realm_roles(user_id=user_id, roles=role_reps)


if __name__ == "__main__":
    main()
