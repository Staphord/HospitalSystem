import os
import sys
from dotenv import load_dotenv
from keycloak import KeycloakAdmin

load_dotenv()

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "hospital-realm")
KEYCLOAK_ADMIN_USER = os.getenv("KEYCLOAK_ADMIN_USERNAME", "admin")
KEYCLOAK_ADMIN_PASS = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")

print("KEYCLOAK_URL:", KEYCLOAK_URL)
print("KEYCLOAK_REALM:", KEYCLOAK_REALM)
print("KEYCLOAK_ADMIN_USER:", KEYCLOAK_ADMIN_USER)
print("KEYCLOAK_ADMIN_PASS:", KEYCLOAK_ADMIN_PASS)


kc_admin = KeycloakAdmin(
    server_url=KEYCLOAK_URL,
    username=KEYCLOAK_ADMIN_USER,
    password=KEYCLOAK_ADMIN_PASS,
    realm_name=KEYCLOAK_REALM,
    user_realm_name="master",
    verify=False,
)


print("Listing all realms:")
print([r["realm"] for r in kc_admin.get_realms()])

user_id = kc_admin.get_user_id("superadmin")
if not user_id:
    print("User 'superadmin' not found in Keycloak realm 'hospital-realm'")
else:
    user = kc_admin.get_user(user_id)
    import pprint
    print("Superadmin user details:")
    pprint.pprint(user)
