"""Shared isolated database fixtures for auth-service tests."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import User, RefreshToken, PasswordResetToken, Tenant, GlobalAuditLog, SuperAdmin


@pytest.fixture(autouse=True)
def sqlite_database(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    import app.core.database as database
    import app.db.master as master
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_SessionLocal", factory)
    monkeypatch.setattr(master, "MasterSessionLocal", factory)
    yield factory
    engine.dispose()


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Keep route tests isolated from the process-wide slowapi storage."""
    from app.core.limiter import limiter
    limiter.limiter.storage.reset()
    yield
    limiter.limiter.storage.reset()


@pytest.fixture
def db_session(sqlite_database):
    session = sqlite_database()
    try:
        yield session
    finally:
        session.close()
