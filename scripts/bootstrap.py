#!/usr/bin/env python3
"""Provision the one initial platform superadmin from SUPERADMIN_* settings.

The environment file is the source of truth. This script synchronizes that
single account to Keycloak's master realm and the master database. It is safe
to run repeatedly.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import uuid
from pathlib import Path

import bcrypt
import httpx
import pyotp
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


MASTER_REALM = "master"
SUPERADMIN_ROLE = "super_admin"
SUPERADMIN_CLIENT_ID = "superadmin-login"


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set in the environment file")
    return value


def configured_superadmin() -> dict[str, str]:
    return {
        "username": required("SUPERADMIN_USERNAME"),
        "email": required("SUPERADMIN_EMAIL"),
        "password": required("SUPERADMIN_PASSWORD"),
        "full_name": required("SUPERADMIN_FULL_NAME"),
    }


def persist_superadmin(env_file: Path, config: dict[str, str]) -> None:
    if not env_file.is_file():
        raise RuntimeError(f"Environment file does not exist: {env_file}")
    replacements = {
        "SUPERADMIN_USERNAME": config["username"],
        "SUPERADMIN_EMAIL": config["email"],
        "SUPERADMIN_PASSWORD": config["password"],
        "SUPERADMIN_FULL_NAME": config["full_name"],
    }
    seen: set[str] = set()
    result: list[str] = []
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in replacements:
            result.append(f"{key}={replacements[key]}")
            seen.add(key)
        else:
            result.append(line)
    for key, value in replacements.items():
        if key not in seen:
            result.append(f"{key}={value}")
    env_file.write_text("\n".join(result) + "\n", encoding="utf-8")


def interactive_superadmin(env_file: Path) -> dict[str, str]:
    current = configured_superadmin()
    print("Configure the initial platform superadmin. Press Enter to retain the configured value.")
    selected = {
        "username": input(f"Username [{current['username']}]: ").strip() or current["username"],
        "email": input(f"Email [{current['email']}]: ").strip() or current["email"],
        "full_name": input(f"Full name [{current['full_name']}]: ").strip() or current["full_name"],
        "password": getpass.getpass("Password [configured value]: ") or current["password"],
    }
    persist_superadmin(env_file, selected)
    return selected


async def admin_headers(client: httpx.AsyncClient, keycloak_url: str) -> dict[str, str]:
    response = await client.post(
        f"{keycloak_url}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": required("KEYCLOAK_ADMIN_USERNAME"),
            "password": required("KEYCLOAK_ADMIN_PASSWORD"),
        },
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}", "Content-Type": "application/json"}


async def ensure_keycloak_superadmin(config: dict[str, str]) -> str:
    keycloak_url = required("KEYCLOAK_URL").rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as client:
        headers = await admin_headers(client, keycloak_url)
        base = f"{keycloak_url}/admin/realms/{MASTER_REALM}"

        clients = await client.get(f"{base}/clients", params={"clientId": SUPERADMIN_CLIENT_ID}, headers=headers)
        clients.raise_for_status()
        if not clients.json():
            created = await client.post(
                f"{base}/clients",
                headers=headers,
                json={
                    "clientId": SUPERADMIN_CLIENT_ID,
                    "enabled": True,
                    "publicClient": True,
                    "directAccessGrantsEnabled": True,
                    "standardFlowEnabled": False,
                    "serviceAccountsEnabled": False,
                    "protocol": "openid-connect",
                },
            )
            created.raise_for_status()

        role_response = await client.get(f"{base}/roles/{SUPERADMIN_ROLE}", headers=headers)
        if role_response.status_code == 404:
            created = await client.post(f"{base}/roles", headers=headers, json={"name": SUPERADMIN_ROLE})
            created.raise_for_status()
            role_response = await client.get(f"{base}/roles/{SUPERADMIN_ROLE}", headers=headers)
        role_response.raise_for_status()
        role = role_response.json()

        users_url = f"{base}/users"
        found = await client.get(users_url, params={"username": config["username"], "exact": "true"}, headers=headers)
        found.raise_for_status()
        name = config["full_name"].split(None, 1)
        payload = {
            "username": config["username"],
            "email": config["email"],
            "firstName": name[0] if name else config["username"],
            "lastName": name[1] if len(name) > 1 else "",
            "enabled": True,
            "emailVerified": True,
            "requiredActions": [],
        }
        if found.json():
            user_id = found.json()[0]["id"]
            updated = await client.put(f"{users_url}/{user_id}", headers=headers, json=payload)
            updated.raise_for_status()
        else:
            created = await client.post(users_url, headers=headers, json=payload)
            created.raise_for_status()
            user_id = created.headers.get("Location", "").rstrip("/").split("/")[-1]
            if not user_id:
                retry = await client.get(users_url, params={"username": config["username"], "exact": "true"}, headers=headers)
                retry.raise_for_status()
                user_id = retry.json()[0]["id"]

        password = await client.put(
            f"{users_url}/{user_id}/reset-password",
            headers=headers,
            json={"type": "password", "value": config["password"], "temporary": False},
        )
        password.raise_for_status()
        assignment = await client.post(f"{users_url}/{user_id}/role-mappings/realm", headers=headers, json=[role])
        assignment.raise_for_status()
        return user_id


def ensure_database_superadmin(config: dict[str, str], keycloak_sub: str) -> None:
    try:
        keycloak_id = str(uuid.UUID(keycloak_sub))
    except ValueError as exc:
        raise RuntimeError(f"Keycloak returned an invalid user ID: {keycloak_sub}") from exc

    engine = create_engine(required("DATABASE_URL"), pool_pre_ping=True)
    password_hash = bcrypt.hashpw(config["password"].encode(), bcrypt.gensalt()).decode()
    try:
        with engine.begin() as connection:
            existing = connection.execute(
                text("SELECT super_admin_id FROM super_admins WHERE username = :username OR email = :email LIMIT 1"),
                {"username": config["username"], "email": config["email"]},
            ).scalar()
            if existing and str(existing) != keycloak_id:
                raise RuntimeError(
                    "A different local superadmin already uses this username or email. "
                    "Resolve that account before changing the bootstrap identity."
                )
            connection.execute(
                text(
                    """
                    INSERT INTO super_admins (
                        super_admin_id, username, email, password_hash, full_name,
                        role, mfa_secret, mfa_enabled, is_active, created_at
                    ) VALUES (
                        :id, :username, :email, :password_hash, :full_name,
                        'super_admin', :mfa_secret, false, true, NOW()
                    )
                    ON CONFLICT (super_admin_id) DO UPDATE SET
                        username = EXCLUDED.username,
                        email = EXCLUDED.email,
                        password_hash = EXCLUDED.password_hash,
                        full_name = EXCLUDED.full_name,
                        role = 'super_admin',
                        is_active = true
                    """
                ),
                {
                    "id": keycloak_id,
                    "username": config["username"],
                    "email": config["email"],
                    "password_hash": password_hash,
                    "full_name": config["full_name"],
                    "mfa_secret": pyotp.random_base32(),
                },
            )
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision the initial platform superadmin")
    parser.add_argument("--interactive", action="store_true", help="prompt and persist values to --env-file")
    parser.add_argument("--env-file", type=Path, help="canonical .env/common.env file for interactive mode")
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    else:
        load_dotenv(override=False)
    if args.interactive:
        if not args.env_file:
            parser.error("--interactive requires --env-file")
        config = interactive_superadmin(args.env_file)
    else:
        config = configured_superadmin()
    keycloak_sub = asyncio.run(ensure_keycloak_superadmin(config))
    ensure_database_superadmin(config, keycloak_sub)
    print(f"Bootstrap complete. Superadmin '{config['username']}' is ready.")


if __name__ == "__main__":
    main()
