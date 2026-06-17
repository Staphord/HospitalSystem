from pydantic import BaseModel, EmailStr, Field, field_validator

from shared.security import validate_password, sanitize_username

ALLOWED_HOSPITAL_ROLES = r"^(hospital_admin|hospital_user|nurse|clinician|doctor|patient)$"


class HospitalUserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    email: EmailStr
    full_name: str = Field(default="", max_length=255)
    role: str = Field(default="hospital_user", pattern=ALLOWED_HOSPITAL_ROLES)

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

    class Config:
        from_attributes = True
