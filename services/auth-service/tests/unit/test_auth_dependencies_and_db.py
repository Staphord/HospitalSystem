import pytest
from unittest.mock import AsyncMock

from app import dependencies
from app.db import session as tenant_session
from app.db import master
from app.core.tenant_auth import TenantContext


def test_master_db_generator_and_role_factory(db_session, monkeypatch):
    monkeypatch.setattr(dependencies, "_get_db", lambda: iter([db_session]))
    assert list(dependencies.get_master_db()) == [db_session]
    assert callable(dependencies.require_role("doctor"))


@pytest.mark.asyncio
async def test_tenant_dependency_generators(monkeypatch):
    async def sessions(_):
        yield "session"
    monkeypatch.setattr(dependencies, "get_tenant_session", sessions)
    ctx = TenantContext("tenant", "sub", None, None, [], False)
    result = [s async for s in dependencies.get_tenant_session_dep(ctx)]
    assert result == ["session"]

    monkeypatch.setattr(tenant_session, "get_tenant_session", sessions)
    assert [s async for s in tenant_session.get_tenant_db("tenant")] == ["session"]


def test_master_session_context_manager(monkeypatch):
    fake = type("DB", (), {"close": lambda self: setattr(self, "closed", True)})()
    monkeypatch.setattr(master, "MasterSessionLocal", lambda: fake)
    with master.get_master_session() as db:
        assert db is fake
    assert fake.closed is True
    assert master.get_master_db() is fake
