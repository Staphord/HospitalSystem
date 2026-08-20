"""Contract tests for Keycloak administration and realm orchestration clients."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.user import User
from app.services import keycloak_admin as ka
from app.services import keycloak_realm as kr


class Response:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = str(self._body)
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.is_success:
            raise RuntimeError(self.status_code)


class Client:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def _response(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "GET" and "/users/id-1" in url:
            return Response(200, body={"id": "id-1", "username": "u", "email": "e@example.com", "firstName": "J", "lastName": "U"})
        if method == "GET" and url.endswith("/users/profile"):
            return Response(200, body={"attributes": []})
        if method == "GET" and "/protocol-mappers/models" in url:
            return Response(200, body=[])
        if method == "GET" and "/admin/realms/" in url and "/users" not in url and "/roles" not in url and "/clients" not in url:
            return Response(200, body={"accessTokenLifespan": 120})
        for key, value in self.responses.items():
            if isinstance(key, tuple):
                continue
            if key in url:
                return value
        return self.responses.get((method, "*"), Response())

    async def get(self, url, **kwargs):
        return self._response("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return self._response("POST", url, **kwargs)

    async def put(self, url, **kwargs):
        return self._response("PUT", url, **kwargs)

    async def delete(self, url, **kwargs):
        return self._response("DELETE", url, **kwargs)

    async def request(self, method, url, **kwargs):
        return self._response(method, url, **kwargs)


@pytest.mark.asyncio
async def test_keycloak_admin_http_helpers(monkeypatch, db_session):
    monkeypatch.setattr(ka, "_headers", AsyncMock(return_value={"Authorization": "Bearer t"}))
    monkeypatch.setattr(ka, "set_user_password", AsyncMock())
    monkeypatch.setattr(ka, "assign_user_roles", AsyncMock())
    client = Client({
        ("POST", "*"): Response(201, headers={"location": "/users/id-1"}),
        ("GET", "*"): Response(200, body=[{"id": "id-1", "username": "u"}]),
        ("PUT", "*"): Response(204),
        ("DELETE", "*"): Response(204),
    })
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **kwargs: client)
    assert await ka.create_keycloak_user("u", "Password1!", "u@example.com", ["doctor"], "Jane Doe") == "id-1"
    await ka.set_user_password("id-1", "Password1!")
    await ka.assign_user_roles("id-1", ["doctor"])
    await ka.update_keycloak_user("id-1", username="new", full_name="Jane Q Doe", enabled=False)
    assert await ka.delete_keycloak_user("u") == "id-1"
    await ka.ensure_roles(["doctor"])
    await ka.remove_user_role("id-1", "doctor")
    await ka.set_user_attribute("id-1", "tenant_id", "t1")

    user = ka.create_local_user(db_session, "sub", "u@example.com", "t1", "u", "User", "doctor")
    assert ka.get_local_users_by_hospital(db_session, "t1") == [user]
    assert ka.update_local_user(db_session, "sub", role="nurse").role == "nurse"
    assert ka.delete_local_user(db_session, "sub") is True
    assert ka.delete_local_user(db_session, "missing") is False


@pytest.mark.asyncio
async def test_keycloak_admin_conflict_and_role_branches(monkeypatch):
    monkeypatch.setattr(ka, "_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(ka, "set_user_password", AsyncMock())
    monkeypatch.setattr(ka, "assign_user_roles", AsyncMock())
    client = Client({
        ("POST", "*"): Response(409),
        ("GET", "*"): Response(200, body=[{"id": "existing"}]),
        ("PUT", "*"): Response(204),
        ("DELETE", "*"): Response(204),
    })
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **kwargs: client)
    assert await ka.create_keycloak_user("u", "p", "e", []) == "existing"

    client.responses[("GET", "*")] = Response(404)
    await ka.ensure_roles(["new-role"])
    assert any(call[0] == "POST" for call in client.calls)


@pytest.mark.asyncio
async def test_keycloak_admin_token_realm_search_and_superadmin_client(monkeypatch):
    class Resp:
        status_code = 200
        is_success = True
        headers = {}
        text = ""
        def json(self): return {"access_token": "admin-token"}
        def raise_for_status(self): pass
    class SearchClient(Client):
        async def post(self, url, **kwargs): return Resp()
        async def get(self, url, **kwargs):
            if url.endswith("/realms"):
                return type("R", (), {"is_success": True, "json": lambda self: [{"realm": "hosp-one"}]})()
            return type("R", (), {"is_success": True, "json": lambda self: [{"id": "u1"}]})()
    client = SearchClient()
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **kwargs: client)
    assert await ka._get_admin_token() == "admin-token"
    assert (await ka._headers())["Authorization"] == "Bearer admin-token"
    assert await ka._list_all_realms() == ["hosp-one"]
    monkeypatch.setattr(ka, "_headers", AsyncMock(return_value={}))
    assert await ka.find_user_realm_by_username("u") in {"hospital-realm", "master", "hosp-one", None}
    await ka.ensure_superadmin_client()


@pytest.mark.asyncio
async def test_keycloak_admin_edge_responses(monkeypatch, db_session):
    monkeypatch.setattr(ka, "_headers", AsyncMock(return_value={}))
    class Resp:
        def __init__(self, status=200, body=None, headers=None):
            self.status_code, self._body, self.headers = status, body or [], headers or {}
            self.is_success = 200 <= status < 300
            self.text = "response"
        def json(self): return self._body
        def raise_for_status(self):
            if not self.is_success: raise RuntimeError("http")
    class EdgeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            if "/clients" in url: return Resp(200, [{"clientId": "superadmin-login"}])
            if "/users?" in url: return Resp(200, [])
            if "/roles/missing" in url: return Resp(404, {})
            if "/roles/doctor" in url: return Resp(200, {"name": "doctor"})
            return Resp(200, {"username": "u", "email": "e", "firstName": "", "lastName": ""})
        async def post(self, *args, **kwargs): return Resp(201)
        async def put(self, *args, **kwargs): return Resp(204)
        async def delete(self, *args, **kwargs): return Resp(204)
        async def request(self, *args, **kwargs): return Resp(200)
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **kwargs: EdgeClient())
    assert await ka.create_keycloak_user("u", "p", "e", [], "") is None
    await ka.assign_user_roles("u", ["missing", "doctor"])
    await ka.update_keycloak_user("u", full_name="   ")
    await ka.ensure_roles(["missing"])
    assert await ka.remove_user_role("u", "missing") is False
    await ka.set_user_attribute("u", "tenant_id", "t")
    await ka.ensure_superadmin_client()


@pytest.mark.asyncio
async def test_realm_client_and_setup_flows(monkeypatch):
    monkeypatch.setattr(kr, "_admin_headers", AsyncMock(return_value={"Authorization": "Bearer t"}))
    client = Client({
        ("POST", "*"): Response(201, body={}, headers={"location": "/clients/cid"}),
        ("GET", "*"): Response(200, body=[]),
        ("PUT", "*"): Response(200),
        ("DELETE", "*"): Response(204),
    })
    monkeypatch.setattr(kr.httpx, "AsyncClient", lambda **kwargs: client)
    await kr.create_tenant_realm("hosp-one")
    await kr.create_realm_client("hosp-one")
    await kr.ensure_realm_roles("hosp-one", ["doctor"])
    await kr.add_tenant_id_to_user_profile("hosp-one")
    await kr.set_realm_token_lifespan("hosp-one", 300)
    await kr.delete_tenant_realm("hosp-one")
    assert await kr.list_all_realms() == []
    assert await kr.verify_tenant_realm_exists("hosp-one") is True
    assert await kr.get_realm_roles("hosp-one") == []
    await kr.create_realm_role("hosp-one", "doctor", "Doctor")
    await kr.update_realm_role("hosp-one", "doctor", description="Updated")
    await kr.delete_realm_role("hosp-one", "doctor")
    assert await kr.get_all_realm_users("hosp-one") == []


@pytest.mark.asyncio
async def test_realm_existing_and_error_status_paths(monkeypatch):
    monkeypatch.setattr(kr, "_admin_headers", AsyncMock(return_value={}))
    client = Client({
        ("POST", "*"): Response(409),
        ("GET", "*"): Response(200, body=[{"id": "cid", "name": "tenant_id"}]),
        ("PUT", "*"): Response(200),
        ("DELETE", "*"): Response(404),
    })
    monkeypatch.setattr(kr.httpx, "AsyncClient", lambda **kwargs: client)
    await kr.create_tenant_realm("hosp-one")
    await kr.create_realm_client("hosp-one")
    await kr.ensure_realm_roles("hosp-one", ["doctor"])
    await kr.add_tenant_id_to_user_profile("hosp-one")
    await kr.set_realm_token_lifespan("hosp-one", 300)
    await kr.delete_tenant_realm("hosp-one")
    await kr.create_realm_role("hosp-one", "doctor")
    await kr.delete_realm_role("hosp-one", "doctor")


@pytest.mark.asyncio
async def test_keycloak_admin_remaining_http_branches(monkeypatch, db_session):
    monkeypatch.setattr(ka, "_headers", AsyncMock(return_value={"Authorization": "Bearer t"}))

    class EmptyResponse(Response):
        def __init__(self, status_code=200, body=None, headers=None):
            super().__init__(status_code, [] if body is None else body, headers)

    class BranchClient:
        def __init__(self): self.calls = []
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            self.calls.append(("GET", url))
            if "/users?" in url: return EmptyResponse(200, [])
            if "/roles/missing" in url: return EmptyResponse(500, {})
            return EmptyResponse(200, {"username": "u", "email": "e", "enabled": True})
        async def post(self, url, **kwargs):
            self.calls.append(("POST", url)); return EmptyResponse(201, {}, {})
        async def put(self, url, **kwargs):
            self.calls.append(("PUT", url)); return EmptyResponse(204)
        async def delete(self, url, **kwargs): return EmptyResponse(204)
        async def request(self, method, url, **kwargs): return EmptyResponse(204)
    client = BranchClient()
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **kwargs: client)
    assert await ka.create_keycloak_user("u", "p", "e", [], None) is None
    await ka.update_keycloak_user("u", full_name="", email="new@example.com", enabled=True)
    await ka.set_user_attribute("u", "tenant", "t")
    assert await ka.remove_user_role("u", "missing") is False
    assert await ka.delete_keycloak_user("u") is None

    class ClientList(BranchClient):
        async def get(self, url, **kwargs):
            if url.endswith("/clients"):
                return EmptyResponse(200, [{"clientId": ka.SUPERADMIN_CLIENT_ID}])
            return await super().get(url, **kwargs)
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **kwargs: ClientList())
    await ka.ensure_superadmin_client()


@pytest.mark.asyncio
async def test_keycloak_admin_search_and_local_update_edges(monkeypatch, db_session):
    monkeypatch.setattr(ka, "_headers", AsyncMock(return_value={}))
    existing = ka.create_local_user(db_session, "local", "old@example.com", "t1", "old", "Old", "nurse")
    assert ka.create_local_user(db_session, "local", "new@example.com", "t2", None, None, None) is existing
    assert existing.email == "new@example.com"
    assert ka.update_local_user(db_session, "missing") is None
    ka.update_local_user(db_session, "local", username="new", full_name="New Name", email="n@e", role="doctor", hospital_id="t3")

    class Empty:
        status_code = 201; headers = {}; is_success = True
        def json(self): return []
        def raise_for_status(self): pass
    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **k): return Empty()
        async def get(self, *a, **k): return Empty()
        async def put(self, *a, **k): return Empty()
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **k: C())
    assert await ka.create_keycloak_user("u", "p", "e", []) is None

    class RealmClient(C):
        async def get(self, url, **kwargs):
            if url.endswith("/realms"): return type("R", (), {"is_success": True, "json": lambda s: [{"realm": "hosp-inactive"}, {"realm": "other"}]})()
            if "/master/" in url: return type("R", (), {"is_success": True, "json": lambda s: []})()
            if "/hosp-inactive/" in url: return type("R", (), {"is_success": True, "json": lambda s: [{"id": "u"}]})()
            return type("R", (), {"is_success": True, "json": lambda s: [{"id": "u"}]})()
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **k: RealmClient())
    monkeypatch.setattr(ka, "_list_all_realms", AsyncMock(return_value=["hosp-inactive", "other"]))
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr("app.core.database.get_session_local", lambda: lambda: fake_db)
    monkeypatch.setattr(ka.settings, "keycloak_realm", "hosp-inactive")
    assert await ka.find_user_realm_by_username("u") == "other"


@pytest.mark.asyncio
async def test_keycloak_realm_status_and_optional_payload_edges(monkeypatch):
    monkeypatch.setattr(kr, "_admin_headers", AsyncMock(return_value={}))
    class R:
        def __init__(self, status=500, body=None, headers=None): self.status_code=status; self._body=body or {}; self.headers=headers or {}; self.is_success=200 <= status < 300
        def json(self): return self._body
        def raise_for_status(self):
            if not self.is_success: raise RuntimeError("http")
    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kwargs): return R(500)
        async def get(self, url, **kwargs): return R(500)
        async def put(self, url, **kwargs): return R(500)
        async def delete(self, url, **kwargs): return R(404)
    monkeypatch.setattr(kr.httpx, "AsyncClient", lambda **k: C())
    with pytest.raises(RuntimeError): await kr.create_tenant_realm("x")
    with pytest.raises(RuntimeError): await kr.create_realm_client("x")
    await kr.delete_tenant_realm("x")
    await kr.set_realm_token_lifespan("x")
    await kr.delete_realm_role("x", "missing")
    await kr.update_realm_role("x", "r")
    with pytest.raises(RuntimeError): await kr.create_realm_role("x", "r")

    class Profile(C):
        async def get(self, url, **kwargs): return R(404)
    monkeypatch.setattr(kr.httpx, "AsyncClient", lambda **k: Profile())
    await kr.add_tenant_id_to_user_profile("x")

    class TokenResponse:
        def json(self): return {"access_token": "admin"}
        def raise_for_status(self): pass
    class TokenClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs): return TokenResponse()
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **kwargs: TokenClient())
    assert await ka._get_admin_token() == "admin"


@pytest.mark.asyncio
async def test_keycloak_realm_helpers_and_creation_branches(monkeypatch):
    assert kr.get_realm_url("r").endswith("/realms/r")
    assert kr.get_realm_admin_url("r").endswith("/admin/realms/r")
    assert kr.get_realm_token_url("r").endswith("/protocol/openid-connect/token")
    class R:
        def __init__(self, status=200, body=None, headers=None): self.status_code=status; self.body=body or {}; self.headers=headers or {}; self.is_success=200 <= status < 300
        def json(self): return self.body
        def raise_for_status(self):
            if not self.is_success: raise RuntimeError("http")
    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kwargs):
            if "protocol/openid" in url: return R(200, {"access_token": "t"})
            if url.endswith("/roles"): return R(201)
            return R(201, {}, {})
        async def get(self, url, **kwargs):
            if "clientId=hospital-api" in url: return R(200, [])
            if "protocol-mappers" in url: return R(200, [])
            if "/roles/" in url: return R(404)
            if url.endswith("/users/profile"): return R(200, {"attributes": [{"name": "tenant_id"}]})
            return R(200, {"accessTokenLifespan": 60})
        async def put(self, url, **kwargs): return R(500)
        async def delete(self, url, **kwargs): return R(500)
    monkeypatch.setattr(kr.httpx, "AsyncClient", lambda **k: C())
    assert await kr._get_master_admin_token() == "t"
    await kr.create_realm_client("r")
    await kr.ensure_realm_roles("r")
    await kr.add_tenant_id_to_user_profile("r")
    with pytest.raises(RuntimeError): await kr.delete_tenant_realm("r")
    await kr.set_realm_token_lifespan("r", 300)
    with pytest.raises(RuntimeError): await kr.update_realm_role("r", "role", new_name="new", description="desc")


@pytest.mark.asyncio
async def test_keycloak_admin_realm_search_failures_and_client_statuses(monkeypatch):
    monkeypatch.setattr(ka, "_headers", AsyncMock(return_value={}))
    class R:
        def __init__(self, status=500, body=None): self.status_code=status; self._body=body or []; self.is_success=200 <= status < 300; self.headers={}
        def json(self): return self._body
        def raise_for_status(self):
            if not self.is_success: raise RuntimeError("http")
    class C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, **kwargs): return R(500)
        async def post(self, url, **kwargs): return R(500)
        async def put(self, url, **kwargs): return R(500)
        async def delete(self, url, **kwargs): return R(500)
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **k: C())
    assert await ka._list_all_realms() == []
    assert await ka.find_user_realm_by_username("nobody") is None
    with pytest.raises(RuntimeError): await ka.ensure_superadmin_client()

    class ExistingClient(C):
        async def get(self, url, **kwargs): return R(200, [{"clientId": "other"}])
        async def post(self, url, **kwargs): return R(409)
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **k: ExistingClient())
    await ka.ensure_superadmin_client()


@pytest.mark.asyncio
async def test_realm_client_profile_pagination_and_status_branches(monkeypatch):
    monkeypatch.setattr(kr, "_admin_headers", AsyncMock(return_value={"Authorization": "Bearer t"}))

    class R:
        def __init__(self, status=200, body=None, headers=None):
            self.status_code = status; self.body = body if body is not None else {}; self.headers = headers or {}; self.is_success = 200 <= status < 300
        def json(self): return self.body
        def raise_for_status(self):
            if not self.is_success: raise RuntimeError("http")

    class C:
        def __init__(self): self.page = 0
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            if url.endswith("/roles"): return R(409)
            if "protocol-mappers" in url: return R(500)
            return R(201, {})
        async def get(self, url, **kwargs):
            if url.endswith("/clients") or "clientId=hospital-api" in url: return R(200, [{"id": "client-id"}])
            if "protocol-mappers" in url: return R(200, [{"name": "tenant_id"}])
            if url.endswith("/users/profile"): return R(500)
            if url.endswith("/realms"): return R(200, [{"realm": "one"}])
            if url.endswith("/users"):
                self.page += 1
                return R(200, [{"id": "u"}] if self.page == 1 else [])
            if url.endswith("/roles/doctor"): return R(404)
            if url.endswith("/roles/nurse"): return R(200, {})
            return R(200, {"accessTokenLifespan": 300})
        async def put(self, url, **kwargs): return R(500)
        async def delete(self, url, **kwargs): return R(404)
    monkeypatch.setattr(kr.httpx, "AsyncClient", lambda **kwargs: C())
    await kr.create_realm_client("hosp")
    await kr.ensure_realm_roles("hosp", ["doctor", "nurse"])
    await kr.add_tenant_id_to_user_profile("hosp")
    await kr.set_realm_token_lifespan("hosp", 300)
    assert await kr.list_all_realms() == ["one"]
    assert await kr.get_all_realm_users("hosp") == [{"id": "u"}]
    assert await kr.verify_tenant_realm_exists("hosp") is True


@pytest.mark.asyncio
async def test_keycloak_realm_search_and_creation_fallbacks(monkeypatch, db_session):
    from app.models.master import Tenant
    db_session.add(Tenant(tenant_id="hosp-active", hospital_name="Active", db_connection_string="dsn", is_active=True))
    db_session.commit()
    monkeypatch.setattr(ka, "_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(ka, "_list_all_realms", AsyncMock(return_value=["hosp-active", "hosp-inactive", "other"]))
    monkeypatch.setattr("app.core.database.get_session_local", lambda: lambda: db_session)

    class C:
        def __init__(self): self.posted = False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            if "hosp-active" in url: return Response(200, [{"id": "active"}])
            if "other" in url: return Response(200, [{"id": "other"}])
            return Response(200, [])
        async def post(self, url, **kwargs): return Response(201, {}, {})
        async def put(self, url, **kwargs): return Response(204)
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **kwargs: C())
    assert await ka.find_user_realm_by_username("u") == "hosp-active"
    monkeypatch.setattr(ka, "_list_all_realms", AsyncMock(return_value=["other"]))
    assert await ka.find_user_realm_by_username("u") == "other"
    assert await ka.create_keycloak_user("u", "p", "e", [], " ") is None

    class ConflictEmpty(C):
        async def post(self, url, **kwargs): return Response(409)
        async def get(self, url, **kwargs): return Response(200, [])
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **kwargs: ConflictEmpty())
    with pytest.raises(Exception):
        await ka.create_keycloak_user("u", "p", "e", [])


@pytest.mark.asyncio
async def test_keycloak_delete_role_and_realm_error_branches(monkeypatch):
    monkeypatch.setattr(ka, "_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(kr, "_admin_headers", AsyncMock(return_value={}))
    class ErrC:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def delete(self, url, **kwargs): return Response(404)
        async def get(self, url, **kwargs): return Response(200, [{"id": "u1", "realm": "master"}])
    monkeypatch.setattr(ka.httpx, "AsyncClient", lambda **kwargs: ErrC())
    monkeypatch.setattr(kr.httpx, "AsyncClient", lambda **kwargs: ErrC())
    await ka.delete_keycloak_user("missing")
    assert await kr.list_all_realms() == ["master"]
