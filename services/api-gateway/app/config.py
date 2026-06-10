from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    environment: str = Field(default="dev", alias="ENVIRONMENT")
    master_db_url: str = Field(alias="MASTER_DB_URL")
    secret_key: str = Field(alias="SECRET_KEY")
    redis_url: str = Field(alias="REDIS_URL")

    keycloak_url: str = Field(alias="KEYCLOAK_URL")
    keycloak_realm: str = Field(alias="KEYCLOAK_REALM")
    keycloak_client_id: str = Field(alias="KEYCLOAK_CLIENT_ID")
    keycloak_client_secret: str = Field(alias="KEYCLOAK_CLIENT_SECRET")
    keycloak_introspect: bool = Field(default=True, alias="KEYCLOAK_INTROSPECT")

    allowed_origins: str = Field(default="", alias="ALLOWED_ORIGINS")

    auth_service_url: str = Field(default="http://localhost:8001", alias="AUTH_SERVICE_URL")
    master_service_url: str = Field(default="http://localhost:8002", alias="MASTER_SERVICE_URL")
    reception_service_url: str = Field(default="http://localhost:8010", alias="RECEPTION_SERVICE_URL")
    triage_service_url: str = Field(default="http://localhost:8011", alias="TRIAGE_SERVICE_URL")
    consultation_service_url: str = Field(default="http://localhost:8012", alias="CONSULTATION_SERVICE_URL")
    laboratory_service_url: str = Field(default="http://localhost:8013", alias="LABORATORY_SERVICE_URL")
    radiology_service_url: str = Field(default="http://localhost:8014", alias="RADIOLOGY_SERVICE_URL")
    pharmacy_service_url: str = Field(default="http://localhost:8015", alias="PHARMACY_SERVICE_URL")
    billing_service_url: str = Field(default="http://localhost:8016", alias="BILLING_SERVICE_URL")
    ward_service_url: str = Field(default="http://localhost:8017", alias="WARD_SERVICE_URL")
    admin_service_url: str = Field(default="http://localhost:8018", alias="ADMIN_SERVICE_URL")
    notification_service_url: str = Field(default="http://localhost:8019", alias="NOTIFICATION_SERVICE_URL")
    report_service_url: str = Field(default="http://localhost:8020", alias="REPORT_SERVICE_URL")

    tenant_db_encryption_key: str = Field(alias="TENANT_DB_ENCRYPTION_KEY")
    impersonation_token_ttl: int = Field(default=900, alias="IMPERSONATION_TOKEN_TTL")
    suspended_tenant_blocklist_ttl: int = Field(default=3600, alias="SUSPENDED_BLOCKLIST_TTL")

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
