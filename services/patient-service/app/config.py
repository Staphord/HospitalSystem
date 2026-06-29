from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    environment: str = Field(default="dev")
    database_url: str = Field(default="sqlite:///:memory:")
    secret_key: str = Field(default="test-secret-key")
    redis_url: str = Field(default="redis://localhost:6379/0")

    keycloak_url: str = Field(default="http://localhost:8080")
    keycloak_realm: str = Field(default="hospital-realm")
    keycloak_client_id: str = Field(default="hospital-client")
    keycloak_client_secret: str = Field(default="test-secret")
    keycloak_admin_username: str = Field(default="admin")
    keycloak_admin_password: str = Field(default="admin")
    keycloak_introspect: bool = Field(default=False)

    allowed_origins: str = Field(default="")
    default_hospital_id: str = Field(default="default-hospital")
    tenant_db_encryption_key: str = Field(default="RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0=")

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
