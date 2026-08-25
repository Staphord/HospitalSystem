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

    # Hospital Assistant capability switches. Every capability is independently
    # gated and defaults to off, so no assistant behaviour can be reached until
    # its phase has passed its exit gate and an operator turns it on.
    assistant_operational_chat_enabled: bool = Field(
        default=False, alias="ASSISTANT_OPERATIONAL_CHAT_ENABLED"
    )
    assistant_voice_enabled: bool = Field(default=False, alias="ASSISTANT_VOICE_ENABLED")
    assistant_medication_check_enabled: bool = Field(
        default=False, alias="ASSISTANT_MEDICATION_CHECK_ENABLED"
    )
    assistant_differential_support_enabled: bool = Field(
        default=False, alias="ASSISTANT_DIFFERENTIAL_SUPPORT_ENABLED"
    )
    assistant_realtime_voice_enabled: bool = Field(
        default=False, alias="ASSISTANT_REALTIME_VOICE_ENABLED"
    )

    # Model provider. Groq is the approved vendor; the credential is read here on
    # the server only and is never sent to a browser, a log, or an audit record.
    assistant_provider: str = Field(default="groq", alias="ASSISTANT_PROVIDER")
    assistant_groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    assistant_groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL"
    )
    assistant_groq_model: str = Field(
        default="llama-3.3-70b-versatile", alias="GROQ_MODEL"
    )
    assistant_request_timeout_seconds: float = Field(
        default=20.0, alias="ASSISTANT_REQUEST_TIMEOUT_SECONDS"
    )
    assistant_max_question_chars: int = Field(
        default=2000, alias="ASSISTANT_MAX_QUESTION_CHARS"
    )

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
