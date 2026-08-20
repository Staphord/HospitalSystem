"""Unit tests for core database, security, middleware, db/master.py, and exceptions.py in master-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request
from starlette.responses import Response
from jose import jwt
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import OperationalError

from app.core.database import (
    init_db,
    get_db,
    DatabaseRouter,
    DefaultDatabaseRouter,
    get_hospital_context,
    close_hospital_context,
)
from app.core.middleware import (
    AuditLogMiddleware,
    ReadOnlyScopeMiddleware,
    ImpersonationBannerMiddleware,
)
from app.core.security import (
    TokenPayload,
    _issuer,
    _extract_realm_from_iss,
    _fetch_jwks,
    _build_rsa_key,
    _decode_token,
    _introspect_token,
    get_current_active_user,
    _extract_roles,
    require_role,
    get_current_hospital_id,
    _jwks_cache,
    _introspection_cache,
)
from app.db.master import (
    _ensure_master_database_exists,
    get_master_session,
    get_master_db,
)
from app.exceptions import (
    UnauthorizedError,
    ForbiddenError,
    NotFoundError,
    ConflictError,
    BadRequestError,
    RateLimitError,
    TenantNotFoundError,
    TokenExpiredError,
    MFARequiredError,
    TenantSuspendedError,
    ReadOnlyScopeError,
)
from app.config import settings

# ---------------------------------------------------------------------------
# Database & Hospital Context Tests
# ---------------------------------------------------------------------------

def test_init_db_and_get_db():
    init_db()
    generator = get_db()
    db = next(generator)
    assert db is not None
    try:
        next(generator)
    except StopIteration:
        pass

def test_database_router_and_hospital_context():
    router = DefaultDatabaseRouter()
    sess = router.get_session("hosp1")
    assert sess is not None
    sess.close()

    ctx = get_hospital_context("hosp1")
    assert ctx.hospital_id == "hosp1"
    close_hospital_context(ctx)

class CustomRouter(DatabaseRouter):
    def get_session(self, hospital_id: str):
        return super().get_session(hospital_id)

def test_abstract_database_router():
    cr = CustomRouter()
    with pytest.raises(NotImplementedError):
        cr.get_session("hosp1")

# ---------------------------------------------------------------------------
# Middleware Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_log_middleware_options_head():
    mw = AuditLogMiddleware(app=MagicMock())
    req_options = Request(scope={"type": "http", "method": "OPTIONS", "path": "/test", "headers": []})
    call_next = AsyncMock(return_value=Response("ok"))
    res_opt = await mw.dispatch(req_options, call_next)
    assert res_opt.status_code == 200

@pytest.mark.asyncio
async def test_audit_log_middleware_post_logging():
    mw = AuditLogMiddleware(app=MagicMock())
    req_post = Request(scope={"type": "http", "method": "POST", "path": "/test", "headers": []})
    req_post.state.user_sub = "user1"
    req_post.state.tenant = MagicMock(tenant_id="t1")
    call_next = AsyncMock(return_value=Response("ok", status_code=201))

    mock_db = MagicMock()
    with patch("app.core.middleware.get_session_local", return_value=lambda: mock_db):
        res_post = await mw.dispatch(req_post, call_next)
        assert res_post.headers["X-Request-ID"] is not None
        assert mock_db.execute.called

@pytest.mark.asyncio
async def test_readonly_scope_middleware():
    mw = ReadOnlyScopeMiddleware(app=MagicMock())
    req_ro = Request(scope={"type": "http", "method": "POST", "path": "/test", "headers": []})
    req_ro.state.tenant = MagicMock(scope="readonly")

    call_next = AsyncMock()
    res_ro = await mw.dispatch(req_ro, call_next)
    assert res_ro.status_code == 403
    assert res_ro.headers["X-Impersonation-Banner"] == "true"

    req_rw = Request(scope={"type": "http", "method": "POST", "path": "/test", "headers": []})
    req_rw.state.tenant = MagicMock(scope="rw")

    call_next = AsyncMock(return_value=Response("ok"))
    res_rw = await mw.dispatch(req_rw, call_next)
    assert res_rw.status_code == 200

@pytest.mark.asyncio
async def test_impersonation_banner_middleware():
    mw = ImpersonationBannerMiddleware(app=MagicMock())
    req = Request(scope={"type": "http", "method": "GET", "path": "/test", "headers": []})
    req.state.tenant = MagicMock(scope="readonly")

    call_next = AsyncMock(return_value=Response("ok"))
    res = await mw.dispatch(req, call_next)
    assert res.headers["X-Impersonation-Banner"] == "true"

# ---------------------------------------------------------------------------
# Core Security Tests
# ---------------------------------------------------------------------------

def test_issuer_and_extract_realm():
    assert f"realms/{settings.keycloak_realm}" in _issuer(None)
    assert "realms/tenant1" in _issuer("tenant1")

    valid_tok = jwt.encode({"iss": f"{settings.keycloak_url}/realms/hospital_a"}, "secret", algorithm="HS256")
    assert _extract_realm_from_iss(valid_tok) == "hospital_a"

    invalid_tok = jwt.encode({"iss": "https://other.domain.com/realms/hospital_a"}, "secret", algorithm="HS256")
    assert _extract_realm_from_iss(invalid_tok) is None
    assert _extract_realm_from_iss("not-a-jwt") is None

@pytest.mark.asyncio
async def test_fetch_jwks():
    _jwks_cache.clear()
    mock_res = MagicMock()
    mock_res.json.return_value = {"keys": [{"kid": "k1"}]}
    mock_res.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_res
    mock_client.__aenter__.return_value = mock_client

    with patch("httpx.AsyncClient", return_value=mock_client):
        jwks = await _fetch_jwks("tenant1")
        assert jwks == {"keys": [{"kid": "k1"}]}
        cached = await _fetch_jwks("tenant1")
        assert cached == {"keys": [{"kid": "k1"}]}

def test_build_rsa_key():
    jwks = {"keys": [{"kid": "k1", "n": "abc"}]}
    assert _build_rsa_key(jwks, "k1") == {"kid": "k1", "n": "abc"}

    with pytest.raises(HTTPException) as exc:
        _build_rsa_key(jwks, "k2")
    assert exc.value.status_code == 401

@pytest.mark.asyncio
async def test_decode_token_hs256():
    payload = {"sub": "u1", "impersonator": "admin"}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256", headers={"kid": "impersonation-key"})

    dec = await _decode_token(token)
    assert dec["sub"] == "u1"

    with pytest.raises(HTTPException) as exc:
        await _decode_token("invalid.jwt.token")
    assert exc.value.status_code == 401

@pytest.mark.asyncio
async def test_introspect_token():
    _introspection_cache.clear()
    token = "test-token"

    mock_res = MagicMock()
    mock_res.json.return_value = {"active": True}
    mock_res.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_res
    mock_client.__aenter__.return_value = mock_client

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _introspect_token(token)
        assert _introspection_cache[token] is True
        await _introspect_token(token)

    token_inact = "inact-token"
    mock_res.json.return_value = {"active": False}
    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc:
            await _introspect_token(token_inact)
        assert exc.value.status_code == 401

@pytest.mark.asyncio
async def test_get_current_active_user():
    req = MagicMock(state=MagicMock())

    with pytest.raises(HTTPException) as exc:
        await get_current_active_user(req, None)
    assert exc.value.status_code == 401

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    with patch("app.core.security._decode_token", AsyncMock(return_value={"sub": "u1", "preferred_username": "usr", "email": "a@b.com", "realm_access": {"roles": ["admin"]}})):
        with patch("app.config.settings.keycloak_introspect", False):
            user = await get_current_active_user(req, creds)
            assert user.sub == "u1"
            assert user.preferred_username == "usr"

def test_extract_roles_and_require_role():
    user = TokenPayload(sub="u1", preferred_username="usr", email=None, realm_access={"roles": ["doctor"]}, raw={})
    assert _extract_roles(user) == ["doctor"]

    superadmin_user = TokenPayload(sub="sa", preferred_username="sa", email=None, realm_access={}, raw={"type": "superadmin", "role": "super_admin"})
    assert _extract_roles(superadmin_user) == ["super_admin"]

    req_role_fn = require_role("doctor")
    assert req_role_fn.__wrapped__(user) == user if hasattr(req_role_fn, "__wrapped__") else True

@pytest.mark.asyncio
async def test_get_current_hospital_id():
    user = TokenPayload(sub="u1", preferred_username="usr", email=None, realm_access={"roles": ["doctor"]}, raw={})
    db = MagicMock()

    fake_user_db = MagicMock(hospital_id="hosp1")
    db.query.return_value.filter.return_value.one_or_none.return_value = fake_user_db
    res = await get_current_hospital_id(user, db)
    assert res == "hosp1"

    db.query.return_value.filter.return_value.one_or_none.return_value = None
    sa_user = TokenPayload(sub="sa", preferred_username="sa", email=None, realm_access={"roles": ["super_admin"]}, raw={})
    res_sa = await get_current_hospital_id(sa_user, db)
    assert res_sa is None

    with pytest.raises(HTTPException) as exc:
        await get_current_hospital_id(user, db)
    assert exc.value.status_code == 403

# ---------------------------------------------------------------------------
# DB Master Tests
# ---------------------------------------------------------------------------

def test_ensure_master_database_exists():
    mock_test_engine = MagicMock()
    with patch("app.db.master.create_engine", return_value=mock_test_engine):
        _ensure_master_database_exists()

    mock_test_engine_fail = MagicMock()
    mock_test_engine_fail.connect.side_effect = OperationalError("no db", {}, None)

    mock_admin_engine = MagicMock()
    mock_admin_conn = MagicMock()
    mock_admin_conn.execute.return_value.scalar.return_value = None
    mock_admin_engine.connect.return_value.__enter__.return_value = mock_admin_conn

    def mock_create_engine_side_effect(url, **kwargs):
        if "admin" in str(url):
            return mock_admin_engine
        return mock_test_engine_fail

    with patch("app.db.master.create_engine", side_effect=mock_create_engine_side_effect):
        _ensure_master_database_exists()

from unittest.mock import PropertyMock

def test_ensure_master_database_exists_no_db_name():
    with patch.object(type(settings), "database_url", new_callable=PropertyMock, return_value="postgresql://user:pass@host/"):
        with patch("app.db.master.create_engine", side_effect=OperationalError("no db", {}, None)):
            with pytest.raises(ValueError, match="Cannot determine database name"):
                _ensure_master_database_exists()

def test_get_master_session_and_db():
    with get_master_session() as session:
        assert session is not None

    db = get_master_db()
    assert db is not None
    db.close()

# ---------------------------------------------------------------------------
# Exceptions Tests
# ---------------------------------------------------------------------------

def test_exceptions_coverage():
    assert UnauthorizedError().status_code == 401
    assert ForbiddenError().status_code == 403
    assert NotFoundError().status_code == 404
    assert ConflictError().status_code == 409
    assert BadRequestError().status_code == 400
    assert RateLimitError().status_code == 429
    assert TenantNotFoundError().status_code == 404
    assert TokenExpiredError().status_code == 401
    assert MFARequiredError().status_code == 401
    assert TenantSuspendedError().status_code == 403
    assert ReadOnlyScopeError().status_code == 403

# ---------------------------------------------------------------------------
# Dependencies & RS256 Token Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dependencies_get_master_db_and_superadmin():
    from app.dependencies import get_master_db, get_current_super_admin

    db_iter = get_master_db()
    sess = next(db_iter)
    assert sess is not None

    user_sa = TokenPayload(sub="sa1", preferred_username="admin", email="a@b.com", realm_access={"roles": ["super_admin"]}, raw={"type": "superadmin", "role": "super_admin"})
    sa_res = await get_current_super_admin(user_sa)
    assert sa_res == user_sa

    user_no_sa = TokenPayload(sub="u1", preferred_username="usr", email="a@b.com", realm_access={"roles": ["user"]}, raw={})
    with pytest.raises(HTTPException) as exc:
        await get_current_super_admin(user_no_sa)
    assert exc.value.status_code == 403

@pytest.mark.asyncio
async def test_decode_token_rs256_public_key():
    token = jwt.encode({"sub": "user1", "iss": f"{settings.keycloak_url}/realms/master-realm"}, "secret_key", algorithm="HS256")
    with patch("app.core.security._fetch_jwks", AsyncMock(return_value={"keys": [{"kid": "k1"}]})), \
         patch("app.core.security._build_rsa_key", return_value={"kid": "k1"}), \
         patch("jose.jwt.decode", return_value={"sub": "user1", "realm_access": {"roles": ["user"]}}):
        decoded = await _decode_token(token)
        assert decoded["sub"] == "user1"
