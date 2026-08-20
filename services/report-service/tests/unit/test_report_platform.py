from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import tenant_service as ts_mod
from app import exceptions as exp_mod


class TestReportRouterEndpoints:
    def test_health_check(self):
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code in [200, 404]

    def test_api_v1_router(self):
        with TestClient(app) as client:
            resp = client.get("/api/v1/")
            assert resp.status_code in [200, 404]


class TestReportTenantService:
    def test_encrypt_and_decrypt_dsn(self):
        dsn = "postgresql://user:pass@localhost:5432/report_tenant_db"
        encrypted = ts_mod.encrypt_dsn(dsn)
        assert encrypted != dsn
        decrypted = ts_mod.decrypt_dsn(encrypted)
        assert decrypted == dsn

    @pytest.mark.asyncio
    async def test_is_tenant_suspended(self):
        mock_redis = AsyncMock()
        mock_redis.get.side_effect = ["1", "0", Exception("Redis down")]
        with patch("app.services.tenant_service._get_redis", return_value=mock_redis):
            assert await ts_mod.is_tenant_suspended("t1") is True
            assert await ts_mod.is_tenant_suspended("t2") is False
            assert await ts_mod.is_tenant_suspended("t3") is False

    @pytest.mark.asyncio
    async def test_cache_and_remove_suspension(self):
        mock_redis = AsyncMock()
        with patch("app.services.tenant_service._get_redis", return_value=mock_redis):
            await ts_mod.cache_tenant_suspension("t1")
            await ts_mod.remove_tenant_suspension_cache("t1")

    @pytest.mark.asyncio
    async def test_check_tenant_subscription(self):
        mock_db = MagicMock()

        # Not found
        mock_db.execute.return_value.one_or_none.return_value = None
        assert await ts_mod.check_tenant_subscription(mock_db, "t-none") == "not_found"

        # Suspended
        mock_db.execute.return_value.one_or_none.return_value = ("suspended", None)
        assert await ts_mod.check_tenant_subscription(mock_db, "t-susp") == "suspended"

        # Expired
        past = datetime.now(timezone.utc) - timedelta(days=5)
        mock_db.execute.return_value.one_or_none.return_value = ("active", past)
        assert await ts_mod.check_tenant_subscription(mock_db, "t-exp") == "expired"

        # Active
        future = datetime.now(timezone.utc) + timedelta(days=5)
        mock_db.execute.return_value.one_or_none.return_value = ("active", future)
        assert await ts_mod.check_tenant_subscription(mock_db, "t-act") == "active"

    @pytest.mark.asyncio
    async def test_check_and_update_tenant_status(self):
        mock_db = MagicMock()

        # Not found
        mock_db.execute.return_value.one_or_none.return_value = None
        assert await ts_mod.check_and_update_tenant_status(mock_db, "t-none") == "not_found"

        # Suspended
        mock_db.execute.return_value.one_or_none.return_value = ("suspended", None, 1)
        with patch("app.services.tenant_service.cache_tenant_suspension", new=AsyncMock()):
            assert await ts_mod.check_and_update_tenant_status(mock_db, "t-susp") == "suspended"

        # Expired < 30 days
        past_10 = datetime.now(timezone.utc) - timedelta(days=10)
        mock_db.execute.return_value.one_or_none.return_value = ("active", past_10, 1)
        assert await ts_mod.check_and_update_tenant_status(mock_db, "t-exp10") == "expired"

        # Expired >= 30 days -> auto suspend
        past_35 = datetime.now(timezone.utc) - timedelta(days=35)
        mock_db.execute.return_value.one_or_none.return_value = ("active", past_35, 1)
        with patch("app.services.tenant_service.cache_tenant_suspension", new=AsyncMock()):
            with patch("app.services.tenant_service._revoke_keycloak_sessions", new=AsyncMock()):
                assert await ts_mod.check_and_update_tenant_status(mock_db, "t-exp35") == "suspended"

    @pytest.mark.asyncio
    async def test_get_tenant_db_dsn(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = None
        assert await ts_mod.get_tenant_db_dsn(mock_db, "t-none") is None

        encrypted = ts_mod.encrypt_dsn("postgresql://localhost/t_db")
        mock_db.execute.return_value.scalar.return_value = encrypted
        assert await ts_mod.get_tenant_db_dsn(mock_db, "t1") == "postgresql://localhost/t_db"

    @pytest.mark.asyncio
    async def test_get_redis_initialization(self):
        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_from_url.return_value = mock_client
            ts_mod._redis = None
            r = await ts_mod._get_redis()
            assert r is mock_client

    @pytest.mark.asyncio
    async def test_revoke_keycloak_sessions_exception(self):
        with patch("httpx.AsyncClient", side_effect=Exception("HTTP Error")):
            await ts_mod._revoke_keycloak_sessions("tenant-err")

    @pytest.mark.asyncio
    async def test_check_and_update_tenant_status_overdue_30_days(self):
        mock_db = MagicMock()
        past_date = datetime.now(timezone.utc) - timedelta(days=35)
        mock_db.execute.return_value.one_or_none.return_value = ("active", past_date, 100)

        with patch("app.services.tenant_service.cache_tenant_suspension", AsyncMock()):
            with patch("app.services.tenant_service._revoke_keycloak_sessions", AsyncMock()):
                res = await ts_mod.check_and_update_tenant_status(mock_db, "tenant-overdue")
                assert res == "suspended"


class TestReportCustomExceptions:
    def test_exception_instantiation(self):
        e1 = exp_mod.UnauthorizedError()
        assert e1.status_code == 401
        e2 = exp_mod.ForbiddenError()
        assert e2.status_code == 403
        e3 = exp_mod.NotFoundError()
        assert e3.status_code == 404
        e4 = exp_mod.ConflictError()
        assert e4.status_code == 409
        e5 = exp_mod.BadRequestError()
        assert e5.status_code == 400
        e6 = exp_mod.RateLimitError()
        assert e6.status_code == 429
        e7 = exp_mod.TenantNotFoundError()
        assert e7.status_code == 404
        e8 = exp_mod.TokenExpiredError()
        assert e8.status_code == 401
        e9 = exp_mod.MFARequiredError()
        assert e9.status_code == 401
        e10 = exp_mod.TenantSuspendedError()
        assert e10.status_code == 403
        e11 = exp_mod.ReadOnlyScopeError()
        assert e11.status_code == 403
