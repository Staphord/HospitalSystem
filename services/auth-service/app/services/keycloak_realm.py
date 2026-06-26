from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TENANT_REALM_ROLES = [
    "hospital_admin",
    "hospital_user",
    "nurse",
    "doctor",
    "clinician",
    "patient",
]


def get_realm_url(realm: str) -> str:
    return f"{settings.keycloak_url}/realms/{realm}"


def get_realm_admin_url(realm: str) -> str:
    return f"{settings.keycloak_url}/admin/realms/{realm}"


def get_realm_token_url(realm: str) -> str:
    return f"{settings.keycloak_url}/realms/{realm}/protocol/openid-connect/token"


async def _get_master_admin_token() -> str:
    data = {
        "grant_type": "password",
        "client_id": "admin-cli",
        "username": settings.keycloak_admin_username,
        "password": settings.keycloak_admin_password,
    }
    url = f"{settings.keycloak_url}/realms/master/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, data=data)
        r.raise_for_status()
        return r.json()["access_token"]


async def _admin_headers() -> dict[str, str]:
    token = await _get_master_admin_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def create_tenant_realm(realm: str) -> None:
    """Create a new Keycloak realm for a tenant via the master Admin API."""
    hdrs = await _admin_headers()
    url = f"{settings.keycloak_url}/admin/realms"

    payload = {
        "realm": realm,
        "enabled": True,
        "registrationAllowed": False,
        "loginTheme": "keycloak",
        "displayName": realm,
        "displayNameHtml": f"<div class='kc-logo-text'><span>{realm}</span></div>",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=payload, headers=hdrs)
        if r.status_code == 201:
            logger.info("Created Keycloak realm: %s", realm)
        elif r.status_code == 409:
            logger.info("Realm %s already exists", realm)
        else:
            r.raise_for_status()


async def create_realm_client(realm: str) -> None:
    """Create the hospital-api client inside a tenant realm."""
    from app.core.config import settings
    hdrs = await _admin_headers()
    clients_url = f"{get_realm_admin_url(realm)}/clients"

    payload = {
        "clientId": "hospital-api",
        "name": "hospital-api",
        "enabled": True,
        "publicClient": False,
        "redirectUris": ["http://localhost:8080/*"],
        "webOrigins": ["http://localhost:8080"],
        "directAccessGrantsEnabled": True,
        "serviceAccountsEnabled": False,
        "standardFlowEnabled": True,
        "protocol": "openid-connect",
        "secret": settings.keycloak_client_secret,
        "fullScopeAllowed": True,
    }

    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(clients_url, json=payload, headers=hdrs)
        if r.status_code == 201:
            logger.info("Created client hospital-api in realm %s", realm)
        elif r.status_code == 409:
            logger.info("Client hospital-api already exists in realm %s", realm)
            return
        else:
            r.raise_for_status()

        # Resolve client UUID from location header or search
        client_uuid = r.headers.get("location", "").rsplit("/", 1)[-1]
        if not client_uuid:
            search = await c.get(f"{clients_url}?clientId=hospital-api", headers=hdrs)
            search.raise_for_status()
            items = search.json()
            if items:
                client_uuid = items[0].get("id")
        if not client_uuid:
            logger.warning("Could not resolve client UUID for realm %s", realm)
            return

        # Add protocol mappers so tenant_id, email etc. appear in tokens
        mappers_url = f"{clients_url}/{client_uuid}/protocol-mappers/models"
        existing_mappers = await c.get(mappers_url, headers=hdrs)
        existing_mappers.raise_for_status()
        existing_names = {m["name"] for m in existing_mappers.json()}

        tenant_mapper = {
            "name": "tenant_id",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {
                "user.attribute": "tenant_id",
                "claim.name": "tenant_id",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
                "json.type.label": "String",
            },
        }

        email_mapper = {
            "name": "email",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {
                "user.attribute": "email",
                "claim.name": "email",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
                "json.type.label": "String",
            },
        }

        for mapper in (tenant_mapper, email_mapper):
            if mapper["name"] not in existing_names:
                mr = await c.post(mappers_url, json=mapper, headers=hdrs)
                if mr.is_success:
                    logger.info("Added mapper '%s' to client in realm %s", mapper["name"], realm)
                else:
                    logger.warning(
                        "Failed to add mapper '%s' in realm %s: %s",
                        mapper["name"], realm, mr.status_code,
                    )


async def ensure_realm_roles(realm: str, roles: list[str] | None = None) -> None:
    """Create realm roles inside a tenant realm."""
    if roles is None:
        roles = TENANT_REALM_ROLES
    hdrs = await _admin_headers()
    async with httpx.AsyncClient(timeout=10.0) as client:
        for role in roles:
            rr = await client.get(
                f"{get_realm_admin_url(realm)}/roles/{role}", headers=hdrs
            )
            if rr.status_code == 404:
                r = await client.post(
                    f"{get_realm_admin_url(realm)}/roles",
                    json={"name": role},
                    headers=hdrs,
                )
                if r.status_code == 201:
                    logger.info("Created role %s in realm %s", role, realm)
                elif r.status_code == 409:
                    logger.info("Role %s already exists in realm %s", role, realm)
                else:
                    r.raise_for_status()


async def add_tenant_id_to_user_profile(realm: str) -> None:
    """Register tenant_id as a user profile attribute in the realm (Keycloak 26+)."""
    hdrs = await _admin_headers()
    url = f"{get_realm_admin_url(realm)}/users/profile"
    async with httpx.AsyncClient(timeout=15.0) as client:
        # GET current profile
        r = await client.get(url, headers=hdrs)
        if not r.is_success:
            logger.warning("Cannot read user profile for realm %s (status %s)", realm, r.status_code)
            return
        profile = r.json()
        attributes = profile.get("attributes", [])
        existing_names = {a["name"] for a in attributes}

        if "tenant_id" not in existing_names:
            attributes.append({
                "name": "tenant_id",
                "displayName": "Tenant ID",
                "validations": {},
                "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]},
                "multivalued": False,
            })
            profile["attributes"] = attributes
            pr = await client.put(url, json=profile, headers=hdrs)
            if pr.is_success:
                logger.info("Added tenant_id to user profile in realm %s", realm)
            else:
                logger.warning(
                    "Failed to add tenant_id to user profile in realm %s: %s",
                    realm, pr.status_code,
                )


async def delete_tenant_realm(realm: str) -> None:
    """Delete a Keycloak realm via the master Admin API."""
    hdrs = await _admin_headers()
    url = f"{settings.keycloak_url}/admin/realms/{realm}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.delete(url, headers=hdrs)
        if r.status_code == 204:
            logger.info("Deleted Keycloak realm: %s", realm)
        elif r.status_code == 404:
            logger.info("Realm %s not found for deletion", realm)
        else:
            r.raise_for_status()


async def set_realm_token_lifespan(realm: str, lifespan_seconds: int = 300) -> None:
    """Set the Keycloak realm's access token lifespan."""
    hdrs = await _admin_headers()
    url = f"{settings.keycloak_url}/admin/realms/{realm}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=hdrs)
        if not r.is_success:
            logger.warning("Cannot read realm %s config (status %s)", realm, r.status_code)
            return
        config = r.json()
        current = config.get("accessTokenLifespan", 0)
        if current == lifespan_seconds:
            return
        config["accessTokenLifespan"] = lifespan_seconds
        pr = await client.put(url, json=config, headers=hdrs)
        if pr.is_success:
            logger.info("Set accessTokenLifespan to %s for realm %s", lifespan_seconds, realm)
        else:
            logger.warning("Failed to set token lifespan for realm %s: %s", realm, pr.status_code)
async def setup_tenant_realm(realm: str) -> None:
    """Idempotently create realm, client, and roles for a tenant."""
    await create_tenant_realm(realm)
    await create_realm_client(realm)
    await ensure_realm_roles(realm)
    await add_tenant_id_to_user_profile(realm)
    await set_realm_token_lifespan(realm, settings.keycloak_access_token_lifespan)


async def list_all_realms() -> list[str]:
    """List all Keycloak realm names via the master Admin API."""
    hdrs = await _admin_headers()
    url = f"{settings.keycloak_url}/admin/realms"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=hdrs)
        r.raise_for_status()
        return [realm_data["realm"] for realm_data in r.json()]


async def verify_tenant_realm_exists(realm: str) -> bool:
    """Check if a Keycloak realm actually exists."""
    hdrs = await _admin_headers()
    url = f"{settings.keycloak_url}/admin/realms/{realm}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, headers=hdrs)
        return r.is_success


async def get_realm_roles(realm: str) -> list[dict]:
    """Get all realm-level roles for a given Keycloak realm."""
    hdrs = await _admin_headers()
    url = f"{get_realm_admin_url(realm)}/roles"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, headers=hdrs)
        r.raise_for_status()
        return r.json()


async def create_realm_role(realm: str, name: str, description: str = "") -> None:
    """Create a realm-level role in the given Keycloak realm."""
    hdrs = await _admin_headers()
    url = f"{get_realm_admin_url(realm)}/roles"
    payload = {"name": name}
    if description:
        payload["description"] = description
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, json=payload, headers=hdrs)
        if r.status_code == 409:
            logger.info("Role %s already exists in realm %s", name, realm)
        elif not r.is_success:
            r.raise_for_status()


async def update_realm_role(realm: str, name: str, new_name: str | None = None, description: str | None = None) -> None:
    """Update a realm-level role in the given Keycloak realm."""
    hdrs = await _admin_headers()
    url = f"{get_realm_admin_url(realm)}/roles/{name}"
    payload: dict[str, str] = {}
    if new_name:
        payload["name"] = new_name
    if description is not None:
        payload["description"] = description
    if not payload:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.put(url, json=payload, headers=hdrs)
        r.raise_for_status()


async def delete_realm_role(realm: str, name: str) -> None:
    """Delete a realm-level role from the given Keycloak realm."""
    hdrs = await _admin_headers()
    url = f"{get_realm_admin_url(realm)}/roles/{name}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.delete(url, headers=hdrs)
        if r.status_code == 404:
            logger.info("Role %s not found in realm %s", name, realm)
        else:
            r.raise_for_status()


async def get_all_realm_users(realm: str) -> list[dict]:
    """List all users in a given Keycloak realm."""
    hdrs = await _admin_headers()
    url = f"{get_realm_admin_url(realm)}/users"
    users = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        first = 0
        while True:
            r = await client.get(url, headers=hdrs, params={"first": first, "max": 100})
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            users.extend(batch)
            first += len(batch)
    return users
