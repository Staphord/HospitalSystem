from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.v1.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    MFAVerifyRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    TokenResponse,
)
from app.core.database import get_db
from app.core.limiter import limiter
from app.core.security import TokenPayload, get_current_active_user
from app.exceptions import BadRequestError
from app.services import auth as auth_service

public_router = APIRouter()
router = APIRouter(dependencies=[Depends(get_current_active_user)])


@public_router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginRequest,
    db: Session = Depends(get_db),
) -> dict:
    result = await auth_service.login(
        username=body.username,
        password=body.password,
        db=db,
    )
    return result


@public_router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
async def refresh(
    request: Request,
    body: RefreshRequest,
    db: Session = Depends(get_db),
) -> dict:
    result = await auth_service.refresh_access_token(
        refresh_token=body.refresh_token,
        db=db,
    )
    return result


@public_router.post("/password-reset", status_code=202)
@limiter.limit("3/minute")
async def password_reset_request(
    request: Request,
    body: PasswordResetRequest,
    db: Session = Depends(get_db),
) -> dict:
    await auth_service.request_password_reset(
        email=body.email,
        db=db,
    )
    return {"detail": "If the email exists, a reset link has been sent"}


@public_router.post("/password-reset/confirm", status_code=200)
@limiter.limit("5/minute")
async def password_reset_confirm(
    request: Request,
    body: PasswordResetConfirm,
    db: Session = Depends(get_db),
) -> dict:
    await auth_service.confirm_password_reset(
        token=body.token,
        new_password=body.new_password,
        db=db,
    )
    return {"detail": "Password reset successfully"}


@router.post("/logout", status_code=204)
@limiter.limit("30/minute")
async def logout(
    request: Request,
    body: LogoutRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> None:
    await auth_service.logout(
        refresh_token=body.refresh_token,
        db=db,
    )


@router.post("/logout-all", status_code=204)
@limiter.limit("10/minute")
async def logout_all(
    request: Request,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> None:
    await auth_service.revoke_all_sessions(
        keycloak_sub=user.sub,
        db=db,
    )


@router.post("/mfa/setup", response_model=dict)
@limiter.limit("10/minute")
async def mfa_setup(
    request: Request,
    user: TokenPayload = Depends(get_current_active_user),
) -> dict:
    return auth_service.generate_mfa_secret(keycloak_sub=user.sub)


@router.post("/mfa/verify", response_model=dict)
@limiter.limit("10/minute")
async def mfa_verify(
    request: Request,
    body: MFAVerifyRequest,
    user: TokenPayload = Depends(get_current_active_user),
) -> dict:
    valid = auth_service.verify_mfa_totp(
        keycloak_sub=user.sub,
        totp_code=body.totp_code,
    )
    if not valid:
        raise BadRequestError("Invalid TOTP code")
    return {"detail": "MFA verified successfully"}
