from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine: Optional[object] = None
_SessionLocal: Optional[sessionmaker] = None


def _init_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        return
    _engine = create_engine(settings.database_url, pool_pre_ping=True)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def get_session_local() -> sessionmaker:
    _init_engine()
    return _SessionLocal


def _migrate_user_table() -> None:
    from sqlalchemy import inspect, text
    from app.db.base import Base

    inspector = inspect(_engine)
    columns = [c["name"] for c in inspector.get_columns("users")]
    with _engine.connect() as conn:
        if "username" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR"))
        if "role" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR"))
        if "full_name" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN full_name VARCHAR"))
        conn.commit()


def _migrate_super_admins_table() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(_engine)
    tables = inspector.get_table_names()
    if "super_admins" not in tables:
        with _engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE super_admins (
                    super_admin_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    username VARCHAR(50) UNIQUE NOT NULL,
                    email VARCHAR(150) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    full_name VARCHAR(200) NOT NULL,
                    role VARCHAR(50) NOT NULL DEFAULT 'super_admin',
                    mfa_secret VARCHAR(100) NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT true,
                    last_login_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT now()
                )
            """))
            conn.commit()


def init_db() -> None:
    from app.db.base import Base
    import app.models

    _init_engine()
    Base.metadata.create_all(bind=_engine)
    _migrate_user_table()
    _migrate_super_admins_table()


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
