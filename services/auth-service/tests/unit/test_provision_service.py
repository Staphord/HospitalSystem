from unittest.mock import MagicMock, patch

import pytest

from app.models.master import Tenant
from app.services import provision


class Conn:
    def __init__(self, exists=False):
        self.exists = exists
        self.executed = []
    def execute(self, statement, params=None):
        self.executed.append((statement, params))
        result = MagicMock()
        result.scalar.return_value = 1 if self.exists else None
        return result
    def __enter__(self): return self
    def __exit__(self, *args): pass


def test_create_database_and_migration(monkeypatch):
    conn = Conn(False); engine = MagicMock(); engine.connect.return_value = conn
    monkeypatch.setattr(provision, "_get_admin_engine", lambda: engine)
    monkeypatch.setattr(provision, "_build_tenant_dsn", lambda t: f"postgresql:///{t}")
    assert provision._create_database("t1") == "postgresql:///t1"
    monkeypatch.setattr(provision.subprocess, "run", MagicMock(return_value=MagicMock(stdout="", stderr="")))
    provision._run_alembic_migrations("t1", "tenant_t1")
    failure = provision.subprocess.CalledProcessError(1, "alembic")
    failure.stderr = "bad"
    failure.stdout = "out"
    monkeypatch.setattr(provision.subprocess, "run", MagicMock(side_effect=failure))
    with pytest.raises(RuntimeError): provision._run_alembic_migrations("t1", "tenant_t1")


def test_update_record_and_orchestration(monkeypatch, db_session):
    tenant = Tenant(tenant_id="t1", hospital_name="Hospital", db_connection_string="old", status="trial")
    db_session.add(tenant); db_session.commit()
    monkeypatch.setattr(provision, "get_session_local", lambda: lambda: db_session)
    monkeypatch.setattr(provision, "encrypt_dsn", lambda dsn: "encrypted")
    provision._update_tenant_record("t1", "postgresql://db")
    assert db_session.query(Tenant).filter(Tenant.tenant_id == "t1").first().db_connection_string == "encrypted"

    monkeypatch.setattr(provision, "_create_database", lambda *args: "dsn")
    monkeypatch.setattr(provision, "_run_alembic_migrations", lambda *args: None)
    monkeypatch.setattr(provision, "_update_tenant_record", lambda *args: None)
    assert provision.provision_tenant_database_sync("t1", "Hospital") == "dsn"
    monkeypatch.setattr(provision, "_create_database", MagicMock(side_effect=RuntimeError("failed")))
    with pytest.raises(RuntimeError): provision.provision_tenant_database_sync("t1", "Hospital")


def test_tenant_session_cache_and_not_found(monkeypatch, db_session):
    class Result:
        def scalar(self): return "encrypted"
    db_session.execute = MagicMock(return_value=Result())
    monkeypatch.setattr(provision, "get_session_local", lambda: lambda: db_session)
    monkeypatch.setattr("app.services.tenant_service.decrypt_dsn", lambda value: "postgresql://db")
    engine = MagicMock(); factory = MagicMock(return_value="session")
    monkeypatch.setattr(provision, "create_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(provision, "sessionmaker", lambda *args, **kwargs: factory)
    provision._tenant_engine_cache.clear()
    assert provision.get_tenant_db_session("t1") == "session"
    assert provision.get_tenant_db_session("t1") == "session"


def test_update_tenant_record_missing_and_exception(monkeypatch):
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(provision, "get_session_local", lambda: lambda: mock_db)
    provision._update_tenant_record("nonexistent_tenant", "postgresql://db")
    mock_db.commit.assert_not_called()

    mock_db_err = MagicMock()
    mock_db_err.query.return_value.filter.side_effect = Exception("DB error")
    monkeypatch.setattr(provision, "get_session_local", lambda: lambda: mock_db_err)
    with pytest.raises(Exception, match="DB error"):
        provision._update_tenant_record("t1", "postgresql://db")
    mock_db_err.rollback.assert_called_once()


def test_get_tenant_db_session_placeholder_and_not_found(monkeypatch):
    from app.exceptions import TenantNotFoundError
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar.return_value = None
    monkeypatch.setattr(provision, "get_session_local", lambda: lambda: mock_db)
    monkeypatch.setattr("time.sleep", lambda s: None)
    provision._tenant_engine_cache.clear()
    with pytest.raises(TenantNotFoundError):
        provision.get_tenant_db_session("missing_tenant")

    mock_db_ph = MagicMock()
    mock_db_ph.execute.return_value.scalar.side_effect = ["enc_ph", "enc_valid"]
    monkeypatch.setattr(provision, "get_session_local", lambda: lambda: mock_db_ph)
    monkeypatch.setattr("app.services.tenant_service.decrypt_dsn", MagicMock(side_effect=["postgresql://placeholder@localhost/db", "postgresql://valid@localhost/db", "postgresql://valid@localhost/db"]))
    monkeypatch.setattr(provision, "create_engine", lambda *args, **kwargs: MagicMock())
    monkeypatch.setattr(provision, "sessionmaker", lambda *args, **kwargs: lambda: "valid_session")
    provision._tenant_engine_cache.clear()
    assert provision.get_tenant_db_session("t_ph") == "valid_session"

