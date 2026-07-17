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
    default_hospital_id: str = Field(default="hosp-ac224699", alias="DEFAULT_HOSPITAL_ID")
    dev_tenant_id: str = Field(default="hosp-ac224699", alias="DEV_TENANT_ID")

    tenant_db_encryption_key: str = Field(alias="TENANT_DB_ENCRYPTION_KEY")
    impersonation_token_ttl: int = Field(default=900, alias="IMPERSONATION_TOKEN_TTL")
    suspension_check_interval: int = Field(default=86400, alias="SUSPENSION_CHECK_INTERVAL")
    suspended_tenant_blocklist_ttl: int = Field(default=3600, alias="SUSPENDED_BLOCKLIST_TTL")

    password_reset_token_ttl: int = Field(default=3600, alias="PASSWORD_RESET_TOKEN_TTL")

    audit_db_url: str | None = Field(default=None, alias="AUDIT_DATABASE_URL")

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
