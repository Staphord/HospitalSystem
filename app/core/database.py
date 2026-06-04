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


def init_db() -> None:
    from app.db.base import Base
    import app.models

    _init_engine()
    Base.metadata.create_all(bind=_engine)


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
