from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    environment: str = Field(default="dev", alias="ENVIRONMENT")
    database_url: str = Field(alias="DATABASE_URL")
    secret_key: str = Field(alias="SECRET_KEY")
    redis_url: str = Field(alias="REDIS_URL")

    keycloak_url: str = Field(alias="KEYCLOAK_URL")
    keycloak_realm: str = Field(alias="KEYCLOAK_REALM")
    keycloak_client_id: str = Field(alias="KEYCLOAK_CLIENT_ID")
    keycloak_client_secret: str = Field(alias="KEYCLOAK_CLIENT_SECRET")
    keycloak_admin_username: str = Field(alias="KEYCLOAK_ADMIN_USERNAME")
    keycloak_admin_password: str = Field(alias="KEYCLOAK_ADMIN_PASSWORD")
    keycloak_introspect: bool = Field(default=True, alias="KEYCLOAK_INTROSPECT")

    allowed_origins: str = Field(default="", alias="ALLOWED_ORIGINS")
    default_hospital_id: str = Field(default="default-hospital", alias="DEFAULT_HOSPITAL_ID")

    tenant_db_encryption_key: str = Field(alias="TENANT_DB_ENCRYPTION_KEY")
    impersonation_token_ttl: int = Field(default=900, alias="IMPERSONATION_TOKEN_TTL")
    suspension_check_interval: int = Field(default=86400, alias="SUSPENSION_CHECK_INTERVAL")
    suspended_tenant_blocklist_ttl: int = Field(default=3600, alias="SUSPENDED_BLOCKLIST_TTL")

    password_reset_token_ttl: int = Field(default=3600, alias="PASSWORD_RESET_TOKEN_TTL")

    audit_db_url: str | None = Field(default=None, alias="AUDIT_DATABASE_URL")
    db_admin_url: str = Field(default="postgresql://postgres:postgres@localhost:5432/postgres", alias="DB_ADMIN_URL")
    tenant_db_template: str = Field(default="postgresql://postgres:postgres@localhost:5432/tenant_{tenant_id}", alias="TENANT_DB_TEMPLATE")

    # Downstream service URLs for health checks
    api_gateway_url: str | None = Field(default=None, alias="API_GATEWAY_URL")
    auth_service_url: str | None = Field(default=None, alias="AUTH_SERVICE_URL")
    master_service_url: str | None = Field(default=None, alias="MASTER_SERVICE_URL")
    admin_service_url: str | None = Field(default=None, alias="ADMIN_SERVICE_URL")
    reception_service_url: str | None = Field(default=None, alias="RECEPTION_SERVICE_URL")
    triage_service_url: str | None = Field(default=None, alias="TRIAGE_SERVICE_URL")
    consultation_service_url: str | None = Field(default=None, alias="CONSULTATION_SERVICE_URL")
    laboratory_service_url: str | None = Field(default=None, alias="LABORATORY_SERVICE_URL")
    radiology_service_url: str | None = Field(default=None, alias="RADIOLOGY_SERVICE_URL")
    pharmacy_service_url: str | None = Field(default=None, alias="PHARMACY_SERVICE_URL")
    billing_service_url: str | None = Field(default=None, alias="BILLING_SERVICE_URL")
    ward_service_url: str | None = Field(default=None, alias="WARD_SERVICE_URL")
    notification_service_url: str | None = Field(default=None, alias="NOTIFICATION_SERVICE_URL")
    report_service_url: str | None = Field(default=None, alias="REPORT_SERVICE_URL")

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
