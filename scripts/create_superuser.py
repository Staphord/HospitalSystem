"""
Create a super admin or hospital admin user in both Keycloak and the local DB.

Usage:
    venv\Scripts\python.exe scripts\create_superuser.py --username=superadmin --password=Str0ng!Pass --email=admin@hospital.com --role=super_admin
    venv\Scripts\python.exe scripts\create_superuser.py --username=hospitaladmin --password=Str0ng!Pass --email=admin@hosp1.com --role=hospital_admin --hospital-id=hosp-001
"""
import argparse
import asyncio
import os
import secrets
import sys
import uuid

import bcrypt
import httpx
from dotenv import load_dotenv
from keycloak import KeycloakAdmin
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "hospital-realm")
KEYCLOAK_ADMIN_USER = os.getenv("KEYCLOAK_ADMIN_USERNAME", "admin")
KEYCLOAK_ADMIN_PASS = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
DATABASE_URL = os.getenv("DATABASE_URL")


def _get_db() -> Session:
    engine = create_engine(DATABASE_URL)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return factory()


def _get_kc_admin() -> KeycloakAdmin:
    import requests
    try:
        requests.get(f"{KEYCLOAK_URL}/.well-known/openid-configuration", timeout=3)
    except requests.ConnectionError:
        print(f"ERROR: Cannot reach Keycloak at {KEYCLOAK_URL}")
        print("Start it with: docker-compose up -d keycloak keycloak-db")
        sys.exit(1)
    return KeycloakAdmin(
        server_url=KEYCLOAK_URL,
        username=KEYCLOAK_ADMIN_USER,
        password=KEYCLOAK_ADMIN_PASS,
        realm_name="master",
        verify=False,
    )


def _ensure_realm_and_roles(kc_admin: KeycloakAdmin) -> None:
    if KEYCLOAK_REALM not in [r["realm"] for r in kc_admin.get_realms()]:
        print(f"Creating realm '{KEYCLOAK_REALM}'...")
        kc_admin.create_realm(payload={"realm": KEYCLOAK_REALM, "enabled": True})

    kc_admin.realm_name = KEYCLOAK_REALM
    kc_admin.connection.realm_name = KEYCLOAK_REALM  # get_users reads this

    for role_name in ("hospital_user", "hospital_admin", "super_admin"):
        try:
            kc_admin.get_realm_role(role_name)
            print(f"  Role '{role_name}' already exists")
        except Exception:
            print(f"  Creating role '{role_name}'...")
            kc_admin.create_realm_role(payload={"name": role_name})

    _ensure_client(kc_admin)


def _ensure_client(kc_admin: KeycloakAdmin) -> None:
    asyncio.run(_ensure_client_async(kc_admin))


async def _ensure_client_async(kc_admin: KeycloakAdmin) -> None:

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

        if existing:
            client_uuid = existing[0]["id"]
            print(f"  Client '{client_id}' already exists (UUID: {client_uuid})")
        else:
            payload = {
                "clientId": client_id,
                "name": "Hospital API",
                "protocol": "openid-connect",
                "publicClient": False,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": True,
                "serviceAccountsEnabled": False,
                "redirectUris": [f"{keycloak_url}/*"],
                "webOrigins": ["+"],
            }
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
            print(f"  Created client '{client_id}' (UUID: {client_uuid})")


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


def create_user(
    username: str,
    password: str,
    email: str,
    roles: list[str],
    hospital_id: str | None,
) -> None:
    print(f"\nCreating user '{username}'...")

    kc_admin = _get_kc_admin()
    _ensure_realm_and_roles(kc_admin)

    user_id = kc_admin.get_user_id(username)
    if user_id:
        # Fetch existing user first to preserve all fields (PUT = full replacement)
        existing_user = kc_admin.get_user(user_id)
        update_payload = {
            "firstName": username.capitalize(),
            "lastName": "User",
            "email": existing_user.get("email", email),
            "emailVerified": True,
            "enabled": existing_user.get("enabled", True),
            "requiredActions": [],
        }
        if hospital_id:
            update_payload["attributes"] = {"tenant_id": [hospital_id]}
        kc_admin.update_user(user_id=user_id, payload=update_payload)
        print(f"  User '{username}' already exists in Keycloak (ID: {user_id})")
    else:
        create_payload = {
            "username": username,
            "firstName": username.capitalize(),
            "lastName": "User",
            "enabled": True,
            "email": email,
            "emailVerified": True,
            "requiredActions": [],
        }
        if hospital_id:
            create_payload["attributes"] = {"tenant_id": [hospital_id]}
        user_id = kc_admin.create_user(create_payload)
        # Explicitly clear any auto-added required actions
        kc_admin.update_user(user_id=user_id, payload=create_payload)
        print(f"  Created Keycloak user (ID: {user_id})")

    kc_admin.set_user_password(user_id=user_id, password=password, temporary=False)

    role_reps = []
    for role in roles:
        try:
            role_reps.append(kc_admin.get_realm_role(role))
        except Exception:
            print(f"  WARNING: Role '{role}' not found in Keycloak, skipping")
    if role_reps:
        kc_admin.assign_realm_roles(user_id=user_id, roles=role_reps)
        print(f"  Assigned roles: {roles}")

    db = _get_db()
    try:
        if "super_admin" not in roles:
            if not hospital_id:
                raise ValueError("hospital_id is required for hospital-level roles")

            primary_role = roles[0]
            full_name = username.capitalize() + " User"

            existing = db.execute(
                text("SELECT id FROM users WHERE keycloak_sub = :sub"),
                {"sub": user_id},
            ).scalar()

            if existing:
                db.execute(
                    text(
                        "UPDATE users SET "
                        "username = :username, "
                        "full_name = :full_name, "
                        "email = :email, "
                        "role = :role, "
                        "hospital_id = :hid "
                        "WHERE id = :id"
                    ),
                    {
                        "username": username,
                        "full_name": full_name,
                        "email": email,
                        "role": primary_role,
                        "hid": hospital_id,
                        "id": existing,
                    },
                )
                print(f"  Updated local user record (ID: {existing})")
            else:
                db.execute(
                    text(
                        "INSERT INTO users (keycloak_sub, username, full_name, email, role, hospital_id) "
                        "VALUES (:sub, :username, :full_name, :email, :role, :hid)"
                    ),
                    {
                        "sub": user_id,
                        "username": username,
                        "full_name": full_name,
                        "email": email,
                        "role": primary_role,
                        "hid": hospital_id,
                    },
                )
                print(f"  Created local user record for hospital: {hospital_id}")

        # Insert into super_admins table for super_admin role
        if "super_admin" in roles:
            password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
            mfa_secret = secrets.token_hex(16)
            full_name = username.capitalize() + " User"
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            db.execute(
                text(
                    """
                    INSERT INTO super_admins (super_admin_id, username, email, password_hash, full_name, role, mfa_secret, is_active, created_at)
                    VALUES (:super_admin_id, :username, :email, :password_hash, :full_name, :role, :mfa_secret, true, :created_at)
                    ON CONFLICT (username) DO UPDATE SET
                        email = EXCLUDED.email,
                        password_hash = EXCLUDED.password_hash,
                        full_name = EXCLUDED.full_name,
                        role = EXCLUDED.role,
                        mfa_secret = EXCLUDED.mfa_secret,
                        is_active = true
                    """
                ),
                {
                    "super_admin_id": str(uuid.uuid4()),
                    "username": username,
                    "email": email,
                    "password_hash": password_hash,
                    "full_name": full_name,
                    "role": "super_admin",
                    "mfa_secret": mfa_secret,
                    "created_at": now,
                },
            )
            print(f"  Created/updated super_admins record for '{username}'")

        db.commit()
    finally:
        db.close()

    print(f"\n[OK] User '{username}' is ready.")
    print(f"   Keycloak ID: {user_id}")
    print(f"   Roles: {roles}")
    print(f"   Hospital: {hospital_id or 'ALL (super_admin)'}")


def delete_user(username: str) -> None:
    kc_admin = _get_kc_admin()
    realm_name = os.getenv("KEYCLOAK_REALM", "hospital-realm")
    kc_admin.realm_name = realm_name
    kc_admin.connection.realm_name = realm_name

    user_id = kc_admin.get_user_id(username)
    if not user_id:
        print(f"User '{username}' not found in Keycloak")
        return

    kc_admin.delete_user(user_id)
    print(f"Deleted user '{username}' from Keycloak (ID: {user_id})")

    db = _get_db()
    try:
        result = db.execute(
            text("DELETE FROM users WHERE keycloak_sub = :sub"),
            {"sub": user_id},
        )
        db.execute(
            text("DELETE FROM super_admins WHERE username = :username"),
            {"username": username},
        )
        db.commit()
        if result.rowcount:
            print(f"Deleted local user record for '{username}'")
        print(f"Deleted super_admins record for '{username}' if present")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or delete Keycloak users")
    parser.add_argument("--username", required=True, help="Login username")
    parser.add_argument("--password", default=None, help="Login password (omit with --delete)")
    parser.add_argument("--email", default=None, help="Email address")
    parser.add_argument(
        "--role",
        default="hospital_admin",
        choices=["hospital_user", "hospital_admin", "super_admin"],
        help="Keycloak realm role to assign",
    )
    parser.add_argument("--hospital-id", default=None, help="Hospital ID (omit for super_admin)")
    parser.add_argument("--delete", action="store_true", help="Delete user instead of creating")

    args = parser.parse_args()

    if args.delete:
        delete_user(username=args.username)
        return

    if not args.password:
        parser.error("--password is required for user creation")

    if args.role != "super_admin" and not args.hospital_id:
        parser.error("--hospital-id is required for hospital-level roles (hospital_admin, hospital_user)")

    if args.role == "super_admin":
        roles = ["super_admin", "hospital_admin"]
    elif args.role == "hospital_admin":
        roles = ["hospital_admin", "hospital_user"]
    else:
        roles = ["hospital_user"]

    email = args.email or f"{args.username}@example.com"

    create_user(
        username=args.username,
        password=args.password,
        email=email,
        roles=roles,
        hospital_id=args.hospital_id,
    )


if __name__ == "__main__":
    main()
