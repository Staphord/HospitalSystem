"""Unit tests for provision.py in master-service.
"""
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services import provision
from app.services.provision import (
    provision_tenant_database_sync,
    provision_tenant_database,
    get_tenant_db_session,
    drop_tenant_database,
    _run_alembic_migrations,
    _update_tenant_record,
    _tenant_engine_cache,
)
from app.exceptions import TenantNotFoundError

class TestProvisionService:
    def test_run_alembic_migrations_success_and_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="Done")
            _run_alembic_migrations("t1", "tenant_t1")
            assert mock_run.called

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "alembic", stderr="Error")):
            with pytest.raises(RuntimeError) as exc:
                _run_alembic_migrations("t1", "tenant_t1")
            assert "Tenant migration failed" in str(exc.value)

    def test_update_tenant_record(self):
        mock_db = MagicMock()
        with patch("app.services.provision.get_master_db", return_value=mock_db):
            mock_db.query.return_value.filter.return_value.first.return_value = None
            _update_tenant_record("t1", "postgresql://dsn")

            mock_t = MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = mock_t
            _update_tenant_record("t1", "postgresql://dsn")
            assert mock_t.status == "active"

            mock_db.query.return_value.filter.side_effect = Exception("DB error")
            with pytest.raises(Exception):
                _update_tenant_record("t1", "postgresql://dsn")

    @pytest.mark.asyncio
    async def test_provision_sync_and_async(self):
        with patch("app.services.provision._create_database", return_value="dsn"):
            with patch("app.services.provision._run_alembic_migrations"):
                with patch("app.services.provision._update_tenant_record"):
                    assert provision_tenant_database_sync("t1", "HOSP1") == "dsn"
                    assert await provision_tenant_database("t1", "HOSP1") == "dsn"

        with patch("app.services.provision._create_database", side_effect=Exception("Creation failed")):
            with patch("app.services.provision.drop_tenant_database"):
                with pytest.raises(Exception):
                    provision_tenant_database_sync("t1", "HOSP1")
                with pytest.raises(Exception):
                    await provision_tenant_database("t1", "HOSP1")

    def test_get_tenant_db_session_and_cache(self):
        _tenant_engine_cache.clear()

        mock_session_class = MagicMock()
        _tenant_engine_cache["cached_t"] = (MagicMock(), mock_session_class)
        get_tenant_db_session("cached_t")
        assert mock_session_class.called

        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = None
        with patch("app.services.provision.get_master_db", return_value=mock_db):
            with patch("time.sleep"):
                with pytest.raises(TenantNotFoundError):
                    get_tenant_db_session("non_existent")

        from app.services.tenant_service import encrypt_dsn
        enc_dsn = encrypt_dsn("sqlite:///:memory:")
        mock_db.execute.return_value.scalar.return_value = enc_dsn
        with patch("app.services.provision.get_master_db", return_value=mock_db):
            with patch("app.services.provision.create_engine") as mock_engine:
                with patch("app.services.provision.sessionmaker") as mock_maker:
                    get_tenant_db_session("real_t")
                    assert mock_maker.called

    def test_drop_tenant_database(self):
        mock_conn = MagicMock()
        mock_admin = MagicMock()
        mock_admin.connect.return_value.__enter__.return_value = mock_conn
        with patch("app.services.provision._get_admin_engine", return_value=mock_admin):
            drop_tenant_database("t1")
            assert mock_conn.execute.called

    def test_provision_helpers_and_database_update_paths(self, monkeypatch):
        assert provision._build_tenant_dsn("tenant-1") == provision.settings.tenant_db_template.format(tenant_id="tenant-1")
        engine = MagicMock()
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock()
        result = MagicMock()
        result.scalar.return_value = None
        conn.execute.return_value = result
        engine.connect.return_value = conn
        monkeypatch.setattr(provision, "_get_admin_engine", lambda: engine)
        dsn = provision._create_database("tenant-1")
        assert dsn.endswith("tenant-1")
        result.scalar.return_value = 1
        assert provision._create_database("tenant-1") == dsn

        monkeypatch.setattr(provision, "_run_alembic_migrations", MagicMock())
        monkeypatch.setattr(provision, "_create_database", MagicMock(return_value="dsn"))
        monkeypatch.setattr(provision, "_update_tenant_record", MagicMock())
        assert provision.provision_tenant_database_sync("t", "name") == "dsn"

        monkeypatch.setattr(provision, "_create_database", MagicMock(side_effect=RuntimeError("failed")))
        monkeypatch.setattr(provision, "drop_tenant_database", MagicMock())
        with pytest.raises(Exception):
            provision.provision_tenant_database_sync("t", "name")

    def test_provision_migrations_and_drop_database(self, monkeypatch):
        completed = MagicMock(stdout="done")
        monkeypatch.setattr(provision.subprocess, "run", MagicMock(return_value=completed))
        provision._run_alembic_migrations("t", "tenant_t")

        monkeypatch.setattr(provision.subprocess, "run", MagicMock(side_effect=provision.subprocess.CalledProcessError(1, "alembic", output="bad")))
        with pytest.raises(Exception):
            provision._run_alembic_migrations("t", "tenant_t")

        engine = MagicMock()
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock()
        conn.execute.side_effect = [RuntimeError("terminate"), RuntimeError("drop")]
        engine.connect.return_value = conn
        monkeypatch.setattr(provision, "_get_admin_engine", lambda: engine)
        provision.drop_tenant_database("t")

    @pytest.mark.asyncio
    async def test_provision_rollback_and_placeholder_retries(self):
        # Admin engine creation test
        with patch("app.services.provision.create_engine") as mock_eng:
            provision._get_admin_engine()
            assert mock_eng.called

        # Rollback exception handling in provision_tenant_database_sync & async
        with patch("app.services.provision._create_database", side_effect=Exception("DB Create Fail")):
            with patch("app.services.provision.drop_tenant_database", side_effect=Exception("Rollback Drop Fail")):
                with pytest.raises(Exception, match="DB Create Fail"):
                    provision_tenant_database_sync("t_err", "Hospital Err")
                with pytest.raises(Exception, match="DB Create Fail"):
                    await provision_tenant_database("t_err", "Hospital Err")

        # Placeholder DSN retry handling in get_tenant_db_session
        from app.services.tenant_service import encrypt_dsn
        _tenant_engine_cache.pop("t_placeholder", None)
        placeholder_enc = encrypt_dsn("postgresql://placeholder:5432/db")
        real_enc = encrypt_dsn("postgresql://localhost:5432/db")

        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.side_effect = [placeholder_enc, real_enc]

        with patch("app.services.provision.get_master_db", return_value=mock_db), \
             patch("time.sleep"), \
             patch("app.services.provision.create_engine"), \
             patch("app.services.provision.sessionmaker"):
            get_tenant_db_session("t_placeholder")
