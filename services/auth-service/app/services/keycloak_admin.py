from __future__ import annotations

import os
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User


async def _get_admin_token() -> str:
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


def _admin_api_url(realm: str | None = None) -> str:
    return f"{settings.keycloak_url}/admin/realms/{realm or settings.keycloak_realm}"


async def _headers() -> dict[str, str]:
    token = await _get_admin_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def find_user_realm_by_username(username: str) -> str | None:
    """Search for a user across ALL Keycloak realms."""
    hdrs = await _headers()
    # Try default realm and master first (fast path)
    fast_realms = []
    if settings.keycloak_realm:
        fast_realms.append(settings.keycloak_realm)
    if "master" not in fast_realms:
        fast_realms.insert(0, "master")

    async with httpx.AsyncClient(timeout=10.0) as client:
        for realm in fast_realms:
            url = f"{settings.keycloak_url}/admin/realms/{realm}/users"
            r = await client.get(f"{url}?username={username}&maxResults=1", headers=hdrs)
            if r.is_success and r.json():
                return realm

        # If not found, search ALL realms
        try:
            realms_url = f"{settings.keycloak_url}/admin/realms"
        except Exception:
            return None

    # Get all realm names
    all_realms = await _list_all_realms()
    async with httpx.AsyncClient(timeout=10.0) as client:
        for realm in all_realms:
            if realm in fast_realms:
                continue
            url = f"{settings.keycloak_url}/admin/realms/{realm}/users"
            r = await client.get(f"{url}?username={username}&maxResults=1", headers=hdrs)
            if r.is_success and r.json():
                return realm
    return None


async def _list_all_realms() -> list[str]:
    """Helper to list all Keycloak realm names."""
    hdrs = await _headers()
    url = f"{settings.keycloak_url}/admin/realms"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=hdrs)
        if r.is_success:
            return [rd["realm"] for rd in r.json()]
    return []


async def create_keycloak_user(
    username: str,
    password: str,
    email: str,
    roles: list[str],
    full_name: str | None = None,
    realm: str | None = None,
) -> str:
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/users"

    name_parts = (full_name or username).strip().split(None, 1)
    first_name = name_parts[0].capitalize() if name_parts else username.capitalize()
    last_name = name_parts[1].capitalize() if len(name_parts) > 1 else "User"

    payload = {
        "username": username,
        "firstName": first_name,
        "lastName": last_name,
        "enabled": True,
        "email": email,
        "emailVerified": True,
        "requiredActions": [],
    }

    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(url, json=payload, headers=hdrs)
        if r.status_code == 409:
            search = await c.get(f"{url}?username={username}", headers=hdrs)
            search.raise_for_status()
            users = search.json()
            if users:
                user_id = users[0]["id"]
                await c.put(f"{url}/{user_id}", json=payload, headers=hdrs)
                return user_id
            raise Exception(f"Conflict creating user '{username}' but could not find existing")
        r.raise_for_status()
        user_id = r.headers.get("location", "").rsplit("/", 1)[-1]
        if not user_id:
            search2 = await c.get(f"{url}?username={username}", headers=hdrs)
            search2.raise_for_status()
            users2 = search2.json()
            user_id = users2[0]["id"] if users2 else None

        # Explicitly clear any auto-added required actions (e.g. VERIFY_EMAIL)
        if user_id:
            await c.put(f"{url}/{user_id}", json=payload, headers=hdrs)

    await set_user_password(user_id, password, realm=realm)
    await assign_user_roles(user_id, roles, realm=realm)
    return user_id


async def set_user_password(user_id: str, password: str, realm: str | None = None) -> None:
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/users/{user_id}/reset-password"
    payload = {"type": "password", "value": password, "temporary": False}
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.put(url, json=payload, headers=hdrs)
        r.raise_for_status()


async def assign_user_roles(user_id: str, roles: list[str], realm: str | None = None) -> None:
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/users/{user_id}/role-mappings/realm"

    role_reps = []
    async with httpx.AsyncClient(timeout=10.0) as c:
        for role in roles:
            rr = await c.get(f"{_admin_api_url(realm)}/roles/{role}", headers=hdrs)
            if rr.is_success:
                role_reps.append(rr.json())
            else:
                print(f"  WARNING: Role '{role}' not found in Keycloak")

        if role_reps:
            r = await c.post(url, json=role_reps, headers=hdrs)
            r.raise_for_status()


async def update_keycloak_user(
    user_id: str,
    username: str | None = None,
    email: str | None = None,
    full_name: str | None = None,
    enabled: bool | None = None,
    realm: str | None = None,
) -> None:
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/users/{user_id}"
    async with httpx.AsyncClient(timeout=10.0) as c:
        existing = await c.get(url, headers=hdrs)
        existing.raise_for_status()
        data = existing.json()
        # Strip read-only fields that Keycloak rejects in PUT
        for key in ("id", "createdTimestamp", "realmRoles", "clientRoles", "notBefore"):
            data.pop(key, None)
        if username is not None:
            data["username"] = username
        if email is not None:
            data["email"] = email
        if full_name is not None:
            cleaned = full_name.strip()
            if cleaned:
                name_parts = cleaned.split(None, 1)
                data["firstName"] = name_parts[0].capitalize()
                data["lastName"] = name_parts[1].capitalize() if len(name_parts) > 1 else ""
            else:
                data.setdefault("firstName", "")
                data.setdefault("lastName", "")
        if enabled is not None:
            data["enabled"] = enabled
        r = await c.put(url, json=data, headers=hdrs)
        r.raise_for_status()


async def delete_keycloak_user(username: str, realm: str | None = None) -> str | None:
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/users"
    async with httpx.AsyncClient(timeout=10.0) as c:
        search = await c.get(f"{url}?username={username}", headers=hdrs)
        search.raise_for_status()
        users = search.json()
        if not users:
            return None
        user_id = users[0]["id"]
        await c.delete(f"{url}/{user_id}", headers=hdrs)
        return user_id


async def ensure_roles(roles: list[str], realm: str | None = None) -> None:
    hdrs = await _headers()
    async with httpx.AsyncClient(timeout=10.0) as c:
        for role in roles:
            rr = await c.get(f"{_admin_api_url(realm)}/roles/{role}", headers=hdrs)
            if rr.status_code == 404:
                await c.post(
                    f"{_admin_api_url(realm)}/roles",
                    json={"name": role},
                    headers=hdrs,
                )


async def set_user_attribute(user_id: str, attr_name: str, attr_value: str, realm: str | None = None) -> None:
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/users/{user_id}"
    async with httpx.AsyncClient(timeout=10.0) as c:
        u = await c.get(url, headers=hdrs)
        u.raise_for_status()
        user_data = u.json()
        payload = {
            "username": user_data.get("username"),
            "email": user_data.get("email"),
            "firstName": user_data.get("firstName", ""),
            "lastName": user_data.get("lastName", ""),
            "enabled": user_data.get("enabled", True),
            "emailVerified": user_data.get("emailVerified", True),
            "requiredActions": user_data.get("requiredActions", []),
            "attributes": {attr_name: [attr_value]},
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        r = await c.put(url, json=payload, headers=hdrs)
        r.raise_for_status()


def create_local_user(
    db: Session,
    keycloak_sub: str,
    email: str,
    hospital_id: str | None,
    username: str | None = None,
    full_name: str | None = None,
    role: str | None = None,
) -> User:
    existing = db.query(User).filter(User.keycloak_sub == keycloak_sub).first()
    if existing:
        existing.username = username or existing.username
        existing.full_name = full_name or existing.full_name
        existing.email = email
        existing.role = role or existing.role
        existing.hospital_id = hospital_id
        db.commit()
        db.refresh(existing)
        return existing
    user = User(
        keycloak_sub=keycloak_sub,
        username=username,
        full_name=full_name,
        email=email,
        role=role,
        hospital_id=hospital_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_local_users_by_hospital(db: Session, hospital_id: str) -> list[User]:
    return db.query(User).filter(User.hospital_id == hospital_id).all()


def update_local_user(
    db: Session,
    keycloak_sub: str,
    username: str | None = None,
    full_name: str | None = None,
    email: str | None = None,
    role: str | None = None,
    hospital_id: str | None = None,
) -> User | None:
    user = db.query(User).filter(User.keycloak_sub == keycloak_sub).first()
    if not user:
        return None
    if username is not None:
        user.username = username
    if full_name is not None:
        user.full_name = full_name
    if email is not None:
        user.email = email
    if role is not None:
        user.role = role
    if hospital_id is not None:
        user.hospital_id = hospital_id
    db.commit()
    db.refresh(user)
    return user


def delete_local_user(db: Session, keycloak_sub: str) -> bool:
    user = db.query(User).filter(User.keycloak_sub == keycloak_sub).first()
    if not user:
        return False
    db.delete(user)
    db.commit()
    return True
