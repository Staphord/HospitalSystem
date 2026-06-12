from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1)


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
