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
    assistant_chat_history_enabled: bool = Field(
        default=False, alias="ASSISTANT_CHAT_HISTORY_ENABLED"
    )
    assistant_live_data_enabled: bool = Field(
        default=False, alias="ASSISTANT_LIVE_DATA_ENABLED"
    )
    # Whether a medicine the reference pack does not carry may be answered from
    # the model's own knowledge of pharmacology, clearly marked as unverified.
    # Off by default and separate from the medicines capability itself, so a
    # hospital can run the reference and nothing but the reference.
    assistant_medicines_model_fallback_enabled: bool = Field(
        default=False, alias="ASSISTANT_MEDICINES_MODEL_FALLBACK_ENABLED"
    )

    # Model provider. Groq is the approved vendor; the credential is read here on
    # the server only and is never sent to a browser, a log, or an audit record.
    assistant_provider: str = Field(default="groq", alias="ASSISTANT_PROVIDER")
    assistant_groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    assistant_groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL"
    )
    assistant_groq_model: str = Field(
        default="openai/gpt-oss-120b", alias="GROQ_MODEL"
    )
    assistant_request_timeout_seconds: float = Field(
        default=20.0, alias="ASSISTANT_REQUEST_TIMEOUT_SECONDS"
    )
    assistant_max_question_chars: int = Field(
        default=2000, alias="ASSISTANT_MAX_QUESTION_CHARS"
    )

    # Chat history bounds. These exist so one staff member cannot fill a tenant
    # database by leaving the panel open: the oldest conversation is dropped
    # once the ceiling is reached, and a single thread stops growing instead of
    # growing without limit. History is kept until its owner deletes it, which
    # is why there is deliberately no expiry setting here to discard it quietly.
    assistant_history_max_conversations: int = Field(
        default=50, alias="ASSISTANT_HISTORY_MAX_CONVERSATIONS"
    )
    assistant_history_max_messages: int = Field(
        default=200, alias="ASSISTANT_HISTORY_MAX_MESSAGES"
    )

    # Push-to-talk voice. Every bound here is enforced on the server; nothing
    # about a capture is accepted from the browser. whisper-large-v3 is chosen
    # over the turbo variant because Swahili and code-mixed Swahili/English
    # speech are a requirement, and turbo trades multilingual accuracy for
    # speed. Raw audio is never persisted: there is deliberately no retention
    # setting to turn on.
    assistant_transcription_model: str = Field(
        default="whisper-large-v3", alias="ASSISTANT_TRANSCRIPTION_MODEL"
    )
    assistant_max_audio_bytes: int = Field(
        default=5 * 1024 * 1024, alias="ASSISTANT_MAX_AUDIO_BYTES"
    )
    assistant_max_audio_duration_ms: int = Field(
        default=60_000, alias="ASSISTANT_MAX_AUDIO_DURATION_MS"
    )
    # Must stay below the API gateway's fixed 30 second proxy timeout, or the
    # browser sees a gateway error instead of the assistant's own safe refusal.
    assistant_voice_timeout_seconds: float = Field(
        default=20.0, alias="ASSISTANT_VOICE_TIMEOUT_SECONDS"
    )

    # Live operational figures read from the tenant database.
    #
    # The cache exists so the chat rate limit cannot be turned into database
    # load: twenty questions a minute about bed availability must not become
    # twenty scans. It is deliberately short, and every figure is stamped
    # with the time it was read so staff can see how fresh it is.
    #
    # The statement timeout is applied inside the read-only transaction, so a
    # pathological query releases its connection instead of holding it.
    assistant_live_data_cache_seconds: int = Field(
        default=30, alias="ASSISTANT_LIVE_DATA_CACHE_SECONDS"
    )
    assistant_live_data_timeout_seconds: float = Field(
        default=3.0, alias="ASSISTANT_LIVE_DATA_TIMEOUT_SECONDS"
    )
    assistant_live_data_max_metrics: int = Field(
        default=3, alias="ASSISTANT_LIVE_DATA_MAX_METRICS"
    )

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
