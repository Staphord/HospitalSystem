from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_engine: Optional[object] = None
_SessionLocal: Optional[sessionmaker] = None


def _ensure_database_exists() -> None:
    """Ensure the master database exists, auto-create if not."""
    import urllib.parse
    from sqlalchemy.exc import OperationalError
    
    try:
        # Try to connect to see if database exists
        test_engine = create_engine(settings.database_url, pool_pre_ping=True)
        with test_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        test_engine.dispose()
        return  # Database exists, nothing to do
    except OperationalError:
        # Database doesn't exist, create it using admin connection
        pass
    
    # Parse database name from URL
    parsed = urllib.parse.urlparse(settings.database_url)
    db_name = parsed.path.lstrip("/")
    
    if not db_name:
        raise ValueError("Cannot determine database name from DATABASE_URL")
    
    # Create database using admin connection
    admin_engine = create_engine(
        settings.db_admin_url,
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )
    
    with admin_engine.connect() as conn:
        # Check if database exists
        result = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
            {"db_name": db_name},
        )
        if not result.scalar():
            # Create database
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            print(f"[AUTO-CREATE] Database '{db_name}' created successfully")
    
    admin_engine.dispose()


def _init_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        return
    
    # Ensure database exists before creating engine
    _ensure_database_exists()
    
    _engine = create_engine(settings.database_url, pool_pre_ping=True)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def get_session_local() -> sessionmaker:
    _init_engine()
    return _SessionLocal


def init_db() -> None:
    """Initialize the SQLAlchemy engine; Alembic owns master schema changes."""
    _init_engine()


class DatabaseRouter(ABC):
    @abstractmethod
    def get_session(self, hospital_id: str) -> Session:
        raise NotImplementedError


class DefaultDatabaseRouter(DatabaseRouter):
    def get_session(self, hospital_id: str) -> Session:
        return get_session_local()()


_router = DefaultDatabaseRouter()


@dataclass
class HospitalContext:
    hospital_id: str
    db: Session


def get_db() -> Generator[Session, None, None]:
    db = get_session_local()()
    try:
        yield db
    finally:
        db.close()


def get_hospital_context(hospital_id: str) -> HospitalContext:
    db = _router.get_session(hospital_id)
    return HospitalContext(hospital_id=hospital_id, db=db)


def close_hospital_context(context: HospitalContext) -> None:
    context.db.close()
