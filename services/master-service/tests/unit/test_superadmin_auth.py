"""Unit tests for 100% coverage of superadmin_auth.py in master-service.
"""
from unittest.mock import MagicMock
import pytest
from uuid import uuid4
from datetime import datetime, timezone

from app.exceptions import UnauthorizedError, BadRequestError
from app.services.superadmin_auth import (
    create_access_token,
    create_superadmin,
    authenticate_superadmin,
    decode_superadmin_token,
    update_superadmin_password,
    update_superadmin_role,
    _hash_password,
    _verify_password,
)

def test_hash_and_verify_password():
    pwd = "secretpassword"
    hashed = _hash_password(pwd)
    assert _verify_password(pwd, hashed) is True
    assert _verify_password("wrong", hashed) is False

def test_create_and_decode_access_token():
    tok = create_access_token("admin-id-1", "admin", "super_admin")
    payload = decode_superadmin_token(tok)
    assert payload["super_admin_id"] == "admin-id-1"
    assert payload["username"] == "admin"
    assert payload["role"] == "super_admin"

    # Invalid token type
    from jose import jwt
    from app.config import settings
    bad_type_tok = jwt.encode({"type": "regular"}, settings.secret_key, algorithm="HS256")
    with pytest.raises(UnauthorizedError, match="Invalid token type"):
        decode_superadmin_token(bad_type_tok)

def test_create_superadmin_success_and_duplicates():
    db = MagicMock()

    # Existing username check -> raises BadRequestError
    db.query.return_value.filter.return_value.first.return_value = MagicMock()
    with pytest.raises(BadRequestError, match="Username already exists"):
        create_superadmin(db, "admin", "a@b.com", "pass", "Admin User")

    # Existing email check -> raises BadRequestError
    db.query.return_value.filter.return_value.first.side_effect = [None, MagicMock()]
    with pytest.raises(BadRequestError, match="Email already exists"):
        create_superadmin(db, "admin", "a@b.com", "pass", "Admin User")

    # Success case
    db.query.return_value.filter.return_value.first.side_effect = [None, None]
    admin = create_superadmin(db, "admin_new", "new@b.com", "pass123", "New Admin")
    assert admin.username == "admin_new"
    assert hasattr(admin, "plaintext_backup_codes")
    assert len(admin.plaintext_backup_codes) == 10

def test_authenticate_superadmin():
    db = MagicMock()

    # Not found -> UnauthorizedError
    db.query.return_value.filter.return_value.first.return_value = None
    with pytest.raises(UnauthorizedError, match="Invalid username or password"):
        authenticate_superadmin(db, "usr", "pwd")

    # Inactive -> UnauthorizedError
    fake_admin = MagicMock(is_active=False)
    db.query.return_value.filter.return_value.first.return_value = fake_admin
    with pytest.raises(UnauthorizedError, match="Account is inactive"):
        authenticate_superadmin(db, "usr", "pwd")

    # Wrong password -> UnauthorizedError
    fake_admin.is_active = True
    fake_admin.password_hash = _hash_password("correctpass")
    with pytest.raises(UnauthorizedError, match="Invalid username or password"):
        authenticate_superadmin(db, "usr", "wrongpass")

    # Success
    res = authenticate_superadmin(db, "usr", "correctpass")
    assert res == fake_admin

def test_update_superadmin_password_and_role():
    db = MagicMock()
    fake_admin = MagicMock(password_hash="", role="support")

    update_superadmin_password(db, fake_admin, "newpassword")
    assert _verify_password("newpassword", fake_admin.password_hash) is True

    update_superadmin_role(db, fake_admin, "super_admin")
    assert fake_admin.role == "super_admin"


def test_decode_superadmin_token_expired_and_invalid():
    from jose import jwt
    from datetime import timedelta
    from app.config import settings

    # Expired token
    expired_payload = {
        "sub": "admin-123",
        "super_admin_id": "admin-123",
        "username": "admin",
        "role": "super_admin",
        "type": "superadmin",
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
    }
    expired_token = jwt.encode(expired_payload, settings.secret_key, algorithm="HS256")
    with pytest.raises(UnauthorizedError, match="Token has expired"):
        decode_superadmin_token(expired_token)

    # Invalid token signature
    invalid_token = jwt.encode({"type": "superadmin"}, "wrong_secret_key", algorithm="HS256")
    with pytest.raises(UnauthorizedError, match="Invalid token"):
        decode_superadmin_token(invalid_token)
