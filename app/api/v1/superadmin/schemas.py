from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, field_validator
from datetime import datetime


class SuperAdminCreate(BaseModel):
    username: str = Field(..., max_length=50)
    password: str = Field(..., min_length=8)
    email: EmailStr
    full_name: str = Field(..., max_length=200)
    role: str = Field(default="super_admin", max_length=50)
    mfa_secret: str | None = Field(default=None, max_length=100)


class SuperAdminUpdate(BaseModel):
    username: str | None = Field(default=None, max_length=50)
    password: str | None = Field(default=None, min_length=8)
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=50)
    mfa_secret: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


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
    status: str
    subscription_plan: str
    is_active: bool
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
    subscription_plan: str = "standard"


class TenantUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    subscription_plan: str | None = None
    is_active: bool | None = None


class RoleCreate(BaseModel):
    name: str
