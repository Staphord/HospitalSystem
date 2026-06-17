from pydantic import BaseModel, EmailStr, Field, field_validator

from shared.security import validate_password, sanitize_username


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1)
    realm: str | None = Field(default=None, max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    token_type: str = "Bearer"
    session_id: str
    not_before_policy: int = 0
    scope: str = "full"
    tenant_id: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password(value)


class MFASetupResponse(BaseModel):
    secret: str
    qr_code_url: str


class MFAVerifyRequest(BaseModel):
    totp_code: str = Field(..., min_length=6, max_length=6)


class MFAStatusResponse(BaseModel):
    mfa_enabled: bool
    mfa_configured: bool


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None


class ImpersonateRequest(BaseModel):
    target_tenant_id: str = Field(..., min_length=1)


class ImpersonateResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str = "readonly"
    tenant_id: str
    impersonator: bool = True


class SignupRequest(BaseModel):
    hospital_name: str = Field(..., min_length=1, max_length=255)
    admin_username: str = Field(..., min_length=3, max_length=255)
    admin_password: str = Field(..., min_length=8, max_length=128)
    admin_email: EmailStr
    admin_full_name: str = Field(default="", max_length=255)
    subscription_plan: str = Field(default="free_trial", max_length=50)
    subscription_billing_cycle: str = Field(default="monthly", max_length=16)

    @field_validator("admin_password")
    @classmethod
    def validate_admin_password(cls, value: str) -> str:
        return validate_password(value)

    @field_validator("admin_username")
    @classmethod
    def validate_admin_username(cls, value: str) -> str:
        return sanitize_username(value)

    @field_validator("subscription_billing_cycle")
    @classmethod
    def validate_billing_cycle(cls, value: str) -> str:
        allowed = {"monthly", "annual"}
        if value.lower() not in allowed:
            raise ValueError(f"billing_cycle must be one of {allowed}")
        return value.lower()


class SuperAdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1)


class SuperAdminTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    token_type: str = "Bearer"
    session_id: str
    not_before_policy: int = 0
    scope: str = "full"


class SignupResponse(BaseModel):
    tenant_id: str
    hospital_name: str
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    token_type: str = "Bearer"
