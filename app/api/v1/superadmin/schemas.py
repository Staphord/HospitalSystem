from pydantic import BaseModel, EmailStr
from datetime import datetime


class UserCreate(BaseModel):
    username: str
    password: str
    email: EmailStr
    full_name: str = ""
    role: str = "super_admin"
    hospital_id: str | None = None


class UserUpdate(BaseModel):
    username: str | None = None
    password: str | None = None
    email: EmailStr | None = None
    full_name: str | None = None
    role: str | None = None
    hospital_id: str | None = None


class UserOut(BaseModel):
    keycloak_sub: str
    username: str | None
    full_name: str | None
    email: str | None
    role: str | None
    hospital_id: str | None

    class Config:
        from_attributes = True


class UserDelete(BaseModel):
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
