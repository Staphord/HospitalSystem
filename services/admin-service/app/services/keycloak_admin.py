from __future__ import annotations

import os
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
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
            # 409 can be username OR email. Username-only search often returns []
            # when the clash is on email under a different username.
            user_id = None
            for params in (
                {"username": username, "exact": "true"},
                {"email": email, "exact": "true"},
            ):
                search = await c.get(url, params=params, headers=hdrs)
                search.raise_for_status()
                users = search.json()
                if users:
                    user_id = users[0]["id"]
                    break
            if not user_id:
                raise Exception(
                    f"Conflict creating user '{username}': username or email already "
                    f"exists in Keycloak (could not resolve existing user). "
                    f"Try a different username and email."
                )
            await c.put(f"{url}/{user_id}", json=payload, headers=hdrs)
            await set_user_password(user_id, password, realm=realm)
            await assign_user_roles(user_id, roles, realm=realm)
            return user_id
        r.raise_for_status()
        user_id = r.headers.get("location", "").rsplit("/", 1)[-1]
        if not user_id:
            search2 = await c.get(url, params={"username": username, "exact": "true"}, headers=hdrs)
            search2.raise_for_status()
            users2 = search2.json()
            user_id = users2[0]["id"] if users2 else None

        # Explicitly clear any auto-added required actions (e.g. VERIFY_EMAIL)
        if user_id:
            await c.put(f"{url}/{user_id}", json=payload, headers=hdrs)

    await set_user_password(user_id, password, realm=realm)
    await assign_user_roles(user_id, roles, realm=realm)
    return user_id


async def set_user_password(
    user_id: str,
    password: str,
    realm: str | None = None,
    *,
    temporary: bool = False,
) -> None:
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/users/{user_id}/reset-password"
    payload = {"type": "password", "value": password, "temporary": temporary}
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.put(url, json=payload, headers=hdrs)
        r.raise_for_status()


async def get_user_realm_roles(user_id: str, realm: str | None = None) -> list[dict]:
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/users/{user_id}/role-mappings/realm"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(url, headers=hdrs)
        r.raise_for_status()
        return r.json()


async def remove_user_roles(user_id: str, roles: list[dict], realm: str | None = None) -> None:
    if not roles:
        return
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/users/{user_id}/role-mappings/realm"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.request("DELETE", url, json=roles, headers=hdrs)
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


async def replace_user_roles(user_id: str, roles: list[str], realm: str | None = None) -> None:
    """Remove existing realm roles (except built-in default roles) and assign the target set."""
    current = await get_user_realm_roles(user_id, realm=realm)
    removable = [
        r
        for r in current
        if not str(r.get("name", "")).startswith("default-roles-")
        and r.get("name") not in ("offline_access", "uma_authorization")
    ]
    await remove_user_roles(user_id, removable, realm=realm)
    await assign_user_roles(user_id, roles, realm=realm)


async def logout_user_sessions(user_id: str, realm: str | None = None) -> None:
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/users/{user_id}/logout"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(url, headers=hdrs)
        if r.status_code not in (204, 200):
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


async def get_realm_roles(realm: str) -> list[dict]:
    """List all realm-level roles in a specific Keycloak realm."""
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/roles"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(url, headers=hdrs)
        r.raise_for_status()
        return r.json()


async def create_realm_role(realm: str, role_name: str) -> dict:
    """Create a new realm-level role in a specific Keycloak realm."""
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/roles"
    payload = {"name": role_name}
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(url, json=payload, headers=hdrs)
        if r.status_code == 409:
            raise Exception(f"Role '{role_name}' already exists in realm '{realm}'")
        r.raise_for_status()
        # Fetch the created role to return its representation
        rr = await c.get(f"{url}/{role_name}", headers=hdrs)
        rr.raise_for_status()
        return rr.json()


async def update_realm_role(realm: str, role_name: str, new_name: str) -> None:
    """Update a realm-level role name in a specific Keycloak realm."""
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/roles/{role_name}"
    async with httpx.AsyncClient(timeout=10.0) as c:
        existing = await c.get(url, headers=hdrs)
        if existing.status_code == 404:
            raise Exception(f"Role '{role_name}' not found in realm '{realm}'")
        data = existing.json()
        data["name"] = new_name
        r = await c.put(url, json=data, headers=hdrs)
        r.raise_for_status()


async def delete_realm_role(realm: str, role_name: str) -> None:
    """Delete a realm-level role from a specific Keycloak realm."""
    hdrs = await _headers()
    url = f"{_admin_api_url(realm)}/roles/{role_name}"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.delete(url, headers=hdrs)
        if r.status_code == 404:
            raise Exception(f"Role '{role_name}' not found in realm '{realm}'")
        r.raise_for_status()


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
    is_active: bool = True,
    force_password_change: bool = False,
    department_id=None,
    phone: str | None = None,
    password_expires_at=None,
) -> User:
    existing = db.query(User).filter(User.keycloak_sub == keycloak_sub).first()
    if existing:
        existing.username = username or existing.username
        existing.full_name = full_name or existing.full_name
        existing.email = email
        existing.role = role or existing.role
        existing.hospital_id = hospital_id
        if is_active is not None:
            existing.is_active = is_active
        if force_password_change is not None:
            existing.force_password_change = force_password_change
        if department_id is not None:
            existing.department_id = department_id
        if phone is not None:
            existing.phone = phone
        if password_expires_at is not None:
            existing.password_expires_at = password_expires_at
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
        is_active=is_active,
        force_password_change=force_password_change,
        department_id=department_id,
        phone=phone,
        password_expires_at=password_expires_at,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_local_users_by_hospital(
    db: Session,
    hospital_id: str,
    *,
    include_deleted: bool = False,
) -> list[User]:
    q = db.query(User).filter(User.hospital_id == hospital_id)
    if not include_deleted:
        q = q.filter(User.deleted_at.is_(None))
    return q.all()


def update_local_user(
    db: Session,
    keycloak_sub: str,
    username: str | None = None,
    full_name: str | None = None,
    email: str | None = None,
    role: str | None = None,
    hospital_id: str | None = None,
    is_active: bool | None = None,
    force_password_change: bool | None = None,
    department_id=None,
    phone: str | None = None,
    password_expires_at=None,
    deleted_at=None,
    clear_deleted: bool = False,
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
    if is_active is not None:
        user.is_active = is_active
    if force_password_change is not None:
        user.force_password_change = force_password_change
    if department_id is not None:
        user.department_id = department_id
    if phone is not None:
        user.phone = phone
    if password_expires_at is not None:
        user.password_expires_at = password_expires_at
    if deleted_at is not None:
        user.deleted_at = deleted_at
    if clear_deleted:
        user.deleted_at = None
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
