"""Unit test suite for master-service platform functions, encryption, tenant status checks, and provision helper functions.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import tenant_service as ts_mod
from app.services import provision as prov_mod
from app.exceptions import (
    UnauthorizedError,
    ForbiddenError,
    NotFoundError,
    ConflictError,
    BadRequestError,
    RateLimitError,
)


# ---------------------------------------------------------------------------
# DSN Encryption & Redis Suspension Cache Tests
# ---------------------------------------------------------------------------

class TestMasterTenantEncryptionAndCache:
    def test_encrypt_and_decrypt_dsn(self):
        original_dsn = "postgresql://user:pass@localhost:5432/tenant_db"
        encrypted = ts_mod.encrypt_dsn(original_dsn)
        assert encrypted != original_dsn
        decrypted = ts_mod.decrypt_dsn(encrypted)
        assert decrypted == original_dsn

    @pytest.mark.asyncio
    async def test_is_tenant_suspended_redis_cache_hit_and_miss(self):
        mock_redis = AsyncMock()
        mock_redis.get.side_effect = ["1", "0", None]

        with patch("app.services.tenant_service._get_redis", return_value=mock_redis):
            assert await ts_mod.is_tenant_suspended("tenant-1") is True
            assert await ts_mod.is_tenant_suspended("tenant-2") is False
            assert await ts_mod.is_tenant_suspended("tenant-3") is False

    @pytest.mark.asyncio
    async def test_is_tenant_suspended_redis_exception_fallback(self):
        mock_redis = AsyncMock()
        mock_redis.get.side_effect = Exception("Redis connection error")

        with patch("app.services.tenant_service._get_redis", return_value=mock_redis):
            assert await ts_mod.is_tenant_suspended("tenant-err") is False

    @pytest.mark.asyncio
    async def test_cache_and_remove_tenant_suspension(self):
        mock_redis = AsyncMock()
        with patch("app.services.tenant_service._get_redis", return_value=mock_redis):
            await ts_mod.cache_tenant_suspension("tenant-susp", ttl=3600)
            mock_redis.set.assert_awaited_once_with("suspended_tenant:tenant-susp", "1", ex=3600)

            await ts_mod.remove_tenant_suspension_cache("tenant-susp")
            mock_redis.delete.assert_awaited_once_with("suspended_tenant:tenant-susp")

    @pytest.mark.asyncio
    async def test_cache_and_remove_suspension_redis_exception_handled(self):
        mock_redis = AsyncMock()
        mock_redis.set.side_effect = Exception("Redis error")
        mock_redis.delete.side_effect = Exception("Redis error")

        with patch("app.services.tenant_service._get_redis", return_value=mock_redis):
            await ts_mod.cache_tenant_suspension("tenant-err")
            await ts_mod.remove_tenant_suspension_cache("tenant-err")


# ---------------------------------------------------------------------------
# Tenant Subscription Checks Tests
# ---------------------------------------------------------------------------

class TestTenantSubscriptionChecks:
    @pytest.mark.asyncio
    async def test_check_tenant_subscription_states(self):
        mock_db = MagicMock()

        # Not found
        mock_db.execute.return_value.one_or_none.return_value = None
        assert await ts_mod.check_tenant_subscription(mock_db, "t-none") == "not_found"

        # Suspended
        mock_db.execute.return_value.one_or_none.return_value = ("suspended", None, None)
        assert await ts_mod.check_tenant_subscription(mock_db, "t-susp") == "suspended"

        # Active
        now = datetime.now(timezone.utc)
        sub_end = now + timedelta(days=10)
        mock_db.execute.return_value.one_or_none.return_value = ("active", sub_end, None)
        assert await ts_mod.check_tenant_subscription(mock_db, "t-act") == "active"

        # Past due (within grace period)
        sub_past = now - timedelta(days=2)
        grace_future = now + timedelta(days=5)
        mock_db.execute.return_value.one_or_none.return_value = ("active", sub_past, grace_future)
        assert await ts_mod.check_tenant_subscription(mock_db, "t-past") == "past_due"

        # Expired (after grace period)
        grace_past = now - timedelta(days=1)
        mock_db.execute.return_value.one_or_none.return_value = ("active", sub_past, grace_past)
        assert await ts_mod.check_tenant_subscription(mock_db, "t-exp") == "expired"


# ---------------------------------------------------------------------------
# Provision Helper Functions Tests
# ---------------------------------------------------------------------------

class TestProvisionHelpers:
    def test_build_tenant_dsn(self):
        dsn = prov_mod._build_tenant_dsn("tenant-xyz")
        assert "tenant-xyz" in dsn or "postgresql" in dsn

    def test_provision_tenant_structure(self):
        with patch("app.services.provision._create_database", return_value="postgresql://localhost/tenant_db"):
            with patch("app.services.provision._run_alembic_migrations"):
                with patch("app.services.provision._update_tenant_record"):
                    dsn = prov_mod.provision_tenant_database_sync("t1", "Hosp 1")
                    assert dsn == "postgresql://localhost/tenant_db"


# ---------------------------------------------------------------------------
# Custom Exception Hierarchies Tests
# ---------------------------------------------------------------------------

class TestMasterCustomExceptions:
    def test_exception_instantiation(self):
        e1 = UnauthorizedError("Not auth")
        assert e1.status_code == 401

        e2 = ForbiddenError("Forbidden")
        assert e2.status_code == 403

        e3 = NotFoundError("Not found")
        assert e3.status_code == 404

        e4 = ConflictError("Conflict")
        assert e4.status_code == 409

        e5 = BadRequestError("Bad req")
        assert e5.status_code == 400

        e6 = RateLimitError("Rate limit")
        assert e6.status_code == 429
