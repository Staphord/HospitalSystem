"""Fix existing Keycloak users that have pending required actions.

Usage:
    .\venv\Scripts\python.exe scripts\fix_required_actions.py <username>
    .\venv\Scripts\python.exe scripts\fix_required_actions.py --all
"""
import argparse
import os

from keycloak import KeycloakAdmin

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "hospital-realm")
ADMIN_USER = os.getenv("KEYCLOAK_ADMIN_USERNAME", "admin")
ADMIN_PASS = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")


def _get_kc_admin() -> KeycloakAdmin:
    return KeycloakAdmin(
        server_url=KEYCLOAK_URL,
        username=ADMIN_USER,
        password=ADMIN_PASS,
        realm_name="master",
        verify=False,
    )


def fix_user(username: str) -> None:
    kc_admin = _get_kc_admin()
    kc_admin.realm_name = KEYCLOAK_REALM
    kc_admin.connection.realm_name = KEYCLOAK_REALM

    user_id = kc_admin.get_user_id(username)
    if not user_id:
        print(f"User '{username}' not found")
        return

    user = kc_admin.get_user(user_id)
    print(f"User: {user.get('username')}")
    print(f"  RequiredActions before: {user.get('requiredActions', [])}")

    payload = {
        "username": user.get("username"),
        "firstName": user.get("firstName", ""),
        "lastName": user.get("lastName", ""),
        "email": user.get("email", ""),
        "emailVerified": True,
        "enabled": True,
        "requiredActions": [],
    }
    if user.get("attributes"):
        payload["attributes"] = user["attributes"]

    kc_admin.update_user(user_id=user_id, payload=payload)

    user_after = kc_admin.get_user(user_id)
    print(f"  RequiredActions after: {user_after.get('requiredActions', [])}")
    print(f"  Fixed '{username}'")


def fix_all() -> None:
    kc_admin = _get_kc_admin()
    kc_admin.realm_name = KEYCLOAK_REALM
    kc_admin.connection.realm_name = KEYCLOAK_REALM

    users = kc_admin.get_users({})
    for u in users:
        if u.get("requiredActions"):
            fix_user(u.get("username"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("username", nargs="?", help="Username to fix")
    parser.add_argument("--all", action="store_true", help="Fix all users with pending actions")
    args = parser.parse_args()

    if args.all:
        fix_all()
    elif args.username:
        fix_user(args.username)
    else:
        print("Usage: python scripts/fix_required_actions.py <username> OR --all")
