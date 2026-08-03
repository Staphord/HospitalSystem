"""Test-time environment defaults so app.core.config.Settings() can be constructed
without a real .env file (mirrors .env.example placeholder values)."""

import os

_DEFAULTS = {
    "ENVIRONMENT": "test",
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/ward_db",
    "SECRET_KEY": "test-secret-key",
    "REDIS_URL": "redis://localhost:6379/0",
    "KEYCLOAK_URL": "http://localhost:8080",
    "KEYCLOAK_REALM": "hospital",
    "KEYCLOAK_CLIENT_ID": "hospital-backend",
    "KEYCLOAK_CLIENT_SECRET": "test-secret",
    "KEYCLOAK_ADMIN_USERNAME": "admin",
    "KEYCLOAK_ADMIN_PASSWORD": "admin",
    "TENANT_DB_ENCRYPTION_KEY": "test-encryption-key",
}

for key, value in _DEFAULTS.items():
    os.environ.setdefault(key, value)
