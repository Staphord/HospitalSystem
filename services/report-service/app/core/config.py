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

    # ── Hospital Assistant ──────────────────────────────────────────────────
    #
    # One switch, and only one. With it off the assistant does not exist for
    # this deployment: every assistant route answers 404, and the status route
    # tells the browser not to render the launcher at all, so staff are never
    # shown a control that cannot work. With it on, every assistant capability
    # is available, and who reaches each one is decided by role in
    # app/assistant/permissions.py rather than by configuration.
    #
    # The per-capability switches that used to sit here are gone. They existed
    # to stage a phased rollout that has now landed; keeping them would have
    # meant a deployment could half-enable the assistant and leave staff with a
    # launcher whose buttons do nothing.
    assistant_operational_chat_enabled: bool = Field(
        default=False, alias="ASSISTANT_OPERATIONAL_CHAT_ENABLED"
    )

    # Whether a medicine the reference pack does not carry may be answered from
    # the model's own knowledge of pharmacology, clearly marked as unverified.
    #
    # This is deliberately the one assistant setting still read from the
    # environment rather than the super admin portal: its safe value depends on
    # a clinical judgement about the reference pack, so it belongs where a
    # deployment can set it and track the change in version control, not
    # somewhere it can be flipped from a browser.
    assistant_medicines_model_fallback_enabled: bool = Field(
        default=False, alias="ASSISTANT_MEDICINES_MODEL_FALLBACK_ENABLED"
    )

    # Provider bootstrap only.
    #
    # These seed the stored assistant configuration the first time the service
    # starts against a database that has none, so an existing deployment keeps
    # working across this change. From then on the super admin portal is the
    # authority: see app/assistant/config_store.py. Prefer leaving the key empty
    # here and setting it in the portal, which stores it encrypted rather than
    # leaving it readable in a process environment.
    assistant_groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    assistant_groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL"
    )
    assistant_groq_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_MODEL")

    # Read-only impersonation enforcement.
    #
    # "log" reports what would have been blocked without blocking it, which is
    # how this is rolled out: the shared ReadOnlyScopeMiddleware has never
    # actually blocked anything, so switching straight to "enforce" would begin
    # refusing writes that succeed today, on paths nobody has tested under
    # enforcement. Run in "log", read the warnings, then move to "enforce".
    #
    # "enforce" refuses writes in a read-only session. "off" disables the check.
    readonly_scope_enforcement: str = Field(
        default="log", alias="READONLY_SCOPE_ENFORCEMENT"
    )

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
