from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import keycloak_admin as kc


class Response:
    def __init__(self, status=200, body=None, headers=None, text="ok"):
        self.status_code = status; self._body = body if body is not None else {}; self.headers = headers or {}; self.text = text; self.reason_phrase = "error"
    def json(self): return self._body
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(f"HTTP {self.status_code}")
    @property
    def is_success(self): return 200 <= self.status_code < 300


class Client:
    def __init__(self, post=None, get=None, put=None, delete=None, request=None):
        self.post_response = post or Response(); self.get_response = get or Response(body=[]); self.put_response = put or Response(); self.delete_response = delete or Response(); self.request_response = request or Response()
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def post(self, *args, **kwargs): return self.post_response
    async def get(self, *args, **kwargs): return self.get_response
    async def put(self, *args, **kwargs): return self.put_response
    async def delete(self, *args, **kwargs): return self.delete_response
    async def request(self, *args, **kwargs): return self.request_response


@pytest.mark.asyncio
async def test_keycloak_token_headers_and_role_http_operations():
    with patch("httpx.AsyncClient", return_value=Client(post=Response(body={"access_token": "token"}))):
        assert await kc._get_admin_token() == "token"
        assert (await kc._headers())["Authorization"] == "Bearer token"
    assert kc._admin_api_url("realm").endswith("/admin/realms/realm")
    assert kc._person_name("A/B_(C)", "fallback") == "A B C"
    assert kc._person_name("!!!", "fallback") == "fallback"
    bad_response = Response(status=500, body="not-json", text="plain")
    bad_response.json = lambda: (_ for _ in ()).throw(ValueError("bad json"))
    assert "plain" in kc._keycloak_error(bad_response)
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={"x": "y"}), patch("httpx.AsyncClient", return_value=Client(get=Response(body=[{"id": "u"}]))):
        assert await kc.get_user_realm_roles("u") == [{"id": "u"}]
        await kc.remove_user_roles("u", [])
        await kc.remove_user_roles("u", [{"name": "doctor"}])
        await kc.assign_user_roles("u", ["doctor", "missing"])
    with patch.object(kc, "get_user_realm_roles", new_callable=AsyncMock, return_value=[{"name": "doctor"}, {"name": "default-roles-r"}, {"name": "offline_access"}]), patch.object(kc, "remove_user_roles", new_callable=AsyncMock) as remove, patch.object(kc, "assign_user_roles", new_callable=AsyncMock):
        await kc.replace_user_roles("u", ["nurse"])
        remove.assert_awaited_once_with("u", [{"name": "doctor"}], realm=None)
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(post=Response(status=204))):
        await kc.logout_user_sessions("u")
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(post=Response(status=500))):
        with pytest.raises(RuntimeError):
            await kc.logout_user_sessions("u")


@pytest.mark.asyncio
async def test_keycloak_user_create_conflict_and_update_flows():
    headers = {"location": "https://host/users/u1"}
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(post=Response(status=201, headers=headers))), patch.object(kc, "set_user_password", new_callable=AsyncMock), patch.object(kc, "assign_user_roles", new_callable=AsyncMock):
        assert await kc.create_keycloak_user("doc", "Password1!", "d@example.com", ["doctor"], "Dr (One)") == "u1"
    conflict_client = Client(post=Response(status=409), get=Response(body=[{"id": "existing"}]), put=Response())
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=conflict_client), patch.object(kc, "set_user_password", new_callable=AsyncMock), patch.object(kc, "assign_user_roles", new_callable=AsyncMock):
        assert await kc.create_keycloak_user("doc", "Password1!", "d@example.com", []) == "existing"
    no_conflict = Client(post=Response(status=409), get=Response(body=[]))
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=no_conflict):
        with pytest.raises(Exception, match="Conflict creating user"):
            await kc.create_keycloak_user("doc", "Password1!", "d@example.com", [])
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(body={"id": "u", "createdTimestamp": 1, "username": "old"}))):
        await kc.update_keycloak_user("u", username="new", full_name="New User", enabled=False)
        await kc.set_user_password("u", "Password1!", temporary=True)
        await kc.set_user_attribute("u", "department", "ICU")
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(body={"id": "u", "email": "old"}))):
        await kc.update_keycloak_user("u", email="new@example.com")
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(body=[]))):
        assert await kc.delete_keycloak_user("missing") is None
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(body=[{"id": "u"}]), delete=Response(status=204))):
        assert await kc.delete_keycloak_user("user") == "u"
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(post=Response(status=400, body={"errorMessage": "bad"}))):
        with pytest.raises(Exception, match="bad"):
            await kc.create_keycloak_user("doc", "Password1!", "d@example.com", [])
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(post=Response(status=201), get=Response(body=[{"id": "found"}]))), patch.object(kc, "set_user_password", new_callable=AsyncMock), patch.object(kc, "assign_user_roles", new_callable=AsyncMock):
        assert await kc.create_keycloak_user("doc", "Password1!", "d@example.com", []) == "found"
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(post=Response(status=201), get=Response(body=[]))), patch.object(kc, "set_user_password", new_callable=AsyncMock), patch.object(kc, "assign_user_roles", new_callable=AsyncMock):
        assert await kc.create_keycloak_user("doc", "Password1!", "d@example.com", []) is None
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(body={"id": "u", "username": "old"}))):
        await kc.update_keycloak_user("u", full_name="   ")


@pytest.mark.asyncio
async def test_keycloak_roles_and_local_user_branches():
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(body=[{"name": "doctor"}]))):
        assert await kc.get_realm_roles("r") == [{"name": "doctor"}]
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(post=Response(status=201), get=Response(body={"name": "custom"}))):
        assert (await kc.create_realm_role("r", "custom"))["name"] == "custom"
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(post=Response(status=409))):
        with pytest.raises(Exception): await kc.create_realm_role("r", "custom")
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(body={"name": "old"}))):
        await kc.update_realm_role("r", "old", "new")
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(status=404))):
        with pytest.raises(Exception): await kc.update_realm_role("r", "missing", "new")
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(delete=Response(status=204))):
        await kc.delete_realm_role("r", "custom")
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(delete=Response(status=404))):
        with pytest.raises(Exception): await kc.delete_realm_role("r", "missing")
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(status=404), post=Response(status=201))):
        await kc.ensure_roles(["new-role"])
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(body={"name": "existing"}))):
        await kc.ensure_roles(["existing"])
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value={}), patch("httpx.AsyncClient", return_value=Client(get=Response(body=[]))):
        await kc.assign_user_roles("u", [])
    db = SimpleNamespace(query=lambda *a: SimpleNamespace(filter=lambda *a: SimpleNamespace(first=lambda: None)), add=lambda x: None, commit=lambda: None, refresh=lambda x: None)
    assert kc.delete_local_user(db, "missing") is False
    existing = SimpleNamespace(username="old", full_name="Old", email="old@x", role="doctor", hospital_id="old", is_active=True, force_password_change=False, department_id=None, phone=None, password_expires_at=None, mfa_enabled=False, deleted_at="old")
    d = SimpleNamespace(query=lambda *a: SimpleNamespace(filter=lambda *a: SimpleNamespace(first=lambda: existing)), commit=lambda: None, refresh=lambda x: None, add=lambda x: None, delete=lambda x: None)
    assert kc.create_local_user(d, "u", "new@x", "t", username="new", full_name="New", role="nurse", is_active=False, force_password_change=True, department_id="dep", phone="1", password_expires_at="later", mfa_enabled=True) is existing
    assert kc.update_local_user(d, "u", username="newer", full_name="Newer", email="n@x", role="doctor", hospital_id="t", is_active=False, force_password_change=True, department_id="dep", phone="2", password_expires_at="later", mfa_enabled=True, deleted_at="x", clear_deleted=True) is existing
    d.query = lambda *a: SimpleNamespace(filter=lambda *a: SimpleNamespace(first=lambda: None, all=lambda: []))
    assert kc.update_local_user(d, "missing") is None
    assert kc.get_local_users_by_hospital(d, "t", include_deleted=True) is not None
    d.query = lambda *a: SimpleNamespace(filter=lambda *a: SimpleNamespace(filter=lambda *a: SimpleNamespace(all=lambda: []), all=lambda: []))
    assert kc.get_local_users_by_hospital(d, "t", include_deleted=False) is not None
    d2 = SimpleNamespace(query=lambda *a: SimpleNamespace(filter=lambda *a: SimpleNamespace(first=lambda: existing)), commit=lambda: None, refresh=lambda x: None, add=lambda x: None, delete=lambda x: None)
    assert kc.create_local_user(d2, "u", "new@x", "t", is_active=None, force_password_change=None, department_id=None, phone=None, password_expires_at=None, mfa_enabled=None) is existing
    assert kc.update_local_user(d2, "u", username=None, full_name=None, email=None, role=None, hospital_id=None, is_active=None, force_password_change=None, department_id=None, phone=None, password_expires_at=None, mfa_enabled=None, deleted_at=None, clear_deleted=False) is existing
