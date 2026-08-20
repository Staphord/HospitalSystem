from unittest.mock import AsyncMock

import pytest
import httpx
from starlette.requests import Request

from app.api.v1.auth import router
from app.api.v1.auth.schemas import LoginRequest, SignupRequest
from app.core.config import settings
from jose import jwt
from app.exceptions import UnauthorizedError
from app.models.admin import SuperAdmin
from app.models.master import Tenant


def request(method="POST", ip="127.0.0.1"):
    return Request({"type": "http", "method": method, "path": "/", "headers": [], "client": (ip, 1)})


def signup_body():
    return SignupRequest(hospital_name="City Hospital", admin_username="city_admin", admin_password="N3w!CedarRiver", admin_email="admin@city.example")


@pytest.mark.asyncio
async def test_signup_orchestration(monkeypatch, db_session):
    monkeypatch.setattr(router, "setup_tenant_realm", AsyncMock())
    monkeypatch.setattr(router, "verify_tenant_realm_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(router, "ensure_roles", AsyncMock())
    monkeypatch.setattr(router, "create_keycloak_user", AsyncMock(return_value="kc-sub"))
    monkeypatch.setattr(router, "set_user_attribute", AsyncMock())
    monkeypatch.setattr(router, "create_local_user", lambda **kwargs: None)
    monkeypatch.setattr(router, "auth_service", router.auth_service)
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(return_value={"access_token": "a", "refresh_token": "r", "expires_in": 1, "refresh_expires_in": 2}))
    monkeypatch.setattr("app.services.provision.provision_tenant_database_sync", lambda *args: "dsn")
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    monkeypatch.setattr("app.events.publisher.publish_tenant_created", AsyncMock())
    result = await router.signup(request(), signup_body(), db_session)
    assert result["hospital_name"] == "City Hospital"
    assert result["access_token"] == "a"


@pytest.mark.asyncio
async def test_signup_keycloak_failure_and_existing_tenant(monkeypatch, db_session):
    monkeypatch.setattr(router, "setup_tenant_realm", AsyncMock(side_effect=RuntimeError("kc down")))
    monkeypatch.setattr(router, "verify_tenant_realm_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(router, "ensure_roles", AsyncMock())
    monkeypatch.setattr(router, "create_keycloak_user", AsyncMock(side_effect=RuntimeError("create failed")))
    monkeypatch.setattr(router.uuid, "uuid4", lambda: type("U", (), {"hex": "abcdef1234567890"})())
    with pytest.raises(Exception):
        await router.signup(request(), signup_body(), db_session)


@pytest.mark.asyncio
async def test_signup_existing_realm_fallback_and_event_failure(monkeypatch, db_session):
    monkeypatch.setattr(router.uuid, "uuid4", lambda: type("U", (), {"hex": "existing123456"})())
    db_session.add(Tenant(tenant_id="hosp-existing", hospital_name="Existing", db_connection_string="dsn")); db_session.commit()
    with pytest.raises(Exception) as exc:
        await router.signup(request(), signup_body(), db_session)
    assert getattr(exc.value, "status_code", None) == 409

    monkeypatch.setattr(router.uuid, "uuid4", lambda: type("U", (), {"hex": "fallback123456"})())
    monkeypatch.setattr(router, "setup_tenant_realm", AsyncMock())
    monkeypatch.setattr(router, "verify_tenant_realm_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(router, "ensure_roles", AsyncMock())
    monkeypatch.setattr(router, "create_keycloak_user", AsyncMock(return_value="kc-fallback"))
    monkeypatch.setattr(router, "set_user_attribute", AsyncMock())
    monkeypatch.setattr(router, "create_local_user", lambda **kwargs: None)
    monkeypatch.setattr("app.services.provision.provision_tenant_database_sync", lambda *a: "dsn")
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    monkeypatch.setattr("app.events.publisher.publish_tenant_created", AsyncMock(side_effect=RuntimeError("broker")))
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(return_value={"access_token": "a", "refresh_token": "r", "expires_in": 1, "refresh_expires_in": 2}))
    result = await router.signup(request(), signup_body(), db_session)
    assert result["tenant_id"] == "hosp-fallback"


@pytest.mark.asyncio
async def test_login_realm_resolution_and_error_paths(monkeypatch, db_session):
    from fastapi import HTTPException
    monkeypatch.setattr(router, "is_blocked", lambda *a: False)
    monkeypatch.setattr(router, "record_successful_login", lambda *a: None)
    monkeypatch.setattr(router, "get_failed_attempts", lambda *a: 1)
    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(return_value="missing-realm"))
    monkeypatch.setattr("app.services.keycloak_realm.verify_tenant_realm_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=HTTPException(401, detail="bad")))
    with pytest.raises(HTTPException) as exc:
        await router.login(request(ip="10.0.0.88"), LoginRequest(username="u", password="p"), db_session)
    assert exc.value.status_code == 401

    token = jwt.encode({"sub": "sa", "realm_access": {"roles": ["super_admin"]}}, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(return_value="master"))
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(return_value={"access_token": token}))
    with pytest.raises(HTTPException) as exc:
        await router.login(request(ip="10.0.0.89"), LoginRequest(username="sa", password="p"), db_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_login_brute_force_rejection(monkeypatch, db_session):
    monkeypatch.setattr(router, "is_blocked", lambda *args: True)
    monkeypatch.setattr(router, "get_remaining_seconds", lambda *args: 30)
    with pytest.raises(Exception) as exc:
        await router.login(request(ip="127.0.0.11"), LoginRequest(username="u", password="p"), db_session)
    assert getattr(exc.value, "status_code", None) == 429


@pytest.mark.asyncio
async def test_login_success_and_invalid_keycloak_response(monkeypatch, db_session):
    token = jwt.encode({"sub": "kc", "tenant_id": "t1", "realm_access": {"roles": ["doctor"]}}, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr(router, "record_successful_login", lambda *args: None)
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(return_value={"access_token": token, "refresh_token": "r", "expires_in": 1, "refresh_expires_in": 2, "user_sub": "kc"}))
    monkeypatch.setattr(router, "is_tenant_suspended", AsyncMock(return_value=False))
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    result = await router.login(request(), LoginRequest(username="u", password="p"), db_session)
    assert result["tenant_id"] == "t1"


@pytest.mark.asyncio
async def test_superadmin_login_success_and_sync(monkeypatch, db_session):
    token = jwt.encode({"sub": "sa-sub", "email": "sa@example.com", "name": "System Admin", "realm_access": {"roles": ["super_admin"]}}, settings.secret_key, algorithm="HS256")
    result = {"access_token": token, "refresh_token": "r", "expires_in": 60, "refresh_expires_in": 120, "session_id": "sid"}
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr(router, "record_successful_login", lambda *args: None)
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(return_value=result))
    response = await router.superadmin_login(request(), type("Body", (), {"username": "sa", "password": "p"})(), db_session)
    assert response["scope"] == "full"
    assert db_session.query(SuperAdmin).filter(SuperAdmin.username == "sa").first() is not None


@pytest.mark.asyncio
async def test_superadmin_login_invalid_credentials(monkeypatch, db_session):
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr(router, "record_failed_attempt", lambda *args: None)
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=UnauthorizedError("invalid")))
    with pytest.raises(Exception) as exc:
        await router.superadmin_login(request("POST"), type("Body", (), {"username": "missing", "password": "p"})(), db_session)
    assert getattr(exc.value, "status_code", None) in (401, 500)


@pytest.mark.asyncio
async def test_superadmin_login_mfa_challenge(monkeypatch, db_session):
    import pyotp
    admin = SuperAdmin(username="mfa-sa", email="mfa-sa@example.com", password_hash="x", full_name="MFA SA", mfa_secret=pyotp.random_base32(), mfa_enabled=True, backup_codes="[\"hash\"]")
    db_session.add(admin); db_session.commit()
    token = jwt.encode({"sub": "sa", "email": "mfa-sa@example.com", "realm_access": {"roles": ["super_admin"]}}, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr(router, "record_successful_login", lambda *args: None)
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(return_value={"access_token": token, "refresh_token": "r", "expires_in": 60, "refresh_expires_in": 120, "session_id": "sid"}))
    monkeypatch.setattr(router.auth_service, "is_valid_totp_secret", lambda _: True)
    response = await router.superadmin_login(request(), type("Body", (), {"username": "mfa-sa", "password": "p"})(), db_session)
    assert response.status_code == 202
    assert response.body


@pytest.mark.asyncio
async def test_first_login_password_change_success(monkeypatch, db_session):
    class Response:
        status_code = 200
        def __init__(self, body): self.body = body; self.headers = {}
        def json(self): return self.body
        def raise_for_status(self): pass
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            if "/users?" in url: return Response([{"id": "kc-id"}])
            return Response({"requiredActions": ["UPDATE_PASSWORD"], "username": "u"})
        async def put(self, *args, **kwargs): return Response({})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr("app.services.keycloak_admin._headers", AsyncMock(return_value={"Authorization": "Bearer token"}))
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=[{}, {"access_token": jwt.encode({"sub": "u", "tenant_id": "t"}, settings.secret_key, algorithm="HS256"), "refresh_token": "r", "expires_in": 1, "refresh_expires_in": 2, "session_id": "s"}]))
    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(return_value="hosp-test"))
    monkeypatch.setattr("app.services.keycloak_admin.set_user_password", AsyncMock())
    body = type("Body", (), {"username": "u", "temp_password": "temp", "new_password": "N3w!CedarRiver"})()
    result = await router.first_login_change_password(request(), body, db_session)
    assert result["scope"] == "full"


@pytest.mark.asyncio
async def test_superadmin_login_sync_role_cleanup_and_existing_record(monkeypatch, db_session):
    import pyotp
    from app.services import auth as auth_service

    token = jwt.encode({"sub": "synced", "email": "sync@example.com", "name": "Sync Admin", "realm_access": {"roles": ["super_admin", "hospital_admin"]}}, settings.secret_key, algorithm="HS256")
    local = SuperAdmin(username="sync-admin", email="sync@example.com", password_hash="x", full_name="Sync Admin", mfa_secret=pyotp.random_base32(), mfa_enabled=False)
    db_session.add(local); db_session.commit()
    calls = {"n": 0}
    async def login_side_effect(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise UnauthorizedError("not in realm")
        return {"access_token": token, "refresh_token": "r", "expires_in": 60, "refresh_expires_in": 120, "session_id": "sid"}
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr(router, "record_successful_login", lambda *args: None)
    monkeypatch.setattr(router.auth_service, "login", login_side_effect)
    monkeypatch.setattr(router, "ensure_roles", AsyncMock())
    monkeypatch.setattr(router, "create_keycloak_user", AsyncMock(return_value="synced"))
    monkeypatch.setattr(router, "remove_user_role", AsyncMock())
    class SearchResponse:
        is_success = True
        def json(self): return [{"id": "kc-id"}]
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return SearchResponse()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr("app.services.keycloak_admin._headers", AsyncMock(return_value={}))
    result = await router.superadmin_login(request(), type("Body", (), {"username": "sync-admin", "password": "Password1!"})(), db_session)
    assert result["scope"] == "full"
    router.remove_user_role.assert_awaited_once()
    assert local.keycloak_sub == "synced"


@pytest.mark.asyncio
async def test_superadmin_login_failure_and_non_superadmin_token(monkeypatch, db_session):
    from fastapi import HTTPException
    token = jwt.encode({"sub": "ordinary", "realm_access": {"roles": ["doctor"]}}, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr(router, "record_successful_login", lambda *args: None)
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(return_value={"access_token": token, "refresh_token": "r", "expires_in": 1, "refresh_expires_in": 2, "session_id": "s"}))
    with pytest.raises(HTTPException) as exc:
        await router.superadmin_login(request(), type("Body", (), {"username": "ordinary", "password": "p"})(), db_session)
    assert exc.value.status_code == 403

    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(router, "record_failed_attempt", lambda *args: None)
    with pytest.raises(HTTPException) as exc:
        await router.superadmin_login(request(), type("Body", (), {"username": "down", "password": "p"})(), db_session)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_login_realm_resolution_and_superadmin_guard(monkeypatch, db_session):
    token = jwt.encode({"sub": "sa", "realm_access": {"roles": ["super_admin"]}}, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(return_value="master"))
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(return_value={"access_token": token, "refresh_token": "r"}))
    with pytest.raises(Exception) as exc:
        await router.login(request(), LoginRequest(username="sa", password="p"), db_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_login_error_branches_and_suspension(monkeypatch, db_session):
    from fastapi import HTTPException
    base = {"access_token": jwt.encode({"sub": "u", "tenant_id": "t1", "realm_access": {"roles": ["doctor"]}}, settings.secret_key, algorithm="HS256"), "refresh_token": "r", "expires_in": 1, "refresh_expires_in": 2, "user_sub": "u"}
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(side_effect=RuntimeError("lookup")))
    monkeypatch.setattr("app.services.keycloak_realm.verify_tenant_realm_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(router, "record_failed_attempt", lambda *args: None)
    monkeypatch.setattr(router, "get_failed_attempts", lambda *args: 1)
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=HTTPException(status_code=401, detail="bad")))
    with pytest.raises(HTTPException) as exc:
        await router.login(request(), LoginRequest(username="u", password="p", realm="hosp-missing"), db_session)
    assert exc.value.status_code == 401

    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=RuntimeError("down")))
    with pytest.raises(HTTPException) as exc:
        await router.login(request(ip="127.0.0.12"), LoginRequest(username="u", password="p"), db_session)
    assert exc.value.status_code == 500

    monkeypatch.setattr(router.auth_service, "login", AsyncMock(return_value=base))
    monkeypatch.setattr(router, "record_successful_login", lambda *args: None)
    monkeypatch.setattr(router, "is_tenant_suspended", AsyncMock(return_value=True))
    with pytest.raises(HTTPException) as exc:
        await router.login(request(), LoginRequest(username="u", password="p"), db_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_login_tenant_mfa_challenge(monkeypatch, db_session):
    import pyotp
    from app.models.user import User
    token = jwt.encode({"sub": "mfa-user", "tenant_id": "t1", "realm_access": {"roles": ["doctor"]}}, settings.secret_key, algorithm="HS256")
    user = User(keycloak_sub="mfa-user", mfa_enabled=True, mfa_secret=pyotp.random_base32(), backup_codes='["hash"]')
    db_session.add(user); db_session.commit()
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr(router, "record_successful_login", lambda *args: None)
    monkeypatch.setattr(router, "is_tenant_suspended", AsyncMock(return_value=False))
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(return_value={"access_token": token, "refresh_token": "r", "expires_in": 1, "refresh_expires_in": 2, "user_sub": "mfa-user"}))
    monkeypatch.setattr(router.auth_service, "is_valid_totp_secret", lambda _: True)
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    response = await router.login(request(ip="127.0.0.13"), LoginRequest(username="mfa-user", password="p"), db_session)
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_first_login_change_password_error_paths(monkeypatch, db_session):
    from fastapi import HTTPException
    body = lambda username="u": type("Body", (), {"username": username, "temp_password": "temp", "new_password": "N3w!CedarRiver"})()
    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await router.first_login_change_password(request(ip="127.0.0.21"), body(), db_session)
    assert exc.value.status_code == 404

    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(return_value="hosp-test"))
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=HTTPException(status_code=401, detail="wrong")))
    with pytest.raises(HTTPException) as exc:
        await router.first_login_change_password(request(ip="127.0.0.22"), body(), db_session)
    assert exc.value.status_code == 401

    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=RuntimeError("down")))
    with pytest.raises(HTTPException) as exc:
        await router.first_login_change_password(request(ip="127.0.0.23"), body(), db_session)
    assert exc.value.status_code == 500

    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=[HTTPException(status_code=401, detail="not fully set up"), {"access_token": "a"}]))
    class EmptyResponse:
        def json(self): return []
        def raise_for_status(self): pass
    class EmptyClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return EmptyResponse()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: EmptyClient())
    monkeypatch.setattr("app.services.keycloak_admin._headers", AsyncMock(return_value={}))
    with pytest.raises(HTTPException) as exc:
        await router.first_login_change_password(request(ip="127.0.0.24"), body(), db_session)
    assert exc.value.status_code == 500

    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=[{}, RuntimeError("new login failed")]))
    class GoodResponse:
        def __init__(self, data): self.data = data
        def json(self): return self.data
        def raise_for_status(self): pass
    class GoodClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs): return GoodResponse([{"id": "id"}] if "users?" in url else {"requiredActions": []})
        async def put(self, *args, **kwargs): return GoodResponse({})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: GoodClient())
    monkeypatch.setattr("app.services.keycloak_admin.set_user_password", AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await router.first_login_change_password(request(ip="127.0.0.25"), body(), db_session)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_auth_public_routes_additional_branches(monkeypatch, db_session):
    # Test password_reset_request with missing user / service error
    monkeypatch.setattr(router.auth_service, "request_password_reset", AsyncMock(side_effect=router.HTTPException(status_code=400, detail="Error")))
    req = request(ip="127.0.0.90")
    reset_req = router.PasswordResetRequest(email="nonexistent@example.com")
    with pytest.raises(router.HTTPException):
        await router.password_reset_request(req, reset_req, db_session)

    # Test password_reset_confirm with invalid token
    monkeypatch.setattr(router.auth_service, "confirm_password_reset", AsyncMock(side_effect=router.HTTPException(status_code=400, detail="Invalid token")))
    confirm_req = router.PasswordResetConfirm(token="invalid_token", new_password="N3w!CedarRiver")
    with pytest.raises(router.HTTPException):
        await router.password_reset_confirm(req, confirm_req, db_session)

    # Test superadmin login with non-401 HTTPException
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr(router, "record_failed_attempt", lambda *args: None)
    monkeypatch.setattr(router.auth_service, "login", AsyncMock(side_effect=router.HTTPException(status_code=400, detail="bad request")))
    with pytest.raises(router.HTTPException) as exc_info:
        await router.superadmin_login(request("POST", ip="127.0.0.91"), type("Body", (), {"username": "non401user", "password": "p"})(), db_session)
    assert exc_info.value.status_code == 400

    # Test superadmin login sync when local.full_name is empty string
    local = SuperAdmin(username="nofullname-admin", email="nofullname@example.com", password_hash="x", full_name="", mfa_secret="sec")
    db_session.add(local); db_session.commit()
    calls = {"n": 0}
    async def login_side_effect(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise UnauthorizedError("not in realm")
        token = jwt.encode({"sub": "synced2", "email": "nofullname@example.com", "realm_access": {"roles": ["super_admin"]}}, settings.secret_key, algorithm="HS256")
        return {"access_token": token, "refresh_token": "r", "expires_in": 60, "refresh_expires_in": 120, "session_id": "sid"}
    monkeypatch.setattr(router.auth_service, "login", login_side_effect)
    monkeypatch.setattr(router, "ensure_roles", AsyncMock())
    monkeypatch.setattr(router, "create_keycloak_user", AsyncMock(return_value="synced2"))
    monkeypatch.setattr(router, "remove_user_role", AsyncMock())
    result = await router.superadmin_login(request("POST", ip="127.0.0.92"), type("Body", (), {"username": "nofullname-admin", "password": "Password1!"})(), db_session)
    assert result["scope"] == "full"

