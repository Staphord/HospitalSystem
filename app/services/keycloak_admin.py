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


def _admin_api_url() -> str:
    return f"{settings.keycloak_url}/admin/realms/{settings.keycloak_realm}"


async def _headers() -> dict[str, str]:
    token = await _get_admin_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def create_keycloak_user(
    username: str,
    password: str,
    email: str,
    roles: list[str],
) -> str:
    hdrs = await _headers()
    url = f"{_admin_api_url()}/users"

    payload = {
        "username": username,
        "firstName": username.capitalize(),
        "lastName": "User",
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

    await set_user_password(user_id, password)
    await assign_user_roles(user_id, roles)
    return user_id


async def set_user_password(user_id: str, password: str) -> None:
    hdrs = await _headers()
    url = f"{_admin_api_url()}/users/{user_id}/reset-password"
    payload = {"type": "password", "value": password, "temporary": False}
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.put(url, json=payload, headers=hdrs)
        r.raise_for_status()


async def assign_user_roles(user_id: str, roles: list[str]) -> None:
    hdrs = await _headers()
    url = f"{_admin_api_url()}/users/{user_id}/role-mappings/realm"

    role_reps = []
    async with httpx.AsyncClient(timeout=10.0) as c:
        for role in roles:
            rr = await c.get(f"{_admin_api_url()}/roles/{role}", headers=hdrs)
            if rr.is_success:
                role_reps.append(rr.json())
            else:
                print(f"  WARNING: Role '{role}' not found in Keycloak")

        if role_reps:
            r = await c.post(url, json=role_reps, headers=hdrs)
            r.raise_for_status()


async def delete_keycloak_user(username: str) -> str | None:
    hdrs = await _headers()
    url = f"{_admin_api_url()}/users"
    async with httpx.AsyncClient(timeout=10.0) as c:
        search = await c.get(f"{url}?username={username}", headers=hdrs)
        search.raise_for_status()
        users = search.json()
        if not users:
            return None
        user_id = users[0]["id"]
        await c.delete(f"{url}/{user_id}", headers=hdrs)
        return user_id


async def ensure_roles(roles: list[str]) -> None:
    hdrs = await _headers()
    async with httpx.AsyncClient(timeout=10.0) as c:
        for role in roles:
            rr = await c.get(f"{_admin_api_url()}/roles/{role}", headers=hdrs)
            if rr.status_code == 404:
                await c.post(
                    f"{_admin_api_url()}/roles",
                    json={"name": role},
                    headers=hdrs,
                )


def create_local_user(db: Session, keycloak_sub: str, email: str, hospital_id: str | None) -> User:
    existing = db.query(User).filter(User.keycloak_sub == keycloak_sub).first()
    if existing:
        existing.email = email
        existing.hospital_id = hospital_id
        db.commit()
        db.refresh(existing)
        return existing
    user = User(keycloak_sub=keycloak_sub, email=email, hospital_id=hospital_id)
    db.add(user)
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
