from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

from app.core.config import settings
from app.services import auth as auth_service
from app.models.auth import PasswordResetToken
from app.models.user import User


class Client:
    def __init__(self, response): self.response = response
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def post(self, *args, **kwargs): return self.response
    async def get(self, *args, **kwargs): return self.response
    async def put(self, *args, **kwargs): return self.response


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status; self._body = body or {}; self.is_success = 200 <= status < 300; self.text = str(self._body)
    def json(self): return self._body
    def raise_for_status(self):
        if not self.is_success: raise RuntimeError(self.status_code)


@pytest.mark.asyncio
async def test_login_and_refresh_rotation(db_session, monkeypatch):
    access = jwt.encode({"sub": "sub", "sid": "sid"}, settings.secret_key, algorithm="HS256")
    response = Response(body={"access_token": access, "refresh_token": "next", "expires_in": 60, "refresh_expires_in": 120})
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: Client(response))
    result = await auth_service.login("user", "Password1!", db_session)
    assert result["user_sub"] == "sub"
    assert db_session.query(PasswordResetToken).count() == 0


@pytest.mark.asyncio
async def test_auth_password_reset_and_mfa_email(db_session, monkeypatch):
    db_session.add(User(keycloak_sub="sub", email="u@example.com", username="u")); db_session.commit()
    monkeypatch.setattr(auth_service, "_send_email", AsyncMock())
    await auth_service.request_password_reset("u@example.com", db_session)
    record = db_session.query(PasswordResetToken).first()
    assert record is not None
    with pytest.raises(Exception): await auth_service.confirm_password_reset("bad", "NewPassword1!", db_session)
    monkeypatch.setattr(auth_service.settings, "smtp_user", "")
    monkeypatch.setattr(auth_service.settings, "smtp_password", "")
    await auth_service.send_mfa_email_code("u@example.com", "123456")


@pytest.mark.asyncio
async def test_confirm_password_reset_success(db_session, monkeypatch):
    import hashlib, secrets
    token = "reset-token"
    record = PasswordResetToken(email="u@example.com", token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=auth_service.datetime.now(auth_service.timezone.utc) + auth_service.timedelta(hours=1))
    db_session.add(record); db_session.commit()
    class ResetClient(Client):
        async def post(self, *args, **kwargs): return Response(body={"access_token": "admin"})
        async def get(self, *args, **kwargs): return Response(body=[{"id": "user-id"}])
        async def put(self, *args, **kwargs): return Response(body={})
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: ResetClient(Response(body={})))
    await auth_service.confirm_password_reset(token, "N3w!CedarRiver", db_session)
    db_session.refresh(record)
    assert record.is_used is True


@pytest.mark.asyncio
async def test_auth_network_and_keycloak_error_paths(db_session, monkeypatch):
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: Client(Response(401, {"error": "invalid"})))
    with pytest.raises(Exception): await auth_service.login("u", "p", db_session)


@pytest.mark.asyncio
async def test_auth_refresh_network_and_success_paths(db_session, monkeypatch):
    access = jwt.encode({"sub": "sub", "sid": "new-sid"}, settings.secret_key, algorithm="HS256")
    auth_service._store_refresh_token(db_session, "sub", "old-sid", "old", 3600, "realm", "ip", "agent")
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: Client(Response(status=500, body={"access_token": access, "refresh_token": "new"})))
    with pytest.raises(Exception): await auth_service.refresh_access_token("old", db_session)

    response = Response(body={"access_token": access, "refresh_token": "new", "expires_in": 60, "refresh_expires_in": 120})
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: Client(response))
    result = await auth_service.refresh_access_token("old", db_session)
    assert result["refresh_token"] == "new"


@pytest.mark.asyncio
async def test_auth_logout_and_email_branches(db_session, monkeypatch):
    auth_service._store_refresh_token(db_session, "sub", "sid", "logout-token", 3600, "realm")
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: Client(Response()))
    await auth_service.logout("logout-token", db_session)
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: Client(Response()))
    monkeypatch.setattr(auth_service.settings, "smtp_user", "smtp")
    monkeypatch.setattr(auth_service.settings, "smtp_password", "secret")
    monkeypatch.setattr(auth_service.aiosmtplib, "send", AsyncMock())
    await auth_service._send_email("u@example.com", "http://reset")
    await auth_service.send_mfa_email_code("u@example.com", "123456")


@pytest.mark.asyncio
async def test_auth_keycloak_lookup_and_totp_db_path(db_session, monkeypatch):
    class Lookup(Client):
        async def get(self, *args, **kwargs): return Response(body=[{"id": "u"}])
        async def post(self, *args, **kwargs): return Response(body={"access_token": "a"})
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: Lookup(Response()))
    monkeypatch.setattr(auth_service, "_get_admin_token", AsyncMock(return_value="a"))
    assert await auth_service._user_exists_in_keycloak("u@example.com") is True
    user = User(keycloak_sub="totp-sub", email="t@example.com", mfa_secret=auth_service.pyotp.random_base32())
    db_session.add(user); db_session.commit()
    assert auth_service.verify_mfa_totp("totp-sub", "bad", db_session) is False
    assert auth_service._extract_token_info("bad", "fallback")[1] == "fallback"
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: Client(Response(500, {})))
    with pytest.raises(Exception): await auth_service.login("u", "p", db_session)


@pytest.mark.asyncio
async def test_auth_login_status_and_refresh_validation_paths(db_session, monkeypatch):
    for status in (429, 503):
        monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: Client(Response(status, {"detail": "down"})))
        with pytest.raises(Exception):
            await auth_service.login("u", "p", db_session)

    with pytest.raises(Exception):
        await auth_service.refresh_access_token("missing", db_session, request_id="req-1")
    auth_service._store_refresh_token(db_session, "sub", "expired", "expired", -1, "realm")
    with pytest.raises(Exception):
        await auth_service.refresh_access_token("expired", db_session)
    auth_service._store_refresh_token(db_session, "sub", "legacy", "legacy", 3600, None)
    with pytest.raises(Exception):
        await auth_service.refresh_access_token("legacy", db_session)
    auth_service._store_refresh_token(db_session, "sub", "revoked", "revoked", 3600, "realm")
    row = db_session.query(auth_service.RefreshToken).filter_by(session_id="revoked").first()
    row.is_revoked = True
    db_session.commit()
    with pytest.raises(Exception):
        await auth_service.refresh_access_token("revoked", db_session)


@pytest.mark.asyncio
async def test_auth_refresh_rejection_and_network_preserve_session(db_session, monkeypatch):
    auth_service._store_refresh_token(db_session, "sub", "refresh-edge", "refresh-edge", 3600, "realm")
    for status in (400, 401):
        monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: Client(Response(status, {"error": "invalid"})))
        with pytest.raises(Exception):
            await auth_service.refresh_access_token("refresh-edge", db_session)
        # recreate the record because explicit Keycloak rejection revokes it
        auth_service._store_refresh_token(db_session, "sub", f"refresh-edge-{status}", f"refresh-edge-{status}", 3600, "realm")
    class FailingClient(Client):
        async def post(self, *args, **kwargs):
            raise auth_service.httpx.RequestError("network")
    auth_service._store_refresh_token(db_session, "sub", "refresh-network", "refresh-network", 3600, "realm")
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: FailingClient(Response()))
    with pytest.raises(Exception):
        await auth_service.refresh_access_token("refresh-network", db_session)
    assert db_session.query(auth_service.RefreshToken).filter_by(session_id="refresh-network").first().is_revoked is False


@pytest.mark.asyncio
async def test_auth_logout_fallback_and_reset_lookup_paths(db_session, monkeypatch):
    class FailingClient(Client):
        async def post(self, *args, **kwargs):
            raise auth_service.httpx.RequestError("network")
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: FailingClient(Response()))
    await auth_service.logout("not-in-db", db_session, realm="master")
    monkeypatch.setattr(auth_service, "_user_exists_in_keycloak", AsyncMock(return_value=False))
    with pytest.raises(Exception):
        await auth_service.request_password_reset("missing@example.com", db_session)
    monkeypatch.setattr(auth_service, "_user_exists_in_keycloak", AsyncMock(return_value=True))
    monkeypatch.setattr(auth_service, "_send_email", AsyncMock())
    await auth_service.request_password_reset("remote@example.com", db_session)
    assert db_session.query(PasswordResetToken).filter_by(email="remote@example.com").count() == 1


def test_auth_token_and_backup_edge_paths(db_session):
    assert auth_service._extract_token_info("bad-token")[0] == ""
    assert auth_service.verify_mfa_totp("missing", "000000") is False
    assert auth_service.verify_backup_code(None, "code", db_session) is False
    user = User(keycloak_sub="bad-backup", backup_codes="not-json")
    assert auth_service.verify_backup_code(user, "code", db_session) is False
    user.backup_codes = "[]"
    assert auth_service.verify_backup_code(user, "code", db_session) is False


@pytest.mark.asyncio
async def test_auth_email_fallbacks_and_existing_refresh_record(db_session, monkeypatch):
    auth_service._store_refresh_token(db_session, "old", "same", "one", 60, "realm", "ip", "agent")
    auth_service._store_refresh_token(db_session, "new", "same", "two", 120, "new-realm", "new-ip", "new-agent")
    row = db_session.query(auth_service.RefreshToken).filter_by(session_id="same").first()
    assert row.keycloak_sub == "new" and row.keycloak_realm == "new-realm"

    monkeypatch.setattr(auth_service.settings, "smtp_user", "smtp")
    monkeypatch.setattr(auth_service.settings, "smtp_password", "secret")
    monkeypatch.setattr(auth_service, "TEMPLATES_DIR", auth_service.Path("/does/not/exist"))
    monkeypatch.setattr(auth_service.aiosmtplib, "send", AsyncMock(side_effect=RuntimeError("smtp")))
    await auth_service._send_email("u@example.com", "http://reset")
    await auth_service.send_mfa_email_code("u@example.com", "123456")


@pytest.mark.asyncio
async def test_auth_reset_remote_and_request_failures(db_session, monkeypatch):
    token = "remote-reset"
    import hashlib
    record = PasswordResetToken(email="remote@example.com", token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=auth_service.datetime.now(auth_service.timezone.utc) + auth_service.timedelta(hours=1))
    db_session.add(record); db_session.commit()
    class NoUserClient(Client):
        async def post(self, *args, **kwargs): return Response(body={"access_token": "admin"})
        async def get(self, *args, **kwargs): return Response(body=[])
        async def put(self, *args, **kwargs): return Response(body={})
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: NoUserClient(Response()))
    await auth_service.confirm_password_reset(token, "N3w!CedarRiver", db_session)
    assert record.is_used is True

    record2 = PasswordResetToken(email="error@example.com", token_hash=hashlib.sha256(b"error").hexdigest(), expires_at=auth_service.datetime.now(auth_service.timezone.utc) + auth_service.timedelta(hours=1))
    db_session.add(record2); db_session.commit()
    class ErrorClient(Client):
        async def post(self, *args, **kwargs): raise auth_service.httpx.RequestError("down")
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kwargs: ErrorClient(Response()))
    with pytest.raises(Exception):
        await auth_service.confirm_password_reset("error", "N3w!CedarRiver", db_session)


def test_auth_totp_invalid_secret_and_db_lookup(db_session):
    user = User(keycloak_sub="db-totp", mfa_secret="invalid")
    db_session.add(user); db_session.commit()
    assert auth_service.is_valid_totp_secret("invalid") is False
    assert auth_service.verify_mfa_totp("db-totp", "000000", db_session) is False
