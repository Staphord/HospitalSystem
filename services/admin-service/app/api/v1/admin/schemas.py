from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from shared.security import validate_password, sanitize_username

ALLOWED_HOSPITAL_ROLES = r"^[a-zA-Z_][a-zA-Z0-9_]*$"


class HospitalUserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    email: EmailStr
    full_name: str = Field(default="", max_length=255)
    role: str = Field(default="hospital_user", pattern=ALLOWED_HOSPITAL_ROLES)
    department_id: UUID | None = None
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        return validate_password(value)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return sanitize_username(value)


class HospitalUserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, pattern=ALLOWED_HOSPITAL_ROLES)
    is_active: bool | None = None
    force_password_change: bool | None = None
    department_id: UUID | None = None
    phone: str | None = Field(default=None, max_length=20)
    reason: str | None = Field(default=None, max_length=500)

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


class HospitalUserOut(BaseModel):
    keycloak_sub: str
    username: str | None
    full_name: str | None
    email: str | None
    role: str | None
    hospital_id: str | None
    is_active: bool
    force_password_change: bool
    department_id: UUID | None = None
    phone: str | None = None
    last_login_at: datetime | None = None
    password_expires_at: datetime | None = None
    mfa_enabled: bool = False
    deleted_at: datetime | None = None

    class Config:
        from_attributes = True


class UserListOut(BaseModel):
    users: list[HospitalUserOut]
    total: int
    limit: int
    offset: int


class RoleCreate(BaseModel):
    name: str = Field(..., max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class RoleUpdate(BaseModel):
    name: str = Field(..., max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class RoleOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    composite: bool | None = None
    clientRole: bool | None = None
    containerId: str | None = None


class GlobalRoleOut(BaseModel):
    global_role_id: UUID
    name: str
    description: str | None
    scope: dict | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TenantRoleCreate(BaseModel):
    name: str = Field(..., max_length=50, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    description: str | None = Field(default=None, max_length=500)
    scope: dict | None = None


class TenantRoleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=50, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    description: str | None = Field(default=None, max_length=500)
    scope: dict | None = None


class TenantRoleOut(BaseModel):
    tenant_role_id: UUID
    tenant_id: str
    name: str
    description: str | None
    scope: dict | None = None
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PermissionOut(BaseModel):
    role_name: str
    modules: list
    actions: list
    updated_at: datetime

    class Config:
        from_attributes = True


class PermissionUpdate(BaseModel):
    modules: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)


class AuditLogOut(BaseModel):
    log_id: UUID
    user_id: str
    action: str
    table_name: str
    record_id: str | None
    old_values: dict | None
    new_values: dict | None
    ip_address: str | None
    session_id: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogListOut(BaseModel):
    items: list[AuditLogOut]
    total: int
    limit: int
    offset: int


class HospitalProfileOut(BaseModel):
    tenant_id: str
    hospital_name: str
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
    status: str | None = None
    subscription_plan: str | None = None

    class Config:
        from_attributes = True


class HospitalProfileUpdate(BaseModel):
    hospital_name: str | None = Field(default=None, max_length=200)
    country: str | None = None
    city: str | None = None
    address: str | None = None
    primary_contact_name: str | None = None
    primary_contact_email: str | None = None
    primary_contact_phone: str | None = None
    billing_email: str | None = None
    timezone: str | None = None
    currency: str | None = Field(default=None, max_length=5)
    date_format: str | None = None
    logo_url: str | None = None


class DepartmentCreate(BaseModel):
    department_name: str = Field(..., max_length=100)
    department_type: str = Field(..., max_length=50)
    head_user_sub: str | None = None
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    department_name: str | None = Field(default=None, max_length=100)
    department_type: str | None = Field(default=None, max_length=50)
    head_user_sub: str | None = None
    is_active: bool | None = None


class DepartmentOut(BaseModel):
    department_id: UUID
    department_name: str
    department_type: str
    head_user_sub: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FeeScheduleCreate(BaseModel):
    item_name: str = Field(..., max_length=200)
    item_code: str = Field(..., max_length=50)
    item_type: str = Field(..., max_length=50)
    standard_price: Decimal
    insurance_price: Decimal | None = None
    is_active: bool = True
    effective_from: date
    effective_to: date | None = None


class FeeScheduleUpdate(BaseModel):
    item_name: str | None = None
    item_type: str | None = None
    standard_price: Decimal | None = None
    insurance_price: Decimal | None = None
    is_active: bool | None = None
    effective_from: date | None = None
    effective_to: date | None = None


class FeeScheduleOut(BaseModel):
    fee_id: UUID
    item_name: str
    item_code: str
    item_type: str
    standard_price: Decimal
    insurance_price: Decimal | None
    is_active: bool
    effective_from: date
    effective_to: date | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class InsuranceProviderCreate(BaseModel):
    name: str = Field(..., max_length=150)
    contact_email: str | None = None
    contact_phone: str | None = None
    notes: str | None = None
    is_active: bool = True


class InsuranceProviderUpdate(BaseModel):
    name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class InsuranceProviderOut(BaseModel):
    provider_id: UUID
    name: str
    contact_email: str | None
    contact_phone: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WardOut(BaseModel):
    ward_name: str
    bed_count: int
    available: int


class BedCreate(BaseModel):
    ward_name: str = Field(
        ...,
        max_length=100,
        description="Ward / unit name (e.g. General Ward, ICU). Creating a bed with a new ward_name effectively creates that ward.",
    )
    bed_number: str = Field(..., max_length=20)
    bed_type: str = Field(
        default="general",
        max_length=50,
        description="e.g. general, icu, hdu, isolation, maternity, paediatric",
    )
    is_available: bool = True
    is_active: bool = True
    notes: str | None = None


class BedUpdate(BaseModel):
    ward_name: str | None = None
    bed_number: str | None = None
    bed_type: str | None = None
    is_available: bool | None = None
    is_active: bool | None = None
    notes: str | None = None


class BedOut(BaseModel):
    bed_id: UUID
    ward_name: str
    bed_number: str
    bed_type: str
    is_available: bool
    is_active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BackupJobOut(BaseModel):
    backup_id: UUID
    tenant_id: str
    status: str
    file_path: str | None
    size_bytes: int | None
    triggered_by: str
    triggered_by_sub: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    class Config:
        from_attributes = True
