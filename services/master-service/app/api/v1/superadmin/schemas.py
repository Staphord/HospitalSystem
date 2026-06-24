from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, field_validator
from datetime import datetime, date
from decimal import Decimal

from shared.security import validate_password, sanitize_username


class SuperAdminCreate(BaseModel):
    username: str = Field(..., max_length=50)
    password: str = Field(..., min_length=8)
    email: EmailStr
    full_name: str = Field(..., max_length=200)
    role: str = Field(default="super_admin", max_length=50)
    mfa_secret: str | None = Field(default=None, max_length=100)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        return validate_password(value)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return sanitize_username(value)


class SuperAdminUpdate(BaseModel):
    username: str | None = Field(default=None, max_length=50)
    password: str | None = Field(default=None, min_length=8)
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=50)
    mfa_secret: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_password(value)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return sanitize_username(value)


class SuperAdminOut(BaseModel):
    super_admin_id: UUID
    username: str
    email: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class SuperAdminDelete(BaseModel):
    username: str


class TenantOut(BaseModel):
    id: int
    tenant_id: str
    name: str
    country: str | None = None
    city: str | None = None
    address: str | None = None
    primary_contact_name: str | None = None
    primary_contact_email: str | None = None
    primary_contact_phone: str | None = None
    billing_email: str | None = None
    timezone: str | None = None
    currency: str | None = None
    date_format: str | None = None
    logo_url: str | None = None
    data_region: str | None = None
    status: str
    subscription_plan: str
    subscription_status: str | None = None
    subscription_billing_cycle: str | None = None
    subscription_start: datetime | None = None
    subscription_end: datetime | None = None
    trial_start: datetime | None = None
    trial_end: datetime | None = None
    has_used_trial: bool | None = None
    is_active: bool
    keycloak_realm: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class TenantCreate(BaseModel):
    hospital_name: str
    admin_username: str
    admin_password: str
    admin_email: EmailStr
    admin_full_name: str = ""

    # SaaS registration fields
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    address: str | None = None
    primary_contact_name: str | None = Field(default=None, max_length=200)
    primary_contact_email: EmailStr | None = None
    primary_contact_phone: str | None = Field(default=None, max_length=20)
    billing_email: EmailStr | None = None
    timezone: str | None = Field(default=None, max_length=50)
    currency: str | None = Field(default=None, max_length=5)
    date_format: str | None = Field(default=None, max_length=20)
    logo_url: str | None = Field(default=None, max_length=255)
    data_region: str | None = Field(default=None, max_length=50)


class TenantUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    is_active: bool | None = None

    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    address: str | None = None
    primary_contact_name: str | None = Field(default=None, max_length=200)
    primary_contact_email: EmailStr | None = None
    primary_contact_phone: str | None = Field(default=None, max_length=20)
    billing_email: EmailStr | None = None
    timezone: str | None = Field(default=None, max_length=50)
    currency: str | None = Field(default=None, max_length=5)
    date_format: str | None = Field(default=None, max_length=20)
    logo_url: str | None = Field(default=None, max_length=255)
    data_region: str | None = Field(default=None, max_length=50)


class RoleCreate(BaseModel):
    name: str


# ---------------------------------------------------------------------------
# Subscription management schemas
# ---------------------------------------------------------------------------


class SubscriptionSubscribeRequest(BaseModel):
    plan: str
    billing_cycle: str = "monthly"
    start_trial: bool = False
    payment_provider_id: str | None = None

    @field_validator("billing_cycle")
    @classmethod
    def validate_billing_cycle(cls, value: str) -> str:
        allowed = {"monthly", "annual"}
        if value.lower() not in allowed:
            raise ValueError(f"billing_cycle must be one of {allowed}")
        return value.lower()


class SubscriptionPlanChangeRequest(BaseModel):
    plan: str
    billing_cycle: str | None = None
    effective_at_end: bool = False

    @field_validator("billing_cycle")
    @classmethod
    def validate_billing_cycle(cls, value: str | None) -> str | None:
        if value is None:
            return value
        allowed = {"monthly", "annual"}
        if value.lower() not in allowed:
            raise ValueError(f"billing_cycle must be one of {allowed}")
        return value.lower()


class SubscriptionRenewRequest(BaseModel):
    billing_cycle: str | None = None

    @field_validator("billing_cycle")
    @classmethod
    def validate_billing_cycle(cls, value: str | None) -> str | None:
        if value is None:
            return value
        allowed = {"monthly", "annual"}
        if value.lower() not in allowed:
            raise ValueError(f"billing_cycle must be one of {allowed}")
        return value.lower()


class TenantSuspendRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=1000)


class TenantTerminateRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=1000)


class SubscriptionSnapshot(BaseModel):
    plan: str
    display_name: str
    status: str
    billing_cycle: str | None
    start: datetime | None
    end: datetime | None
    grace_period_end: datetime | None
    auto_renew: bool
    is_expired: bool
    in_grace_period: bool
    has_used_trial: bool
    pending_plan: str | None = None
    pending_billing_cycle: str | None = None


class SuspensionSnapshot(BaseModel):
    suspended_at: datetime | None
    reason: str | None
    reactivated_at: datetime | None


class TerminationSnapshot(BaseModel):
    terminated_at: datetime | None
    reason: str | None


class SubscriptionStateOut(BaseModel):
    tenant_id: str
    name: str
    status: str
    is_active: bool
    is_trial: bool
    subscription: SubscriptionSnapshot
    suspension: SuspensionSnapshot
    termination: TerminationSnapshot
    payment_provider_id: str | None


class SubscriptionActionOut(BaseModel):
    tenant_id: str
    action: str
    previous_status: str | None
    previous_plan: str | None
    subscription: SubscriptionSnapshot
    suspension: SuspensionSnapshot
    termination: TerminationSnapshot


class PlanCatalogOut(BaseModel):
    plan: str
    plan_id: UUID
    display_name: str
    monthly_price: int
    annual_price: int
    trial_days: int
    max_users: int
    features: list[str]
    rank: int


# ---------------------------------------------------------------------------
# New SaaS schema-oriented response models
# ---------------------------------------------------------------------------


class SubscriptionPlanOut(BaseModel):
    plan_id: UUID
    plan_name: str
    description: str | None
    max_users: int | None
    max_patients: int | None
    storage_gb: int
    modules_included: list[str]
    monthly_price: Decimal
    annual_price: Decimal
    annual_discount_pct: Decimal
    uptime_sla_pct: Decimal
    backup_frequency_hours: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SubscriptionOut(BaseModel):
    subscription_id: UUID
    tenant_id: str
    plan_id: UUID
    billing_cycle: str
    start_date: date
    end_date: date
    grace_period_days: int
    auto_renew: bool
    status: str
    suspended_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class InvoiceOut(BaseModel):
    invoice_id: UUID
    tenant_id: str
    subscription_id: UUID
    invoice_number: str
    billing_period_start: date
    billing_period_end: date
    plan_name: str
    amount: Decimal
    currency: str
    due_date: date
    status: str
    issued_at: datetime
    paid_at: datetime | None

    class Config:
        from_attributes = True


class SaaSPaymentOut(BaseModel):
    payment_id: UUID
    invoice_id: UUID
    tenant_id: str
    amount: Decimal
    currency: str
    payment_method: str
    reference_number: str | None
    recorded_by: UUID
    receipt_sent_at: datetime | None
    paid_at: datetime

    class Config:
        from_attributes = True


class SubscriptionAuditLogOut(BaseModel):
    event_id: UUID
    tenant_id: str
    subscription_id: UUID | None
    event_type: str
    actor_id: UUID | None
    actor_type: str
    reason: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AnnouncementOut(BaseModel):
    announcement_id: UUID
    title: str
    body: str
    audience: str
    target_tenant_ids: list[str] | None
    publish_at: datetime
    expires_at: datetime | None
    created_by: UUID | None
    created_at: datetime

    class Config:
        from_attributes = True


class AnnouncementCreate(BaseModel):
    title: str = Field(..., max_length=200)
    body: str
    audience: str = Field(default="all")
    target_tenant_ids: list[str] | None = None
    publish_at: datetime
    expires_at: datetime | None = None


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = None
    audience: str | None = None
    target_tenant_ids: list[str] | None = None
    publish_at: datetime | None = None
    expires_at: datetime | None = None


class PlanCreate(BaseModel):
    plan_name: str = Field(..., max_length=50)
    description: str | None = Field(default=None, max_length=255)
    max_users: int | None = None
    max_patients: int | None = None
    storage_gb: int = 0
    modules_included: list[str] = Field(default_factory=list)
    monthly_price: Decimal = Field(default=Decimal("0"))
    annual_price: Decimal = Field(default=Decimal("0"))
    annual_discount_pct: Decimal = Field(default=Decimal("0"))
    uptime_sla_pct: Decimal = Field(default=Decimal("99.9"))
    backup_frequency_hours: int = 24
    is_active: bool = True


class PlanUpdate(BaseModel):
    plan_name: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=255)
    max_users: int | None = None
    max_patients: int | None = None
    storage_gb: int | None = None
    modules_included: list[str] | None = None
    monthly_price: Decimal | None = None
    annual_price: Decimal | None = None
    annual_discount_pct: Decimal | None = None
    uptime_sla_pct: Decimal | None = None
    backup_frequency_hours: int | None = None
    is_active: bool | None = None


class SuperAdminAuditLogOut(BaseModel):
    log_id: UUID
    super_admin_id: UUID
    action: str
    tenant_id: str | None
    action_detail: dict | None
    is_impersonation: bool
    ip_address: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class InvoiceCreate(BaseModel):
    subscription_id: UUID
    invoice_number: str = Field(..., max_length=30)
    billing_period_start: date
    billing_period_end: date
    plan_name: str = Field(..., max_length=50)
    amount: Decimal
    currency: str = Field(default="USD", max_length=5)
    due_date: date
    status: str = Field(default="unpaid", max_length=32)


class InvoiceUpdate(BaseModel):
    status: str | None = Field(default=None, max_length=32)
    paid_at: datetime | None = None


class SaaSPaymentCreate(BaseModel):
    invoice_id: UUID
    amount: Decimal
    currency: str = Field(default="USD", max_length=5)
    payment_method: str = Field(..., max_length=50)
    reference_number: str | None = Field(default=None, max_length=100)
    receipt_sent_at: datetime | None = None


# ---------------------------------------------------------------------------
# Incident management schemas
# ---------------------------------------------------------------------------


class IncidentCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str
    severity: str = Field(default="warning", max_length=16)
    source: str | None = Field(default=None, max_length=100)
    tenant_id: str | None = Field(default=None, max_length=64)
    assigned_to: UUID | None = None


class IncidentUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    severity: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=100)
    tenant_id: str | None = Field(default=None, max_length=64)
    assigned_to: UUID | None = None
    resolution_notes: str | None = None


class IncidentOut(BaseModel):
    incident_id: UUID
    title: str
    description: str
    severity: str
    status: str
    source: str | None
    tenant_id: str | None
    assigned_to: UUID | None
    resolved_at: datetime | None
    resolution_notes: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
