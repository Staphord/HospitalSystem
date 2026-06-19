from __future__ import annotations

import asyncio
import hashlib
import secrets
import aiosmtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import pyotp
from cachetools import TTLCache
from sqlalchemy.orm import Session

from app.core.config import settings
from app.exceptions import BadRequestError, UnauthorizedError
from app.models.admin import SuperAdmin
from app.models.auth import PasswordResetToken, RefreshToken
from app.models.user import User


def _keycloak_token_endpoint(realm: str | None = None) -> str:
    realm = realm or settings.keycloak_realm
    return f"{settings.keycloak_url}/realms/{realm}/protocol/openid-connect/token"


def _keycloak_logout_endpoint(realm: str | None = None) -> str:
    realm = realm or settings.keycloak_realm
    return f"{settings.keycloak_url}/realms/{realm}/protocol/openid-connect/logout"


async def login(username: str, password: str, db: Session, realm: str | None = None) -> Dict[str, Any]:
    data = {
        "grant_type": "password",
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
        "username": username,
        "password": password,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(_keycloak_token_endpoint(realm), data=data)

    if response.status_code == 401:
        raise UnauthorizedError("Invalid username or password")
    if response.status_code == 429:
        raise BadRequestError("Too many login attempts. Please try again later.")
    if not response.is_success:
        detail = response.text or "Authentication service unavailable"
        raise BadRequestError(f"Keycloak error ({response.status_code}): {detail}")

    token_data = response.json()
    user_sub, session_state = _extract_token_info(token_data["access_token"], token_data.get("session_state"))
    session_id = _store_refresh_token(
        db=db,
        keycloak_sub=user_sub,
        session_id=session_state,
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

    user_sub, session_state = _extract_token_info(token_data["access_token"], token_data.get("session_state"))
    session_id = _store_refresh_token(
        db=db,
        keycloak_sub=user_sub,
        session_id=session_state,
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


async def logout(refresh_token: str, db: Session, realm: str | None = None) -> None:
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
            await client.post(_keycloak_logout_endpoint(realm), data=data)
    except httpx.RequestError:
        pass


async def revoke_all_sessions(keycloak_sub: str, db: Session) -> int:
    revoked = db.query(RefreshToken).filter(
        RefreshToken.keycloak_sub == keycloak_sub,
        RefreshToken.is_revoked == False,
    ).update({"is_revoked": True})
    db.commit()
    return revoked


def _extract_token_info(access_token: str, session_state_fallback: Optional[str] = None) -> tuple[str, str]:
    from jose import jwt as _jwt
    try:
        unverified = _jwt.get_unverified_claims(access_token)
        user_sub = unverified.get("sub", "")
        session_state = session_state_fallback or unverified.get("session_state") or unverified.get("sid") or secrets.token_urlsafe(24)
        return user_sub, session_state
    except Exception:
        return "", session_state_fallback or secrets.token_urlsafe(24)


def _store_refresh_token(
    db: Session, keycloak_sub: str, session_id: str, refresh_token: str, expires_in: int
) -> str:
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


TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


async def _send_email(email: str, reset_link: str) -> None:
    if not settings.smtp_user or not settings.smtp_password:
        print("\n" + "="*80)
        print(f" MOCK EMAIL DISPATCH TO: {email}")
        print(f" Subject: Reset Your Password - HospitalFlow")
        print(f" Reset Link: {reset_link}")
        print("="*80 + "\n")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Reset Your Password - HospitalFlow"
        msg["From"] = settings.smtp_from
        msg["To"] = email

        # Read template files or fall back to defaults
        try:
            html_path = TEMPLATES_DIR / "password_reset.html"
            with open(html_path, "r", encoding="utf-8") as f:
                html = f.read().format(
                    frontend_url=settings.frontend_url,
                    reset_link=reset_link,
                    current_year=datetime.now().year
                )
        except Exception as te:
            print(f"[WARNING] Could not load HTML email template from file: {str(te)}. Using fallback.")
            html = f"""
            <html>
            <body>
              <h2>Reset Your Password</h2>
              <p>Click the link below to choose a new password:</p>
              <a href="{reset_link}">Reset Password</a>
              <p>Or paste this link into your browser:</p>
              <p>{reset_link}</p>
            </body>
            </html>
            """

        try:
            text_path = TEMPLATES_DIR / "password_reset.txt"
            with open(text_path, "r", encoding="utf-8") as f:
                text = f.read().format(reset_link=reset_link)
        except Exception as te:
            print(f"[WARNING] Could not load text email template from file: {str(te)}. Using fallback.")
            text = f"Reset your password by clicking here: {reset_link}"

        part1 = MIMEText(text, "plain")
        part2 = MIMEText(html, "html")
        msg.attach(part1)
        msg.attach(part2)

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=True if settings.smtp_port == 587 else False,
            use_tls=True if settings.smtp_port == 465 else False,
        )
        print(f"[SUCCESS] Real HTML email successfully sent via aiosmtplib to {email}")
    except Exception as e:
        print(f"[ERROR] Failed to send real email to {email}: {str(e)}")


async def _user_exists_in_keycloak(email: str) -> bool:
    try:
        admin_url = f"{settings.keycloak_url}/admin/realms/{settings.keycloak_realm}/users"
        async with httpx.AsyncClient(timeout=5.0) as client:
            admin_token = await _get_admin_token()
            headers = {"Authorization": f"Bearer {admin_token}"}
            resp = await client.get(f"{admin_url}?email={email}", headers=headers)
            if resp.is_success:
                users = resp.json()
                return len(users) > 0
    except Exception as e:
        print(f"[ERROR] Keycloak user existence check failed: {str(e)}")
    return False


async def request_password_reset(email: str, db: Session) -> None:
    # 1. Check local DB (User & SuperAdmin)
    user_exists = (
        db.query(User).filter(User.email == email).first() is not None
        or db.query(SuperAdmin).filter(SuperAdmin.email == email).first() is not None
    )
    # 2. Check Keycloak if not in local DB
    if not user_exists:
        user_exists = await _user_exists_in_keycloak(email)

    if not user_exists:
        raise BadRequestError("Email address not found")

    # Invalidate existing tokens
    db.query(PasswordResetToken).filter(
        PasswordResetToken.email == email,
        PasswordResetToken.is_used == False,
    ).update({PasswordResetToken.is_used: True}, synchronize_session=False)

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    record = PasswordResetToken(
        email=email,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.password_reset_token_ttl),
    )
    db.add(record)
    db.commit()

    reset_link = f"{settings.frontend_url}/reset-password?token={token}"
    await _send_email(email, reset_link)


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
        
        # Sync SuperAdmin password locally if they exist in the local super_admins table
        db_admin = db.query(SuperAdmin).filter(SuperAdmin.email == record.email).first()
        if db_admin:
            from app.services.superadmin_auth import _hash_password
            db_admin.password_hash = _hash_password(new_password)
            db.add(db_admin)
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


def is_valid_totp_secret(secret: str | None) -> bool:
    if not secret:
        return False
    try:
        pyotp.TOTP(secret).now()
        return True
    except Exception:
        return False


def generate_mfa_secret(keycloak_sub: str) -> Dict[str, str]:
    secret = _totp_secrets.get(keycloak_sub)
    if not secret:
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


def get_pending_mfa_secret(keycloak_sub: str) -> str | None:
    return _totp_secrets.get(keycloak_sub)


def clear_pending_mfa_secret(keycloak_sub: str) -> None:
    _totp_secrets.pop(keycloak_sub, None)


def verify_mfa_totp(
    keycloak_sub: str,
    totp_code: str,
    db: Session | None = None,
    secret: str | None = None,
) -> bool:
    if secret is None and db is not None:
        user = db.query(User).filter(User.keycloak_sub == keycloak_sub).first()
        if user and user.mfa_secret:
            secret = user.mfa_secret

    if secret is None:
        secret = _totp_secrets.get(keycloak_sub)

    if not secret:
        return False

    try:
        totp = pyotp.TOTP(secret)
        return totp.verify(totp_code, valid_window=2)
    except Exception:
        return False


def verify_backup_code(user_record: Any, code: str, db: Session) -> bool:
    """
    Verify a single-use backup recovery code against the stored hashed list.
    If the code matches, it is removed from the list (consumed) and the
    database is updated immediately.

    Args:
        user_record: SQLAlchemy model instance with a `backup_codes` JSON column.
        code:        The plain-text backup code supplied by the user.
        db:          The SQLAlchemy session the user_record belongs to.

    Returns:
        True if the code matched and was consumed, False otherwise.
    """
    import json as _json
    if not user_record or not user_record.backup_codes:
        return False

    try:
        stored: list[str] = _json.loads(user_record.backup_codes)
    except (ValueError, TypeError):
        return False

    code_hash = hashlib.sha256(code.strip().encode()).hexdigest()
    if code_hash not in stored:
        return False

    # Consume the code — it is single-use
    stored.remove(code_hash)
    user_record.backup_codes = _json.dumps(stored)
    db.add(user_record)
    db.commit()
    return True
