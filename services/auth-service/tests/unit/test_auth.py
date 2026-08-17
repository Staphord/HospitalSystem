from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from app.exceptions import BadRequestError, UnauthorizedError
from app.models.auth import RefreshToken
from app.services import auth as auth_service


def test_hash_token():
    token = "test-token-123"
    h1 = auth_service._hash_token(token)
    h2 = auth_service._hash_token(token)
    assert h1 == h2
    assert len(h1) == 64


def test_store_refresh_token(db_session):
    session_id = auth_service._store_refresh_token(
        db=db_session,
        keycloak_sub="sub-123",
        session_id="session-xyz",
        refresh_token="ref-abc",
        expires_in=3600,
        keycloak_realm="tenant-realm-1",
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert session_id == "session-xyz"

    record = db_session.query(RefreshToken).filter_by(session_id="session-xyz").first()
    assert record is not None
    assert record.keycloak_sub == "sub-123"
    assert record.keycloak_realm == "tenant-realm-1"
    assert record.is_revoked is False


@pytest.mark.asyncio
async def test_refresh_token_not_found(db_session):
    with pytest.raises(UnauthorizedError, match="Refresh token not found or revoked"):
        await auth_service.refresh_access_token("nonexistent-token", db_session)


@pytest.mark.asyncio
async def test_refresh_token_revoked(db_session):
    token = "revoked-token-123"
    auth_service._store_refresh_token(
        db=db_session,
        keycloak_sub="sub-123",
        session_id="session-revoked",
        refresh_token=token,
        expires_in=3600,
        keycloak_realm="tenant-realm-1",
    )
    rec = db_session.query(RefreshToken).filter_by(session_id="session-revoked").first()
    rec.is_revoked = True
    db_session.commit()

    with pytest.raises(UnauthorizedError, match="Refresh token not found or revoked"):
        await auth_service.refresh_access_token(token, db_session)


@pytest.mark.asyncio
async def test_refresh_token_expired(db_session):
    token = "expired-token-123"
    token_hash = auth_service._hash_token(token)
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    rec = RefreshToken(
        session_id="session-expired",
        keycloak_sub="sub-123",
        refresh_token_hash=token_hash,
        expires_at=past_time,
        is_revoked=False,
        keycloak_realm="tenant-realm-1",
    )
    db_session.add(rec)
    db_session.commit()

    with pytest.raises(UnauthorizedError, match="Refresh token expired"):
        await auth_service.refresh_access_token(token, db_session)

    db_session.refresh(rec)
    assert rec.is_revoked is True


@pytest.mark.asyncio
async def test_refresh_legacy_row_without_realm(db_session):
    token = "legacy-token-123"
    auth_service._store_refresh_token(
        db=db_session,
        keycloak_sub="sub-123",
        session_id="session-legacy",
        refresh_token=token,
        expires_in=3600,
        keycloak_realm=None,
    )

    with pytest.raises(UnauthorizedError, match="Session format outdated"):
        await auth_service.refresh_access_token(token, db_session)


@pytest.mark.asyncio
async def test_refresh_keycloak_401_revokes_token(db_session):
    token = "kc-401-token"
    auth_service._store_refresh_token(
        db=db_session,
        keycloak_sub="sub-123",
        session_id="session-401",
        refresh_token=token,
        expires_in=3600,
        keycloak_realm="tenant-realm-1",
    )

    mock_resp = AsyncMock()
    mock_resp.status_code = 401
    mock_resp.text = "invalid_grant"

    with patch("httpx.AsyncClient.post", return_value=mock_resp):
        with pytest.raises(UnauthorizedError, match="Refresh token expired or invalid"):
            await auth_service.refresh_access_token(token, db_session)

    rec = db_session.query(RefreshToken).filter_by(session_id="session-401").first()
    assert rec.is_revoked is True


@pytest.mark.asyncio
async def test_refresh_keycloak_5xx_does_not_revoke_token(db_session):
    token = "kc-500-token"
    auth_service._store_refresh_token(
        db=db_session,
        keycloak_sub="sub-123",
        session_id="session-500",
        refresh_token=token,
        expires_in=3600,
        keycloak_realm="tenant-realm-1",
    )

    mock_resp = AsyncMock()
    mock_resp.status_code = 500
    mock_resp.is_success = False
    mock_resp.text = "Internal Server Error"

    with patch("httpx.AsyncClient.post", return_value=mock_resp):
        with pytest.raises(BadRequestError, match="Token refresh service error"):
            await auth_service.refresh_access_token(token, db_session)

    rec = db_session.query(RefreshToken).filter_by(session_id="session-500").first()
    assert rec.is_revoked is False
