from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services import keycloak_admin as admin
from app.services import keycloak_realm as realm


class Response:
    def __init__(self, status_code=200, data=None, headers=None, success=None):
        self.status_code = status_code; self._data = data if data is not None else {}; self.headers = headers or {}
        self.is_success = status_code < 400 if success is None else success
    def raise_for_status(self):
        if not self.is_success: raise RuntimeError(f"HTTP {self.status_code}")
    def json(self): return self._data


class Client:
    def __init__(self, responses=None): self.responses = list(responses or []); self.calls = []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs)); return self.responses.pop(0) if self.responses else Response()
    async def get(self, url, **kwargs): return self._next("get", url, **kwargs)
    async def post(self, url, **kwargs): return self._next("post", url, **kwargs)
    async def put(self, url, **kwargs): return self._next("put", url, **kwargs)
    async def delete(self, url, **kwargs): return self._next("delete", url, **kwargs)


@pytest.mark.asyncio
async def test_keycloak_user_lifecycle_and_role_management(monkeypatch):
    monkeypatch.setattr(admin, "_headers", AsyncMock(return_value={"Authorization": "Bearer token"}))
    client = Client([Response(201, headers={"location": "/users/u1"}), Response(), Response(), Response(200, {"id": "role"}), Response(200, {"id": "role2"}), Response(), Response(), Response(200, {"username": "old", "id": "u1", "createdTimestamp": 1, "firstName": "Old", "lastName": "Name"}), Response(), Response(200, [{"id": "u1"}]), Response(), Response(200, {"username": "u1", "email": "e"}), Response()])
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    user_id = await admin.create_keycloak_user("jane", "pw", "j@example.com", ["doctor"], full_name="Jane Doe", temporary_password=True)
    assert user_id == "u1"
    await admin.update_keycloak_user("u1", username="new", email="new@example.com", full_name="Jane Smith", enabled=False)
    client.responses = [Response(200, [{"id": "u1"}]), Response()]
    assert await admin.delete_keycloak_user("jane") == "u1"
    await admin.ensure_roles(["doctor", "nurse"])
    await admin.set_user_attribute("u1", "tenant_id", "t1")


@pytest.mark.asyncio
async def test_keycloak_admin_conflict_fallbacks_and_missing_roles(monkeypatch):
    monkeypatch.setattr(admin, "_headers", AsyncMock(return_value={}))
    client = Client([Response(409), Response(200, [{"id": "existing"}]), Response(), Response(), Response(201), Response(404), Response(200, []), Response(200, []), Response(200, {"username":"u"}), Response()])
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setattr(admin, "set_user_password", AsyncMock()); monkeypatch.setattr(admin, "assign_user_roles", AsyncMock())
    assert await admin.create_keycloak_user("u", "pw", "e", [], full_name=" ") == "existing"
    client = Client([Response(409), Response(200, [])]); monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    with pytest.raises(Exception): await admin.create_keycloak_user("u", "pw", "e", [])
    client = Client([Response(), Response(404), Response(200, [])]); monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    await admin.assign_user_roles("u", ["missing"])
    client = Client([Response(200, [])]); monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    assert await admin.delete_keycloak_user("missing") is None


@pytest.mark.asyncio
async def test_keycloak_admin_token_no_location_and_update_empty_name(monkeypatch):
    token_client = Client([Response(200, {"access_token": "admin"})]); monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: token_client)
    assert await admin._get_admin_token() == "admin"; token_client.responses = [Response(200, {"access_token": "admin"})]; assert (await admin._headers())["Authorization"] == "Bearer admin"
    monkeypatch.setattr(admin, "_headers", AsyncMock(return_value={}))
    client = Client([Response(201), Response(200, []), Response()]); monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setattr(admin, "set_user_password", AsyncMock()); monkeypatch.setattr(admin, "assign_user_roles", AsyncMock())
    assert await admin.create_keycloak_user("u", "pw", "e", []) is None
    client = Client([Response(200, {"id":"u", "firstName":"Old"}), Response()]); monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    await admin.update_keycloak_user("u", full_name="")
    client = Client([Response(200, {"id":"role"}), Response()]); monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client); await admin.ensure_roles(["doctor"])
    client = Client([Response(404)]); await admin.assign_user_roles("u", ["missing"])
    client = Client([Response(404)]); await admin.ensure_roles(["new-role"])
    db = MagicMock(); db.query.return_value.filter.return_value.first.return_value = None; assert admin.update_local_user(db, "missing") is None


def test_local_user_repository_operations():
    db = MagicMock(); existing = MagicMock(username="old", full_name="Old", email="old", role="user", hospital_id="h", is_active=True, force_password_change=False)
    db.query.return_value.filter.return_value.first.return_value = None
    created = admin.create_local_user(db, "sub", "e", "h", username="u", full_name="User", role="doctor")
    db.query.return_value.filter.return_value.first.return_value = existing
    assert created.email == "e"; updated = admin.update_local_user(db, "sub", username="new", email="new")
    assert updated.username == "new"; db.query.return_value.filter.return_value.first.return_value = None; assert admin.delete_local_user(db, "missing") is False
    db.query.return_value.filter.return_value.all.return_value = [existing]
    assert admin.get_local_users_by_hospital(db, "h") == [existing]
    db.query.return_value.filter.return_value.first.return_value = existing
    assert admin.delete_local_user(db, "sub") is True
    db.query.return_value.filter.return_value.first.return_value = existing
    assert admin.create_local_user(db, "sub", "new", "new-hospital", username=None, full_name=None, role=None, is_active=False, force_password_change=True) is existing
    db.query.return_value.filter.return_value.first.return_value = existing
    assert admin.update_local_user(db, "sub", full_name="Full", role="admin", hospital_id="h2").hospital_id == "h2"


@pytest.mark.asyncio
async def test_realm_creation_clients_roles_profiles_and_queries(monkeypatch):
    monkeypatch.setattr(realm, "_admin_headers", AsyncMock(return_value={"Authorization": "Bearer t"}))
    client = Client([Response(201)]); monkeypatch.setattr(realm.httpx, "AsyncClient", lambda **kw: client); await realm.create_tenant_realm("t1")
    client = Client([Response(201, headers={"location":"/clients/c1"}), Response(200, []), Response(201), Response(201)]); await realm.create_realm_client("t1")
    client = Client([Response(404), Response(201)]); await realm.ensure_realm_roles("t1", ["doctor"])
    client = Client([Response(200, {"attributes": []}), Response(201)]); await realm.add_tenant_id_to_user_profile("t1")
    client = Client([Response(200, [{"realm":"one"}, {"realm":"two"}])]); assert await realm.list_all_realms() == ["one", "two"]
    client = Client([Response(200, [{"name":"doctor"}])]); assert await realm.get_realm_roles("t1") == [{"name":"doctor"}]
    client = Client([Response(200, success=True)]); assert await realm.verify_tenant_realm_exists("t1") is True


@pytest.mark.asyncio
async def test_realm_role_and_user_listing_edge_cases(monkeypatch):
    monkeypatch.setattr(realm, "_admin_headers", AsyncMock(return_value={}))
    client = Client([Response(409)]); monkeypatch.setattr(realm.httpx, "AsyncClient", lambda **kw: client)
    await realm.create_tenant_realm("t")
    client = Client([Response(409)]); await realm.create_realm_client("t")
    client = Client([Response(404), Response(201)]); await realm.ensure_realm_roles("t", ["doctor"])
    client = Client([Response(404)]); await realm.add_tenant_id_to_user_profile("t")
    client = Client([Response(409)]); await realm.create_realm_role("t", "doctor", "desc")
    client = Client([Response(200), Response()]); await realm.update_realm_role("t", "doctor", description="new")
    client = Client([Response(404)]); await realm.delete_realm_role("t", "missing")
    client = Client([Response(200, {"attributes": []}), Response(201)]); await realm.add_tenant_id_to_user_profile("t")
    client = Client([Response(200, [{"id":"u"}]), Response(200, [])]);
    assert await realm.get_all_realm_users("t") == [{"id":"u"}]
    await realm.update_realm_role("t", "doctor")


@pytest.mark.asyncio
async def test_realm_mapper_failures_deletion_and_setup_orchestration(monkeypatch):
    monkeypatch.setattr(realm, "_admin_headers", AsyncMock(return_value={}))
    client = Client([Response(201, headers={}), Response(200, [{"id":"c1"}]), Response(200, [{"name":"tenant_id"}]), Response(500, success=False)])
    monkeypatch.setattr(realm.httpx, "AsyncClient", lambda **kw: client)
    await realm.create_realm_client("t")
    client = Client([Response(409)]); await realm.create_realm_role("t", "doctor")
    client = Client([Response(204)]); await realm.delete_tenant_realm("t")
    client = Client([Response(200, [])]); assert await realm.get_all_realm_users("t") == []
    monkeypatch.setattr(realm, "create_tenant_realm", AsyncMock()); monkeypatch.setattr(realm, "create_realm_client", AsyncMock()); monkeypatch.setattr(realm, "ensure_realm_roles", AsyncMock()); monkeypatch.setattr(realm, "add_tenant_id_to_user_profile", AsyncMock())
    await realm.setup_tenant_realm("t")


@pytest.mark.asyncio
async def test_realm_http_error_and_existing_attribute_paths(monkeypatch):
    monkeypatch.setattr(realm, "_admin_headers", AsyncMock(return_value={}))
    client = Client([Response(201), Response(200, [{"id": "c1"}]), Response(200, []), Response(200, success=False), Response(200, success=False)])
    monkeypatch.setattr(realm.httpx, "AsyncClient", lambda **kw: client)
    await realm.create_realm_client("t")
    client = Client([Response(200, {"attributes": [{"name": "tenant_id"}]})]); await realm.add_tenant_id_to_user_profile("t")
    client = Client([Response(404)]); await realm.delete_tenant_realm("t")
    client = Client([Response(204)]); await realm.delete_tenant_realm("t")
    client = Client([Response(500, success=False)])
    with pytest.raises(Exception): await realm.create_realm_role("t", "bad")
    client = Client([Response(200)]); await realm.delete_realm_role("t", "good")
