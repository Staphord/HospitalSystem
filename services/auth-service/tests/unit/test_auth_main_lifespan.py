import pytest
from unittest.mock import AsyncMock

from app.main import app, lifespan


@pytest.mark.asyncio
async def test_lifespan_startup_and_shutdown(monkeypatch):
    monkeypatch.setattr("app.core.database.init_db", lambda: None)
    monkeypatch.setattr("app.services.keycloak_admin.ensure_superadmin_client", AsyncMock())
    monkeypatch.setattr("app.services.keycloak_realm.set_realm_token_lifespan", AsyncMock())
    async def subscriber():
        return None
    monkeypatch.setattr("app.events.subscriber.start_subscriber", subscriber)
    async with lifespan(app):
        assert True


@pytest.mark.asyncio
async def test_main_health_security_headers_and_lifespan_failures(monkeypatch):
    from app import main
    from starlette.requests import Request
    from starlette.responses import Response

    assert await main.health() == {"status": "ok", "service": "auth-service"}
    async def next_response(request):
        return Response("ok")
    response = await main.security_headers(
        Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
        next_response,
    )
    assert response.headers["X-Frame-Options"] == "DENY"
    monkeypatch.setattr(main.settings, "environment", "prod")
    response = await main.security_headers(
        Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
        next_response,
    )
    assert response.headers["Content-Security-Policy"] == "default-src 'none'"
    monkeypatch.setattr(main, "get_session_local", lambda: None, raising=False)
    result = await main.global_exception_handler(
        Request({"type": "http", "method": "GET", "path": "/x", "headers": []}), RuntimeError("boom")
    )
    assert result.status_code == 500

    monkeypatch.setattr("app.core.database.init_db", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    with pytest.raises(RuntimeError):
        async with lifespan(main.app):
            pass

    monkeypatch.setattr("app.core.database.init_db", lambda: None)
    monkeypatch.setattr("app.services.keycloak_admin.ensure_superadmin_client", AsyncMock(side_effect=RuntimeError("kc")))
    monkeypatch.setattr("app.services.keycloak_realm.set_realm_token_lifespan", AsyncMock(side_effect=RuntimeError("realm")))
    monkeypatch.setattr("app.events.subscriber.start_subscriber", AsyncMock(side_effect=RuntimeError("events")))
    async with lifespan(main.app):
        pass

    async def running_subscriber():
        import asyncio
        await asyncio.Future()
    monkeypatch.setattr("app.core.database.init_db", lambda: None)
    monkeypatch.setattr("app.services.keycloak_admin.ensure_superadmin_client", AsyncMock())
    monkeypatch.setattr("app.services.keycloak_realm.set_realm_token_lifespan", AsyncMock())
    monkeypatch.setattr("app.events.subscriber.start_subscriber", running_subscriber)
    async with lifespan(main.app):
        pass
