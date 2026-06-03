from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class DatabaseRouter(ABC):
    @abstractmethod
    def get_session(self, hospital_id: str) -> Session:
        raise NotImplementedError


class DefaultDatabaseRouter(DatabaseRouter):
    def get_session(self, hospital_id: str) -> Session:
        # Placeholder: route by hospital_id to separate DBs in the future.
        return SessionLocal()


_router = DefaultDatabaseRouter()


@dataclass
class HospitalContext:
    hospital_id: str
    db: Session


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_hospital_context(hospital_id: str) -> HospitalContext:
    db = _router.get_session(hospital_id)
    return HospitalContext(hospital_id=hospital_id, db=db)


def close_hospital_context(context: HospitalContext) -> None:
    context.db.close()
