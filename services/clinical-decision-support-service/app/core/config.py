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

    audit_db_url: str | None = Field(default=None, alias="AUDIT_DATABASE_URL")

    # Read-only impersonation enforcement, carried over from report-service so
    # this service behaves identically under an impersonated session. "log"
    # reports what would have been refused; "enforce" refuses it; "off"
    # disables the check.
    readonly_scope_enforcement: str = Field(
        default="log", alias="READONLY_SCOPE_ENFORCEMENT"
    )

    # ── Clinical decision support kill switches ──────────────────────────────
    #
    # Two levels, both defaulting to off. cds_enabled is the whole-service
    # switch: with it off every /api/v1/cds route answers 404 and no clinical
    # code runs at all. The per-capability flag below is the finer kill switch,
    # so differential support can be pulled without taking down anything else
    # and without a redeploy of other services.
    cds_enabled: bool = Field(default=False, alias="CDS_ENABLED")
    cds_differential_support_enabled: bool = Field(
        default=False, alias="CDS_DIFFERENTIAL_SUPPORT_ENABLED"
    )

    # ── Clinical differential support ────────────────────────────────────────
    #
    # A narrow, clinician-only workflow that organizes what was recorded into
    # considerations for review. It is not a diagnosis engine and it is not an
    # authority: red flags come from the deterministic rule pack in code, never
    # from a model, and no output may prescribe, dose, refer, or write a record.
    #
    # The department this is approved for. A request for any other department is
    # refused, so switching the capability on cannot quietly widen it beyond the
    # workflow a clinical owner reviewed.
    cds_differential_department: str = Field(
        default="general_opd", alias="CDS_DIFFERENTIAL_DEPARTMENT"
    )

    # Red flags are deterministic. This names the versioned rule pack that
    # produces them so a flag can always be traced to a rule and a version.
    cds_redflag_ruleset_version: str = Field(
        default="builtin-general-opd-2026.08", alias="CDS_REDFLAG_RULESET_VERSION"
    )

    # The prompt is versioned so a suggestion can be reproduced. Bumping this is
    # a deliberate act that shows up in every audit record afterwards.
    cds_differential_prompt_version: str = Field(
        default="differential-2026.08.1", alias="CDS_DIFFERENTIAL_PROMPT_VERSION"
    )

    # Bounds on one differential request, so a single call cannot walk an entire
    # clinical history or hand a model an unbounded amount of free text.
    cds_max_symptoms_per_request: int = Field(
        default=20, alias="CDS_MAX_SYMPTOMS_PER_REQUEST"
    )
    # Bound on how many current medicines are listed as context, so one request
    # cannot walk an entire medication history.
    cds_max_medicines_in_context: int = Field(
        default=30, alias="CDS_MAX_MEDICINES_IN_CONTEXT"
    )
    cds_max_considerations: int = Field(default=8, alias="CDS_MAX_CONSIDERATIONS")
    cds_differential_timeout_seconds: float = Field(
        default=20.0, alias="CDS_DIFFERENTIAL_TIMEOUT_SECONDS"
    )

    # ── Model provider ───────────────────────────────────────────────────────
    #
    # Vendor-neutral seam. The model may organize and explain approved data; it
    # never decides severity, never raises a red flag, and never receives a
    # database credential, a tenant header, or a write tool. With no key
    # configured the provider is the fail-closed null provider, which returns
    # nothing rather than fabricating a suggestion.
    cds_provider: str = Field(default="groq", alias="CDS_PROVIDER")
    cds_groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    cds_groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL"
    )
    cds_groq_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_MODEL")

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
