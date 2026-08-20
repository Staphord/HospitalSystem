"""Unit tests for keycloak_admin.py and keycloak_realm.py in master-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from app.services import keycloak_admin as admin
from app.services import keycloak_realm as realm
from app.services.keycloak_admin import assign_user_roles
from app.services.keycloak_realm import (
    get_realm_url,
    get_realm_token_url,
    create_tenant_realm,
    create_realm_client,
    ensure_realm_roles,
    add_tenant_id_to_user_profile,
    delete_tenant_realm,
)

class Response:
    def __init__(self, status_code=200, data=None, headers=None, success=None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.headers = headers or {}
        self.is_success = status_code < 400 if success is None else success

    def raise_for_status(self):
        if not self.is_success:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class Client:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0) if self.responses else Response()

    async def get(self, url, **kwargs):
        return self._next("get", url, **kwargs)

    async def post(self, url, **kwargs):
        return self._next("post", url, **kwargs)

    async def put(self, url, **kwargs):
        return self._next("put", url, **kwargs)

    async def delete(self, url, **kwargs):
        return self._next("delete", url, **kwargs)


@pytest.mark.asyncio
async def test_keycloak_user_lifecycle_and_role_management(monkeypatch):
    monkeypatch.setattr(admin, "_headers", AsyncMock(return_value={"Authorization": "Bearer token"}))
    client = Client([
        Response(201, headers={"location": "/users/u1"}),
        Response(), Response(),
        Response(200, {"id": "role"}),
        Response(200, {"id": "role2"}),
        Response(), Response(),
        Response(200, {"username": "old", "id": "u1", "createdTimestamp": 1, "firstName": "Old", "lastName": "Name"}),
        Response(),
        Response(200, [{"id": "u1"}]),
        Response(),
        Response(200, {"username": "u1", "email": "e"}),
        Response()
    ])
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
    client = Client([
        Response(409),
        Response(200, [{"id": "existing"}]),
        Response(), Response(), Response(201),
        Response(404), Response(200, []), Response(200, []),
        Response(200, {"username": "u"}), Response()
    ])
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setattr(admin, "set_user_password", AsyncMock())
    monkeypatch.setattr(admin, "assign_user_roles", AsyncMock())
    assert await admin.create_keycloak_user("u", "pw", "e", [], full_name=" ") == "existing"

    client = Client([Response(409), Response(200, [])])
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    with pytest.raises(Exception):
        await admin.create_keycloak_user("u", "pw", "e", [])

    client = Client([Response(), Response(404), Response(200, [])])
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    await admin.assign_user_roles("u", ["missing"])

    client = Client([Response(200, [])])
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    assert await admin.delete_keycloak_user("missing") is None


@pytest.mark.asyncio
async def test_keycloak_admin_token_no_location_and_update_empty_name(monkeypatch):
    token_client = Client([Response(200, {"access_token": "admin"})])
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: token_client)
    assert await admin._get_admin_token() == "admin"
    token_client.responses = [Response(200, {"access_token": "admin"})]
    assert (await admin._headers())["Authorization"] == "Bearer admin"

    monkeypatch.setattr(admin, "_headers", AsyncMock(return_value={}))
    client = Client([Response(201), Response(200, []), Response()])
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setattr(admin, "set_user_password", AsyncMock())
    monkeypatch.setattr(admin, "assign_user_roles", AsyncMock())
    assert await admin.create_keycloak_user("u", "pw", "e", []) is None

    client = Client([Response(200, {"id": "u", "firstName": "Old"}), Response()])
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    await admin.update_keycloak_user("u", full_name="")

    client = Client([Response(200, {"id": "role"}), Response()])
    monkeypatch.setattr(admin.httpx, "AsyncClient", lambda **kw: client)
    await admin.ensure_roles(["doctor"])

    client = Client([Response(404)])
    await admin.assign_user_roles("u", ["missing"])

    client = Client([Response(404)])
    await admin.ensure_roles(["new-role"])

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert admin.update_local_user(db, "missing") is None


def test_local_user_repository_operations():
    db = MagicMock()
    existing = MagicMock(username="old", full_name="Old", email="old", role="user", hospital_id="h", is_active=True, force_password_change=False)
    db.query.return_value.filter.return_value.first.return_value = None
    created = admin.create_local_user(db, "sub", "e", "h", username="u", full_name="User", role="doctor")
    db.query.return_value.filter.return_value.first.return_value = existing
    assert created.email == "e"
    updated = admin.update_local_user(db, "sub", username="new", email="new")
    assert updated.username == "new"
    db.query.return_value.filter.return_value.first.return_value = None
    assert admin.delete_local_user(db, "missing") is False

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
    client = Client([Response(201)])
    monkeypatch.setattr(realm.httpx, "AsyncClient", lambda **kw: client)
    await realm.create_tenant_realm("t1")

    client = Client([Response(201, headers={"location": "/clients/c1"}), Response(200, []), Response(201), Response(201)])
    await realm.create_realm_client("t1")

    client = Client([Response(404), Response(201)])
    await realm.ensure_realm_roles("t1", ["doctor"])

    client = Client([Response(200, {"attributes": []}), Response(201)])
    await realm.add_tenant_id_to_user_profile("t1")

    client = Client([Response(200, [{"realm": "one"}, {"realm": "two"}])])
    assert await realm.list_all_realms() == ["one", "two"]


@pytest.mark.asyncio
async def test_assign_user_roles_missing_role():
    mock_res_role_found = MagicMock(is_success=True)
    mock_res_role_found.json.return_value = {"id": "r1", "name": "doctor"}
    mock_res_role_missing = MagicMock(is_success=False)
    mock_res_post = MagicMock(is_success=True)

    mock_client = AsyncMock()
    mock_client.get.side_effect = [mock_res_role_found, mock_res_role_missing]
    mock_client.post.return_value = mock_res_post
    mock_client.__aenter__.return_value = mock_client

    with patch("app.services.keycloak_admin._headers", AsyncMock(return_value={})):
        with patch("httpx.AsyncClient", return_value=mock_client):
            await assign_user_roles("user-123", ["doctor", "missing_role"], realm="master")


def test_realm_urls():
    assert "realms/t1" in get_realm_url("t1")
    assert "protocol/openid-connect/token" in get_realm_token_url("t1")


@pytest.mark.asyncio
async def test_create_tenant_realm_error():
    mock_res = MagicMock(status_code=500)
    mock_res.raise_for_status.side_effect = Exception("HTTP 500")

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_res
    mock_client.__aenter__.return_value = mock_client

    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})):
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(Exception, match="HTTP 500"):
                await create_tenant_realm("t_fail")


@pytest.mark.asyncio
async def test_create_realm_client_search_and_warnings():
    mock_res_post = MagicMock(status_code=201, headers={})
    mock_res_search = MagicMock(status_code=200)
    mock_res_search.json.return_value = [{"id": "client-uuid-1"}]
    mock_res_mappers = MagicMock(status_code=200)
    mock_res_mappers.json.return_value = []
    mock_res_add_mapper = MagicMock(is_success=False, status_code=400)

    mock_client = AsyncMock()
    mock_client.post.side_effect = [mock_res_post, mock_res_add_mapper, mock_res_add_mapper]
    mock_client.get.side_effect = [mock_res_search, mock_res_mappers]
    mock_client.__aenter__.return_value = mock_client

    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})):
        with patch("httpx.AsyncClient", return_value=mock_client):
            await create_realm_client("t1")

    mock_res_err = MagicMock(status_code=500)
    mock_res_err.raise_for_status.side_effect = Exception("HTTP 500 Client")
    mock_client.post.side_effect = None
    mock_client.post.return_value = mock_res_err

    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})):
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(Exception, match="HTTP 500 Client"):
                await create_realm_client("t1")


@pytest.mark.asyncio
async def test_ensure_realm_roles_default_and_status_codes():
    mock_res_get = MagicMock(status_code=404)
    mock_res_post_201 = MagicMock(status_code=201)
    mock_res_post_409 = MagicMock(status_code=409)
    mock_res_post_500 = MagicMock(status_code=500)
    mock_res_post_500.raise_for_status.side_effect = Exception("HTTP 500 Role")

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_res_get
    mock_client.post.side_effect = [mock_res_post_201, mock_res_post_409, mock_res_post_500]
    mock_client.__aenter__.return_value = mock_client

    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})):
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(Exception, match="HTTP 500 Role"):
                await ensure_realm_roles("t1", roles=["role1", "role2", "role3"])


@pytest.mark.asyncio
async def test_add_tenant_id_to_user_profile_put_failure():
    mock_res_get = MagicMock(is_success=True)
    mock_res_get.json.return_value = {"attributes": []}
    mock_res_put = MagicMock(is_success=False, status_code=400)

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_res_get
    mock_client.put.return_value = mock_res_put
    mock_client.__aenter__.return_value = mock_client

    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})):
        with patch("httpx.AsyncClient", return_value=mock_client):
            await add_tenant_id_to_user_profile("t1")


@pytest.mark.asyncio
async def test_delete_tenant_realm_404_and_500():
    mock_res_404 = MagicMock(status_code=404)
    mock_res_500 = MagicMock(status_code=500)
    mock_res_500.raise_for_status.side_effect = Exception("HTTP 500 Delete")

    mock_client = AsyncMock()
    mock_client.delete.side_effect = [mock_res_404, mock_res_500]
    mock_client.__aenter__.return_value = mock_client

    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})):
        with patch("httpx.AsyncClient", return_value=mock_client):
            await delete_tenant_realm("t1")
            with pytest.raises(Exception, match="HTTP 500 Delete"):
                await delete_tenant_realm("t1")


@pytest.mark.asyncio
async def test_keycloak_realm_role_and_user_helpers():
    from app.services.keycloak_realm import (
        list_all_realms,
        verify_tenant_realm_exists,
        get_realm_roles,
        create_realm_role,
        update_realm_role,
        delete_realm_role,
        get_all_realm_users,
        setup_tenant_realm,
    )

    mock_realms_res = MagicMock(status_code=200, is_success=True)
    mock_realms_res.json.return_value = [{"realm": "master"}, {"realm": "t1"}]

    mock_roles_res = MagicMock(status_code=200, is_success=True)
    mock_roles_res.json.return_value = [{"name": "doctor"}]

    mock_create_role_201 = MagicMock(status_code=201, is_success=True)
    mock_create_role_409 = MagicMock(status_code=409, is_success=False)

    mock_update_role_204 = MagicMock(status_code=204, is_success=True)

    mock_delete_role_404 = MagicMock(status_code=404, is_success=False)
    mock_delete_role_204 = MagicMock(status_code=204, is_success=True)

    mock_users_batch1 = MagicMock(status_code=200, is_success=True)
    mock_users_batch1.json.return_value = [{"username": "u1"}]
    mock_users_batch2 = MagicMock(status_code=200, is_success=True)
    mock_users_batch2.json.return_value = []

    mock_client = AsyncMock()
    mock_client.get.side_effect = [
        mock_realms_res,
        mock_realms_res,
        mock_roles_res,
        mock_users_batch1,
        mock_users_batch2,
    ]
    mock_client.post.side_effect = [mock_create_role_201, mock_create_role_409]
    mock_client.put.return_value = mock_update_role_204
    mock_client.delete.side_effect = [mock_delete_role_404, mock_delete_role_204]
    mock_client.__aenter__.return_value = mock_client

    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})):
        with patch("httpx.AsyncClient", return_value=mock_client):
            realms = await list_all_realms()
            assert realms == ["master", "t1"]

            exists = await verify_tenant_realm_exists("t1")
            assert exists is True

            roles = await get_realm_roles("t1")
            assert len(roles) == 1

            await create_realm_role("t1", "nurse", "Nurse role")
            await create_realm_role("t1", "doctor", "Doctor role")

            await update_realm_role("t1", "doctor", new_name="lead_doctor", description="Lead Doctor")
            await update_realm_role("t1", "doctor")  # Empty payload noop

            await delete_realm_role("t1", "old_role")
            await delete_realm_role("t1", "lead_doctor")

            users = await get_all_realm_users("t1")
            assert len(users) == 1

    with patch("app.services.keycloak_realm.create_tenant_realm", AsyncMock()), \
         patch("app.services.keycloak_realm.create_realm_client", AsyncMock()), \
         patch("app.services.keycloak_realm.ensure_realm_roles", AsyncMock()), \
         patch("app.services.keycloak_realm.add_tenant_id_to_user_profile", AsyncMock()):
        await setup_tenant_realm("t_setup")


@pytest.mark.asyncio
async def test_keycloak_realm_additional_branch_coverage():
    from app.services.keycloak_realm import (
        create_tenant_realm,
        create_realm_client,
        add_tenant_id_to_user_profile,
        delete_tenant_realm,
        create_realm_role,
    )

    # test 409 status code in create_tenant_realm
    mock_res_409 = MagicMock(status_code=409)
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_res_409
    mock_client.__aenter__.return_value = mock_client
    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})), \
         patch("httpx.AsyncClient", return_value=mock_client):
        await create_tenant_realm("t_existing")

    # test missing client_uuid in create_realm_client
    mock_res_post_201 = MagicMock(status_code=201, headers={})
    mock_res_search_empty = MagicMock(status_code=200)
    mock_res_search_empty.json.return_value = []
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_res_post_201
    mock_client.get.return_value = mock_res_search_empty
    mock_client.__aenter__.return_value = mock_client
    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})), \
         patch("httpx.AsyncClient", return_value=mock_client):
        await create_realm_client("t_no_uuid")

    # test profile read failure in add_tenant_id_to_user_profile
    mock_res_profile_fail = MagicMock(is_success=False, status_code=500)
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_res_profile_fail
    mock_client.__aenter__.return_value = mock_client
    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})), \
         patch("httpx.AsyncClient", return_value=mock_client):
        await add_tenant_id_to_user_profile("t_profile_fail")

    # test successful 204 in delete_tenant_realm
    mock_res_204 = MagicMock(status_code=204)
    mock_client = AsyncMock()
    mock_client.delete.return_value = mock_res_204
    mock_client.__aenter__.return_value = mock_client
    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})), \
         patch("httpx.AsyncClient", return_value=mock_client):
        await delete_tenant_realm("t_delete_204")

    # test role create error in create_realm_role
    mock_res_role_err = MagicMock(status_code=500, is_success=False)
    mock_res_role_err.raise_for_status.side_effect = Exception("HTTP 500 Role Create")
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_res_role_err
    mock_client.__aenter__.return_value = mock_client
    with patch("app.services.keycloak_realm._admin_headers", AsyncMock(return_value={})), \
         patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(Exception, match="HTTP 500 Role Create"):
            await create_realm_role("t1", "fail_role")

