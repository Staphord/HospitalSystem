from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import bcrypt
from jose import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.exceptions import UnauthorizedError, BadRequestError
from app.models.admin import SuperAdmin


ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(super_admin_id: str, username: str, role: str) -> str:
    to_encode = {
        "super_admin_id": str(super_admin_id),
        "username": username,
        "role": role,
        "type": "superadmin",
    }
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def create_superadmin(
    db: Session,
    username: str,
    email: str,
    password: str,
    full_name: str,
    role: str = "super_admin",
    mfa_secret: str | None = None,
) -> SuperAdmin:
    if db.query(SuperAdmin).filter(SuperAdmin.username == username).first():
        raise BadRequestError("Username already exists")
    if db.query(SuperAdmin).filter(SuperAdmin.email == email).first():
        raise BadRequestError("Email already exists")

    secret = mfa_secret or secrets.token_hex(16)
    admin = SuperAdmin(
        username=username,
        email=email,
        password_hash=_hash_password(password),
        full_name=full_name,
        role=role,
        mfa_secret=secret,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def authenticate_superadmin(db: Session, username: str, password: str) -> SuperAdmin:
    admin = (
        db.query(SuperAdmin)
        .filter(SuperAdmin.username == username)
        .first()
    )
    if not admin:
        raise UnauthorizedError("Invalid username or password")
    if not admin.is_active:
        raise UnauthorizedError("Account is inactive")
    if not _verify_password(password, admin.password_hash):
        raise UnauthorizedError("Invalid username or password")

    admin.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(admin)
    return admin


def decode_superadmin_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError("Token has expired")
    except jwt.JWTError:
        raise UnauthorizedError("Invalid token")

    if payload.get("type") != "superadmin":
        raise UnauthorizedError("Invalid token type")

    return payload


def update_superadmin_password(db: Session, admin: SuperAdmin, new_password: str) -> None:
    admin.password_hash = _hash_password(new_password)
    db.commit()


def update_superadmin_role(db: Session, admin: SuperAdmin, new_role: str) -> None:
    admin.role = new_role
    db.commit()
    db.refresh(admin)
