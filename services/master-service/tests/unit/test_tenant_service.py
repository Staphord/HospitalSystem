"""Unit tests for tenant_service.py in master-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from datetime import datetime, timezone, timedelta

from app.services import tenant_service as ts_mod
from app.services.tenant_service import (
    encrypt_dsn,
    decrypt_dsn,
    check_tenant_subscription,
    check_and_update_tenant_status,
    is_tenant_suspended,
    cache_tenant_suspension,
    remove_tenant_suspension_cache,
    _revoke_keycloak_sessions,
    get_tenant_db_dsn,
)

def test_tenant_dsn_round_trip() -> None:
    dsn = "postgresql://tenant_user:secret@db.example/tenant_a"
    encrypted = encrypt_dsn(dsn)
    assert encrypted != dsn
    assert decrypt_dsn(encrypted) == dsn

class TestTenantServiceOps:
    def test_dsn_encryption_decryption(self):
        original = "postgresql://user:pass@localhost:5432/db"
        enc = encrypt_dsn(original)
        assert enc != original
        dec = decrypt_dsn(enc)
        assert dec == original

    @pytest.mark.asyncio
    async def test_redis_suspension_cache(self):
        ts_mod._redis = None
        mock_r = AsyncMock()
        mock_r.get.return_value = "1"
        with patch.object(ts_mod, "_get_redis", AsyncMock(return_value=mock_r)):
            assert await is_tenant_suspended("t1") is True
            await cache_tenant_suspension("t1")
            assert mock_r.set.called
            await remove_tenant_suspension_cache("t1")
            assert mock_r.delete.called

            mock_r.get.return_value = "0"
            assert await is_tenant_suspended("t1") is False

            mock_r.get.return_value = None
            assert await is_tenant_suspended("t1") is False

        with patch.object(ts_mod, "_get_redis", side_effect=Exception("Redis Error")):
            assert await is_tenant_suspended("t1") is False

    @pytest.mark.asyncio
    async def test_check_tenant_subscription_and_update_status(self):
        db = MagicMock()
        
        # Not found
        db.execute.return_value.one_or_none.return_value = None
        assert await check_tenant_subscription(db, "t1") == "not_found"
        assert await check_and_update_tenant_status(db, "t1") == "not_found"

        # Suspended
        db.execute.return_value.one_or_none.return_value = ("suspended", None, None)
        assert await check_tenant_subscription(db, "t1") == "suspended"

        db.execute.return_value.one_or_none.return_value = ("suspended", None, None, 1, "suspended")
        with patch("app.services.tenant_service.cache_tenant_suspension", AsyncMock()):
            assert await check_and_update_tenant_status(db, "t1") == "suspended"

        # Active normal
        now = datetime.now(timezone.utc)
        future = now + timedelta(days=30)
        db.execute.return_value.one_or_none.return_value = ("active", future, None)
        assert await check_tenant_subscription(db, "t1") == "active"

        # Past due
        past = now - timedelta(days=2)
        grace = now + timedelta(days=5)
        db.execute.return_value.one_or_none.return_value = ("active", past, grace)
        assert await check_tenant_subscription(db, "t1") == "past_due"

        db.execute.return_value.one_or_none.return_value = ("active", past, grace, 1, "active")
        assert await check_and_update_tenant_status(db, "t1") == "past_due"

        # Expired / auto suspend
        grace_past = now - timedelta(days=1)
        db.execute.return_value.one_or_none.return_value = ("active", past, grace_past)
        assert await check_tenant_subscription(db, "t1") == "expired"

        db.execute.return_value.one_or_none.return_value = ("active", past, grace_past, 1, "active")
        with patch("app.services.tenant_service.cache_tenant_suspension", AsyncMock()):
            with patch("app.services.tenant_service._revoke_keycloak_sessions", AsyncMock()):
                assert await check_and_update_tenant_status(db, "t1") == "suspended"

    @pytest.mark.asyncio
    async def test_revoke_keycloak_sessions_and_get_dsn(self):
        db = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_c = MagicMock()
            mock_client.return_value.__aenter__.return_value = mock_c
            mock_c.post = AsyncMock(return_value=MagicMock(raise_for_status=lambda: None, json=lambda: {"access_token": "tok"}))
            mock_c.get = AsyncMock(return_value=MagicMock(is_success=True, json=lambda: [{"id": "u1"}]))
            mock_c.put = AsyncMock()

            await _revoke_keycloak_sessions("t1")

        enc_dsn = encrypt_dsn("postgresql://user:pass@localhost:5432/t1")
        db.execute.return_value.scalar.return_value = enc_dsn
        assert await get_tenant_db_dsn(db, "t1") == "postgresql://user:pass@localhost:5432/t1"

    @pytest.mark.asyncio
    async def test_tenant_service_additional_branch_coverage(self):
        db = MagicMock()

        # Redis exception in cache and remove suspension
        with patch.object(ts_mod, "_get_redis", side_effect=Exception("Redis Cache Error")):
            await cache_tenant_suspension("t1")
            await remove_tenant_suspension_cache("t1")

        # Active tenant not expired in check_and_update_tenant_status
        now = datetime.now(timezone.utc)
        future = now + timedelta(days=30)
        db.execute.return_value.one_or_none.return_value = ("active", future, None, 1, "active")
        assert await check_and_update_tenant_status(db, "t1") == "active"

        # Revoke sessions exception and fallback realm handling
        mock_master_db = MagicMock()
        mock_master_db.execute.return_value.first.return_value = None
        with patch("app.db.master.MasterSessionLocal", return_value=mock_master_db), \
             patch("httpx.AsyncClient", side_effect=Exception("HTTP error")):
            await _revoke_keycloak_sessions("t1")
