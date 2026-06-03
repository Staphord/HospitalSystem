import os

from keycloak import KeycloakAdmin


def main() -> None:
    keycloak_url = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
    realm = os.getenv("KEYCLOAK_REALM", "hospital-realm")
    admin_user = os.getenv("KEYCLOAK_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")

    kc_admin = KeycloakAdmin(
        server_url=keycloak_url,
        username=admin_user,
        password=admin_password,
        realm_name="master",
        verify=False,
    )

    if realm not in [r["realm"] for r in kc_admin.get_realms()]:
        kc_admin.create_realm(payload={"realm": realm, "enabled": True})

    kc_admin.realm_name = realm

    for role_name in ("hospital_user", "hospital_admin"):
        try:
            kc_admin.get_realm_role(role_name)
        except Exception:
            kc_admin.create_realm_role(payload={"name": role_name})

    test_username = os.getenv("KEYCLOAK_TEST_USERNAME", "testuser")
    test_password = os.getenv("KEYCLOAK_TEST_PASSWORD", "testpassword")
    admin_username = os.getenv("KEYCLOAK_ADMIN_TEST_USERNAME", "adminuser")
    admin_password_user = os.getenv(
        "KEYCLOAK_ADMIN_TEST_PASSWORD", "adminpassword")

    _ensure_user(kc_admin, test_username, test_password, ["hospital_user"])
    _ensure_user(kc_admin, admin_username,
                 admin_password_user, ["hospital_admin"])


def _ensure_user(kc_admin: KeycloakAdmin, username: str, password: str, roles: list[str]) -> None:
    user_id = kc_admin.get_user_id(username)
    if not user_id:
        user_id = kc_admin.create_user(
            {
                "username": username,
                "enabled": True,
                "email": f"{username}@example.com",
            }
        )
        kc_admin.set_user_password(
            user_id=user_id, password=password, temporary=False)

    role_reps = [kc_admin.get_realm_role(role) for role in roles]
    kc_admin.assign_realm_roles(user_id=user_id, roles=role_reps)


if __name__ == "__main__":
    main()
