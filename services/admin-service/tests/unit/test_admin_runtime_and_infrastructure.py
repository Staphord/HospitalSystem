import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from starlette.requests import Request

from app.core import database
from app.core.middleware import AuditLogMiddleware, ImpersonationBannerMiddleware, ReadOnlyScopeMiddleware
from app.db import tenant_sync
from app import main
from app.api.v1 import router as shared_api
from app import dependencies
from app.db import master


def scope(method="GET", path="/x"):
    return {"type": "http", "method": method, "path": path, "headers": [], "client": ("127.0.0.1", 1), "scheme": "http", "server": ("x", 80)}


def test_database_lifecycle_and_contexts():
    fake_session = MagicMock()
    with patch.object(database, "_ensure_database_exists"), patch.object(database, "create_engine", return_value=MagicMock()), patch.object(database, "sessionmaker", return_value=lambda: fake_session):
        database._engine = None; database._SessionLocal = None
        database.init_db()
        assert database.get_session_local() is not None
    database._SessionLocal = lambda: fake_session
    generator = database.get_db(); yielded = next(generator); assert yielded is fake_session; generator.close()
    context = database.get_hospital_context("h1"); assert context.hospital_id == "h1"
    database.close_hospital_context(context)


def test_database_creation_paths_and_tenant_engine_cache():
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    with patch.object(database, "create_engine", return_value=engine), patch.object(database.settings, "database_url", "sqlite:///db"):
        connection.execute.return_value = SimpleNamespace(scalar=lambda: True)
        database._ensure_database_exists()
    tenant_sync._tenant_engine_cache.clear()
    master = MagicMock(); master.execute.return_value.scalar.return_value = "encrypted"
    session_factory = MagicMock(return_value=MagicMock())
    with patch.object(tenant_sync, "get_master_db", return_value=master), patch.object(tenant_sync, "decrypt_dsn", return_value="sqlite:///tenant"), patch.object(tenant_sync, "create_engine", return_value=MagicMock()), patch.object(tenant_sync, "sessionmaker", return_value=session_factory):
        engine_pair = tenant_sync._get_tenant_engine("t1")
        assert tenant_sync._get_tenant_engine("t1") == engine_pair
        session = next(tenant_sync.get_tenant_db_sync("t1")); assert session is session_factory.return_value


def test_database_autocreate_and_router_contracts():
    from sqlalchemy.exc import OperationalError

    first = MagicMock()
    first.connect.side_effect = OperationalError("connect", {}, Exception("missing"))
    admin = MagicMock()
    admin.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = 0
    with patch.object(database, "create_engine", side_effect=[first, admin]), \
         patch.object(database, "settings", SimpleNamespace(
             database_url="postgresql://u:p@db/hospital",
             db_admin_url="postgresql://u:p@db/postgres",
         )):
        database._ensure_database_exists()
    with patch.object(database, "create_engine", side_effect=[first, admin]), \
         patch.object(database, "settings", SimpleNamespace(
             database_url="postgresql://u:p@db/",
             db_admin_url="postgresql://u:p@db/postgres",
         )):
        with pytest.raises(ValueError):
            database._ensure_database_exists()

    existing_engine = MagicMock()
    existing_engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = 1
    with patch.object(database, "create_engine", return_value=existing_engine), \
         patch.object(database, "settings", SimpleNamespace(database_url="postgresql://u:p@db/hospital", db_admin_url="postgresql://u:p@db/postgres")):
        database._ensure_database_exists()
    missing = MagicMock(); missing.connect.side_effect = OperationalError("connect", {}, Exception("missing"))
    existing_admin = MagicMock(); existing_admin.connect.return_value.__enter__.return_value.execute.return_value.scalar.return_value = 1
    with patch.object(database, "create_engine", side_effect=[missing, existing_admin]), patch.object(database, "settings", SimpleNamespace(database_url="postgresql://u:p@db/hospital", db_admin_url="postgresql://u:p@db/postgres")):
        database._ensure_database_exists()

    class ConcreteRouter(database.DatabaseRouter):
        def get_session(self, hospital_id):
            return super().get_session(hospital_id)
    with pytest.raises(NotImplementedError):
        ConcreteRouter().get_session("t")

    assert database.get_hospital_context("hospital").hospital_id == "hospital"


@pytest.mark.asyncio
async def test_shared_read_only_handlers_validate_service_rows():
    from app.api.v1.admin.schemas import DepartmentOut, HospitalUserOut, InsuranceProviderOut

    db = MagicMock()
    ctx = SimpleNamespace(tenant_id="t1")
    with patch.object(shared_api.admin_svc, "list_insurance_providers", return_value=[]):
        assert await shared_api.get_shared_providers(Request(scope()), db=db, ctx=ctx) == []
    with patch.object(shared_api.admin_svc, "list_departments", return_value=[]):
        assert await shared_api.get_shared_departments(Request(scope()), db=db, ctx=ctx) == []
    with patch.object(shared_api.admin_svc, "list_users", return_value=[]):
        assert await shared_api.get_shared_users(Request(scope()), db=db, ctx=ctx) == []


def test_dependencies_and_master_session_lifecycle():
    session = MagicMock()
    with patch.object(dependencies, "get_tenant_db_sync", return_value=iter([session])):
        assert list(dependencies.get_tenant_db_for_request(SimpleNamespace(tenant_id="t1"))) == [session]
    with pytest.raises(Exception):
        list(dependencies.get_tenant_db_for_request(SimpleNamespace(tenant_id=None)))
    with patch.object(master, "MasterSessionLocal", return_value=session):
        assert master.get_master_db() is session
        with master.get_master_session() as current:
            assert current is session
        session.close.assert_called_once()
    with patch.object(dependencies, "_get_db", return_value=iter([session])):
        assert list(dependencies.get_tenant_db()) == [session]
    assert __import__("asyncio").run(dependencies.get_current_user("user")) == "user"
    assert callable(dependencies.require_role("doctor"))


@pytest.mark.asyncio
async def test_middleware_readonly_banner_and_audit_paths():
    async def call_next(request): return PlainTextResponse("ok")
    readonly = ReadOnlyScopeMiddleware(FastAPI())
    request = Request(scope("POST")); request.state.tenant = SimpleNamespace(scope="readonly")
    response = await readonly.dispatch(request, call_next)
    assert response.status_code == 403 and response.headers["x-impersonation-banner"] == "true"
    request = Request(scope("POST")); request.state.tenant = SimpleNamespace(scope="full")
    assert (await readonly.dispatch(request, call_next)).status_code == 200
    banner = ImpersonationBannerMiddleware(FastAPI())
    request = Request(scope()); request.state.tenant = SimpleNamespace(scope="readonly")
    assert (await banner.dispatch(request, call_next)).headers["x-impersonation-banner"] == "true"
    audit = AuditLogMiddleware(FastAPI())
    request = Request(scope("POST", "/write")); request.state.tenant = SimpleNamespace(tenant_id="t1"); request.state.user_sub = "u"
    with patch("app.db.tenant_sync._get_tenant_engine", return_value=(None, lambda: MagicMock())), patch("app.services.audit_service.log_change"):
        response = await audit.dispatch(request, call_next)
    assert response.headers["x-request-id"]
    request = Request(scope("OPTIONS"))
    assert (await audit.dispatch(request, call_next)).body == b"ok"
    request = Request(scope("POST", "/write")); request.state.tenant = SimpleNamespace(tenant_id=None)
    await audit.dispatch(request, call_next)
    request = Request(scope("POST", "/write")); request.state.tenant = SimpleNamespace(tenant_id="t1"); request.state.user_sub = "u"
    with patch("app.db.tenant_sync._get_tenant_engine", side_effect=RuntimeError("db")):
        await audit.dispatch(request, call_next)


@pytest.mark.asyncio
async def test_rabbit_connection_close_and_reconnect_paths():
    from app.messaging import connection
    old = SimpleNamespace(is_closed=False, close=AsyncMock(), channel=AsyncMock())
    connection._connection = old
    await connection.close_connection()
    old.close.assert_awaited_once()
    new = SimpleNamespace(is_closed=False, channel=AsyncMock())
    with patch.object(connection.aio_pika, "connect_robust", new_callable=AsyncMock, return_value=new):
        assert await connection.get_connection() is new
    await connection.get_channel()
    new.channel.assert_awaited_once()
    connection._connection = SimpleNamespace(is_closed=True)
    await connection.close_connection()


@pytest.mark.asyncio
async def test_lifespan_startup_shutdown_and_health():
    app = FastAPI()
    with patch("app.core.database.init_db"), patch("app.events.subscriber.start_subscriber", new_callable=AsyncMock), patch("app.services.backup.backup_scheduler_loop", new_callable=AsyncMock):
        async with main.lifespan(app):
            result = await main.health()
            assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_lifespan_handles_background_start_failures():
    app = FastAPI()
    with patch("app.core.database.init_db"), \
         patch("app.events.subscriber.start_subscriber", new_callable=AsyncMock), \
         patch("app.services.backup.backup_scheduler_loop", new_callable=AsyncMock), \
         patch("asyncio.create_task", side_effect=RuntimeError("task")):
        async with main.lifespan(app):
            pass


@pytest.mark.asyncio
async def test_security_headers_production_branch():
    async def call_next(request): return PlainTextResponse("ok")
    with patch.object(main.settings, "environment", "prod"):
        response = await main.security_headers(Request(scope()), call_next)
    assert response.headers["Content-Security-Policy"] == "default-src 'none'"


@pytest.mark.asyncio
async def test_lifespan_cancellation_cleanup_and_empty_cors_branch():
    class CancelledTask:
        def cancel(self): pass
        def __await__(self):
            async def cancelled():
                raise asyncio.CancelledError()
            return cancelled().__await__()

    app = FastAPI()
    with patch("app.core.database.init_db"), \
         patch("app.events.subscriber.start_subscriber", new=lambda: "subscriber"), \
         patch("app.services.backup.backup_scheduler_loop", new=lambda stop: "backup"), \
         patch("asyncio.create_task", side_effect=[CancelledTask(), CancelledTask()]):
        async with main.lifespan(app):
            pass
    import importlib
    original = main.settings.allowed_origins
    try:
        with patch.object(main.settings, "allowed_origins", ""):
            importlib.reload(main)
    finally:
        main.settings.allowed_origins = original
        importlib.reload(main)
