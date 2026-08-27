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
    # code runs at all. The per-capability flags below are the finer kill
    # switches required by the phase gate, so medication checking can be pulled
    # without taking down anything else and without a redeploy of other
    # services.
    cds_enabled: bool = Field(default=False, alias="CDS_ENABLED")
    cds_medication_check_enabled: bool = Field(
        default=False, alias="CDS_MEDICATION_CHECK_ENABLED"
    )
    # Phase 7 owns differential support. The flag exists so the kill switch is
    # in place before the capability is, and it is never read by phase 5 code.
    cds_differential_support_enabled: bool = Field(
        default=False, alias="CDS_DIFFERENTIAL_SUPPORT_ENABLED"
    )

    # ── Interaction ruleset ──────────────────────────────────────────────────
    #
    # No ruleset ships with this repository. Deciding whether two medicines
    # interact, and how severely, is a clinical judgement that must come from a
    # licensed or hospital-approved source, so the default source is the
    # fail-closed null source: it answers "unavailable" to every question and
    # every result becomes needs_review.
    #
    # CDS_RULESET_PATH points at an approved, versioned ruleset artifact. The
    # loader validates its metadata, its approval block, and its effective and
    # review dates, and refuses to load an artifact that is not approved for the
    # current environment.
    cds_ruleset_source: str = Field(default="null", alias="CDS_RULESET_SOURCE")
    cds_ruleset_path: str | None = Field(default=None, alias="CDS_RULESET_PATH")

    # A ruleset past its review date is stale. Stale is not safe: it degrades
    # to needs_review rather than being used or being reported as no alerts.
    cds_ruleset_stale_after_days: int = Field(
        default=180, alias="CDS_RULESET_STALE_AFTER_DAYS"
    )

    # Bound on how much a single check may consider, so one request cannot walk
    # an entire medication history.
    cds_max_medications_per_check: int = Field(
        default=30, alias="CDS_MAX_MEDICATIONS_PER_CHECK"
    )
    cds_max_override_reason_chars: int = Field(
        default=500, alias="CDS_MAX_OVERRIDE_REASON_CHARS"
    )

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
