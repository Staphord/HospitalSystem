from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx
import pyotp
from cachetools import TTLCache
from sqlalchemy.orm import Session

from app.core.config import settings
from app.exceptions import BadRequestError, UnauthorizedError
from app.models.auth import PasswordResetToken, RefreshToken


def _keycloak_token_endpoint() -> str:
    return f"{settings.keycloak_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/token"


def _keycloak_logout_endpoint() -> str:
    return f"{settings.keycloak_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/logout"


async def login(username: str, password: str, db: Session) -> Dict[str, Any]:
    data = {
        "grant_type": "password",
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
        "username": username,
        "password": password,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(_keycloak_token_endpoint(), data=data)

    if response.status_code == 401:
        raise UnauthorizedError("Invalid username or password")
    if response.status_code == 429:
        raise BadRequestError("Too many login attempts. Please try again later.")
    if not response.is_success:
        raise BadRequestError("Authentication service unavailable")

    token_data = response.json()
    session_id = _store_refresh_token(
        db=db,
        keycloak_sub=token_data.get("session_state", ""),
        refresh_token=token_data["refresh_token"],
        expires_in=token_data.get("refresh_expires_in", 1800),
    )

    return {
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "expires_in": token_data.get("expires_in", 300),
        "refresh_expires_in": token_data.get("refresh_expires_in", 1800),
        "token_type": "Bearer",
        "session_id": session_id,
        "not_before_policy": token_data.get("not-before-policy", 0),
    }


async def refresh_access_token(refresh_token: str, db: Session) -> Dict[str, Any]:
    db_record = db.query(RefreshToken).filter(
        RefreshToken.refresh_token_hash == _hash_token(refresh_token),
        RefreshToken.is_revoked == False,
    ).first()

    if not db_record:
        raise UnauthorizedError("Refresh token not found or revoked")

    if db_record.expires_at < datetime.now(timezone.utc):
        db_record.is_revoked = True
        db.commit()
        raise UnauthorizedError("Refresh token expired")

    data = {
        "grant_type": "refresh_token",
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(_keycloak_token_endpoint(), data=data)

    if response.status_code == 401:
        db_record.is_revoked = True
        db.commit()
        raise UnauthorizedError("Invalid refresh token")

    if not response.is_success:
        raise BadRequestError("Token refresh service unavailable")

    token_data = response.json()

    db_record.is_revoked = True
    db.commit()

    session_id = _store_refresh_token(
        db=db,
        keycloak_sub=token_data.get("session_state", ""),
        refresh_token=token_data["refresh_token"],
        expires_in=token_data.get("refresh_expires_in", 1800),
    )

    return {
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "expires_in": token_data.get("expires_in", 300),
        "refresh_expires_in": token_data.get("refresh_expires_in", 1800),
        "token_type": "Bearer",
        "session_id": session_id,
        "not_before_policy": token_data.get("not-before-policy", 0),
    }


async def logout(refresh_token: str, db: Session) -> None:
    db_record = db.query(RefreshToken).filter(
        RefreshToken.refresh_token_hash == _hash_token(refresh_token),
        RefreshToken.is_revoked == False,
    ).first()

    if db_record:
        db_record.is_revoked = True
        db.commit()

    try:
        data = {
            "client_id": settings.keycloak_client_id,
            "client_secret": settings.keycloak_client_secret,
            "refresh_token": refresh_token,
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(_keycloak_logout_endpoint(), data=data)
    except httpx.RequestError:
        pass


async def revoke_all_sessions(keycloak_sub: str, db: Session) -> int:
    revoked = db.query(RefreshToken).filter(
        RefreshToken.keycloak_sub == keycloak_sub,
        RefreshToken.is_revoked == False,
    ).update({"is_revoked": True})
    db.commit()
    return revoked


def _store_refresh_token(
    db: Session, keycloak_sub: str, refresh_token: str, expires_in: int
) -> str:
    session_id = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    record = RefreshToken(
        session_id=session_id,
        keycloak_sub=keycloak_sub,
        refresh_token_hash=_hash_token(refresh_token),
        expires_at=expires_at,
    )
    db.add(record)
    db.commit()
    return session_id


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def request_password_reset(email: str, db: Session) -> None:
    existing = db.query(PasswordResetToken).filter(
        PasswordResetToken.email == email,
        PasswordResetToken.is_used == False,
        PasswordResetToken.expires_at > datetime.now(timezone.utc),
    ).first()
    if existing:
        return

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    record = PasswordResetToken(
        email=email,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(record)
    db.commit()


async def confirm_password_reset(token: str, new_password: str, db: Session) -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash,
        PasswordResetToken.is_used == False,
        PasswordResetToken.expires_at > datetime.now(timezone.utc),
    ).first()

    if not record:
        raise BadRequestError("Invalid or expired password reset token")

    try:
        data = {
            "client_id": settings.keycloak_client_id,
            "client_secret": settings.keycloak_client_secret,
            "username": record.email,
            "password": new_password,
            "temporary": "false",
        }
        admin_url = f"{settings.keycloak_url}/admin/realms/{settings.keycloak_realm}/users"
        async with httpx.AsyncClient(timeout=10.0) as client:
            admin_token = await _get_admin_token()
            headers = {"Authorization": f"Bearer {admin_token}"}
            user_resp = await client.get(
                f"{admin_url}?email={record.email}",
                headers=headers,
            )
            if user_resp.is_success and user_resp.json():
                user_id = user_resp.json()[0]["id"]
                await client.put(
                    f"{admin_url}/{user_id}/reset-password",
                    headers=headers,
                    json={"type": "password", "value": new_password, "temporary": False},
                )
    except httpx.RequestError:
        raise BadRequestError("Password reset service unavailable")

    record.is_used = True
    db.commit()


async def _get_admin_token() -> str:
    data = {
        "grant_type": "password",
        "client_id": "admin-cli",
        "username": settings.keycloak_admin_username,
        "password": settings.keycloak_admin_password,
    }
    url = f"{settings.keycloak_url}/realms/master/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, data=data)
        response.raise_for_status()
        return response.json()["access_token"]


_totp_secrets: TTLCache[str, str] = TTLCache(maxsize=1024, ttl=600)


def generate_mfa_secret(keycloak_sub: str) -> Dict[str, str]:
    secret = pyotp.random_base32()
    _totp_secrets[keycloak_sub] = secret

    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name=keycloak_sub, issuer_name="HospitalFlow"
    )

    return {
        "secret": secret,
        "qr_code_url": provisioning_uri,
    }


def verify_mfa_totp(keycloak_sub: str, totp_code: str) -> bool:
    secret = _totp_secrets.get(keycloak_sub)
    if not secret:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(totp_code, valid_window=1)
