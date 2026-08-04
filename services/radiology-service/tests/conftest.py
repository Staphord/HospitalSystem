import os

# Provide required settings before app modules import Settings()
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/radiology_db")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("KEYCLOAK_URL", "http://localhost:8080")
os.environ.setdefault("KEYCLOAK_REALM", "hospital")
os.environ.setdefault("KEYCLOAK_CLIENT_ID", "hospital-backend")
os.environ.setdefault("KEYCLOAK_CLIENT_SECRET", "test-secret")
os.environ.setdefault("KEYCLOAK_ADMIN_USERNAME", "admin")
os.environ.setdefault("KEYCLOAK_ADMIN_PASSWORD", "admin")
os.environ.setdefault("TENANT_DB_ENCRYPTION_KEY", "test-encryption-key-32bytes-long!!")
os.environ.setdefault("ENVIRONMENT", "dev")
