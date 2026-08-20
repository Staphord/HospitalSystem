from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import keycloak_admin as kc


class Response:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = str(self._body)
        self.reason_phrase = "error"
        self.is_success = status_code < 400

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.text)


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def _next(self, *args, **kwargs):
        return next(self.responses)

    get = _next
    post = _next
    put = _next
    delete = _next

    async def request(self, *args, **kwargs):
        return next(self.responses)


def test_name_and_error_helpers():
    assert kc._person_name("Dr. Jane/(Doe)", "Staff") == "Dr. Jane Doe"
    assert kc._person_name("", "Staff") == "Staff"
    assert kc._keycloak_error(Response(400, {"errorMessage": "bad"})) == "bad"
    assert kc._keycloak_error(Response(400, {"error": "bad"})) == "bad"
    assert kc._admin_api_url("realm").endswith("/admin/realms/realm")


@pytest.mark.asyncio
async def test_keycloak_user_creation_success_and_conflict_resolution():
    headers = {"Authorization": "Bearer token"}
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value=headers), \
         patch.object(kc, "set_user_password", new_callable=AsyncMock) as password, \
         patch.object(kc, "assign_user_roles", new_callable=AsyncMock) as roles, \
         patch("httpx.AsyncClient", return_value=Client([Response(201, headers={"location": "/users/u1"}), Response(204)])):
        assert await kc.create_keycloak_user("jane", "Secret1!", "jane@example.com", ["doctor"], "Jane Doe", "r") == "u1"
    password.assert_awaited_once()
    roles.assert_awaited_once()

    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value=headers), \
         patch.object(kc, "set_user_password", new_callable=AsyncMock), \
         patch.object(kc, "assign_user_roles", new_callable=AsyncMock), \
         patch("httpx.AsyncClient", return_value=Client([Response(409), Response(200, [{"id": "existing"}]), Response(204)])):
        assert await kc.create_keycloak_user("jane", "Secret1!", "jane@example.com", [], realm="r") == "existing"


@pytest.mark.asyncio
async def test_keycloak_user_and_role_operations():
    headers = {"Authorization": "Bearer token"}
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value=headers), \
         patch("httpx.AsyncClient", return_value=Client([Response(204), Response(204), Response(204)])):
        await kc.set_user_password("u", "Secret1!", temporary=True)
        await kc.remove_user_roles("u", [{"name": "doctor"}])
        await kc.logout_user_sessions("u")

    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value=headers), \
         patch("httpx.AsyncClient", return_value=Client([Response(200, [{"name": "doctor"}])])):
        assert (await kc.get_user_realm_roles("u"))[0]["name"] == "doctor"

    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value=headers), \
         patch("httpx.AsyncClient", return_value=Client([Response(200, {"name": "doctor"}), Response(200, {"name": "nurse"})])):
        await kc.update_realm_role("r", "doctor", "nurse")

    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value=headers), \
         patch("httpx.AsyncClient", return_value=Client([Response(201), Response(200, {"name": "doctor"})])):
        assert (await kc.create_realm_role("r", "doctor"))["name"] == "doctor"

    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value=headers), \
         patch("httpx.AsyncClient", return_value=Client([Response(200), Response(204)])):
        await kc.delete_realm_role("r", "doctor")


@pytest.mark.asyncio
async def test_keycloak_listing_assignment_and_local_user_helpers():
    headers = {"Authorization": "Bearer token"}
    with patch.object(kc, "_headers", new_callable=AsyncMock, return_value=headers), \
         patch("httpx.AsyncClient", return_value=Client([Response(200, {"id": "doctor"}), Response(404), Response(204)])):
        await kc.assign_user_roles("u", ["doctor", "missing"])

    with patch.object(kc, "get_user_realm_roles", new_callable=AsyncMock, return_value=[{"name": "doctor"}, {"name": "default-roles-r"}, {"name": "offline_access"}]), \
         patch.object(kc, "remove_user_roles", new_callable=AsyncMock), \
         patch.object(kc, "assign_user_roles", new_callable=AsyncMock):
        await kc.replace_user_roles("u", ["nurse"])

    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.first.return_value = None
    created = kc.create_local_user(db, "u", "u@example.com", "tenant", username="u", role="doctor")
    assert created.keycloak_sub == "u"

    existing = SimpleNamespace(keycloak_sub="u", username="old", full_name="Old", email="old@example.com", role="nurse")
    query.first.return_value = existing
    assert kc.create_local_user(db, "u", "new@example.com", "tenant", username="new").email == "new@example.com"
    query.first.return_value = None
    assert kc.update_local_user(db, "missing") is None
    query.first.return_value = existing
    assert kc.update_local_user(db, "u", role="doctor", clear_deleted=True) is existing
    query.first.return_value = None
    assert kc.delete_local_user(db, "missing") is False
    query.first.return_value = existing
    assert kc.delete_local_user(db, "u") is True
